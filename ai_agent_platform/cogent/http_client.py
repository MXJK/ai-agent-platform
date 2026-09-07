"""HTTP/SSE transport used by the public Cogent terminal entrypoint."""

from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
from typing import Any, AsyncIterator

import httpx

from ai_agent_platform.domain import AgentEvent, QueryCommand, QueryParams, QueryResult
from ai_agent_platform.services import AgentEventEncoder


DEFAULT_API_URL = "http://127.0.0.1:8000/api/v1"


class CogentAPIError(RuntimeError):
    """Safe, actionable failure returned by the shared Cogent service."""


@dataclass(frozen=True)
class RemoteCliContext:
    api_url: str
    session_id: str
    workspace_id: str
    workspace_root: str
    model_summary: str
    credential_summary: str
    registry: dict[str, Any]
    model_preference: dict[str, Any]


class CogentHTTPClient:
    """Thin client over the same API consumed by the browser UI."""

    def __init__(
        self,
        api_url: str | None = None,
        *,
        user_id: str = "cli-user",
        timeout: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.api_url = normalize_api_url(api_url or default_api_url())
        self.user_id = user_id
        self._event_encoder = AgentEventEncoder()
        self._client = httpx.AsyncClient(
            base_url=self.api_url,
            headers={"X-User-ID": user_id},
            timeout=httpx.Timeout(timeout, read=None),
            transport=transport,
        )
        self._cursors: dict[str, int] = {}
        self.context: RemoteCliContext | None = None

    async def close(self) -> None:
        await self._client.aclose()

    async def prepare(
        self,
        *,
        cwd: str | Path,
        workspace_id: str | None = None,
        session_id: str | None = None,
    ) -> RemoteCliContext:
        await self._request("GET", "/health", purpose="connect to Cogent")
        registry = await self._json("GET", "/model-registry")
        preferences = await self._json("GET", "/users/me/preferences")
        workspaces_payload = await self._json("GET", "/workspaces")
        workspaces = [
            item
            for item in workspaces_payload.get("workspaces", [])
            if item.get("available", True)
        ]

        session: dict[str, Any] | None = None
        if session_id:
            session = await self._json("GET", f"/sessions/{session_id}")
        selected = _select_workspace(
            workspaces,
            requested=workspace_id,
            session_workspace=(session or {}).get("workspace_id"),
            preferred=preferences.get("default_workspace_id"),
            cwd=Path(cwd),
        )

        if session is None:
            session = await self._json(
                "POST",
                "/sessions",
                json={"user_id": self.user_id},
                expected={201},
            )
            if session.get("workspace_id") != selected["id"] or session.get(
                "composer_mode"
            ) != "agent":
                session = await self._json(
                    "PATCH",
                    f"/sessions/{session['id']}",
                    json={
                        "configuration": {
                            "workspace_id": selected["id"],
                            "composer_mode": "agent",
                        }
                    },
                )
        elif workspace_id and session.get("workspace_id") not in {
            None,
            selected["id"],
        }:
            raise CogentAPIError(
                f"Session {session['id']} belongs to Workspace "
                f"{session['workspace_id']!r}; omit --session-id or select that Workspace."
            )
        elif session.get("workspace_id") is None or session.get("composer_mode") != "agent":
            session = await self._json(
                "PATCH",
                f"/sessions/{session['id']}",
                json={
                    "configuration": {
                        "workspace_id": selected["id"],
                        "composer_mode": "agent",
                    }
                },
            )

        preference = await self._json(
            "GET",
            f"/sessions/{session['id']}/model-preference",
        )
        self.context = _remote_context(
            api_url=self.api_url,
            session_id=str(session["id"]),
            workspace_id=str(selected["id"]),
            workspace_root=str(selected["root_path"]),
            registry=registry,
            preference=preference,
        )
        return self.context

    async def composer_capabilities(self) -> dict[str, Any]:
        context = self._require_context()
        return await self._json(
            "GET",
            "/agent/composer-capabilities",
            params={
                "conversation_id": context.session_id,
                "workspace_id": context.workspace_id,
            },
        )

    async def list_sessions(self) -> list[dict[str, Any]]:
        payload = await self._json("GET", "/sessions", params={"limit": 100})
        return list(payload.get("sessions") or [])

    async def select_session(self, session_id: str | None = None) -> dict[str, Any]:
        context = self._require_context()
        if session_id is None:
            session = await self._json(
                "POST", "/sessions", json={"user_id": self.user_id}, expected={201}
            )
        else:
            session = await self._json("GET", f"/sessions/{session_id}")
        assigned = session.get("workspace_id")
        if assigned not in {None, context.workspace_id}:
            raise CogentAPIError(
                f"Session {session['id']} belongs to Workspace {assigned!r}."
            )
        if assigned is None or session.get("composer_mode") != "agent":
            session = await self._json(
                "PATCH",
                f"/sessions/{session['id']}",
                json={"configuration": {
                    "workspace_id": context.workspace_id,
                    "composer_mode": "agent",
                }},
            )
        preference = await self._json(
            "GET",
            f"/sessions/{session['id']}/model-preference",
        )
        registry = await self._json("GET", "/model-registry")
        self.context = _remote_context(
            api_url=context.api_url,
            session_id=str(session["id"]),
            workspace_id=context.workspace_id,
            workspace_root=context.workspace_root,
            registry=registry,
            preference=preference,
        )
        return session

    async def register_model(self, *, provider: str, model: str) -> dict[str, Any]:
        """Register an enabled model through the server-owned model registry."""

        registered = await self._json(
            "POST",
            "/model-registry/models",
            json={
                "provider": provider,
                "model": model,
                "enabled": True,
                "auto_eligible": True,
            },
            expected={201},
        )
        await self.refresh_model_context()
        return registered

    async def select_model(self, selector: str) -> dict[str, Any]:
        """Persist an exact manual model selection for the active session."""

        context = self._require_context()
        model = _resolve_model(context.registry, selector)
        if not model.get("enabled"):
            raise CogentAPIError(
                f"Model {model.get('provider')}/{model.get('model')} is disabled."
            )
        preference = await self._json(
            "PUT",
            f"/sessions/{context.session_id}/model-preference",
            json={
                "mode": "manual",
                "routing_policy": "smart",
                "preferred_model_id": model["id"],
                "fallback_enabled": False,
            },
        )
        await self.refresh_model_context(preference=preference)
        return model

    async def select_auto_model(self, routing_policy: str = "smart") -> dict[str, Any]:
        """Restore server-side automatic routing for the active session."""

        if routing_policy not in {"smart", "quality", "cost", "latency"}:
            raise ValueError("routing policy must be smart, quality, cost, or latency")
        context = self._require_context()
        preference = await self._json(
            "PUT",
            f"/sessions/{context.session_id}/model-preference",
            json={
                "mode": "auto",
                "routing_policy": routing_policy,
                "preferred_model_id": None,
                "fallback_enabled": True,
            },
        )
        await self.refresh_model_context(preference=preference)
        return preference

    async def refresh_model_context(
        self,
        *,
        preference: dict[str, Any] | None = None,
    ) -> RemoteCliContext:
        context = self._require_context()
        registry = await self._json("GET", "/model-registry")
        if preference is None:
            preference = await self._json(
                "GET",
                f"/sessions/{context.session_id}/model-preference",
            )
        self.context = _remote_context(
            api_url=context.api_url,
            session_id=context.session_id,
            workspace_id=context.workspace_id,
            workspace_root=context.workspace_root,
            registry=registry,
            preference=preference,
        )
        return self.context

    async def delete_session(self, session_id: str) -> None:
        await self._request("DELETE", f"/sessions/{session_id}", expected={204})

    async def memory_files(self, scope: str | None = None) -> list[dict[str, Any]]:
        context = self._require_context()
        params = {"workspace_id": context.workspace_id}
        if scope:
            params["scope"] = scope
        payload = await self._json("GET", "/memory/files", params=params)
        return list(payload.get("files") or [])

    async def write_memory(self, payload: dict[str, Any], *, create: bool=False) -> dict[str, Any]:
        return await self._json("POST" if create else "PUT", "/memory/files",
                                json=payload, expected={201} if create else None)

    async def delete_memory(self, payload: dict[str, Any]) -> None:
        await self._request("DELETE", "/memory/files", json=payload, expected={204})

    async def maintain_memory(self, operation: str, *, scope: str | None=None) -> dict[str, Any]:
        context = self._require_context()
        body = {"conversation_id": context.session_id, "operation": operation}
        if scope:
            body["scope"] = scope
        return await self._json(
            "POST",
            "/memory/maintenance",
            json=body,
            expected={202},
        )

    def query(self, params: QueryParams) -> AsyncIterator[AgentEvent]:
        return self._start_and_stream(params)

    async def _start_and_stream(self, params: QueryParams) -> AsyncIterator[AgentEvent]:
        body: dict[str, Any] = {
            "conversation_id": params.conversation_id,
            "message": params.message,
            "workspace_id": params.workspace_id,
            "focus_files": list(params.focus_files),
            "additional_workspace_ids": list(params.additional_workspace_ids),
            "permission_mode": params.permission_mode,
            "sandbox_enabled": params.sandbox_enabled,
            "sandbox_network_enabled": params.sandbox_network_enabled,
            "skill_arguments": list(params.skill_arguments),
        }
        for name in (
            "cwd",
            "provider",
            "model",
            "thinking_level",
            "routing_policy",
            "mode",
            "skill_name",
            "preferred_tool_name",
        ):
            value = getattr(params, name)
            if value is not None:
                body[name] = value
        record = await self._json(
            "POST",
            "/agent/runs",
            json=body,
            expected={202},
        )
        run_id = str(record["run_id"])
        self._cursors[run_id] = 0
        async for event in self.events(run_id):
            yield event

    async def events(
        self,
        run_id: str,
        *,
        cursor: int | None = None,
    ) -> AsyncIterator[AgentEvent]:
        current = self._cursors.get(run_id, 0) if cursor is None else max(0, cursor)
        try:
            async with self._client.stream(
                "GET",
                f"/agent/runs/{run_id}/events/stream",
                params={"cursor": current},
                headers={"Last-Event-ID": str(current)},
            ) as response:
                if response.status_code >= 400:
                    await response.aread()
                    raise self._response_error(response, purpose="stream Run events")
                data_lines: list[str] = []
                async for line in response.aiter_lines():
                    if not line:
                        if data_lines:
                            payload = json.loads("\n".join(data_lines))
                            event = self._event_encoder.decode(payload, run_id=run_id)
                            current = max(current, event.sequence)
                            self._cursors[run_id] = current
                            yield event
                            data_lines.clear()
                        continue
                    if line.startswith("data:"):
                        data_lines.append(line[5:].lstrip())
                if data_lines:
                    payload = json.loads("\n".join(data_lines))
                    event = self._event_encoder.decode(payload, run_id=run_id)
                    self._cursors[run_id] = max(current, event.sequence)
                    yield event
        except httpx.RequestError as exc:
            raise CogentAPIError(
                f"Lost the Cogent event stream at {self.api_url}: {exc.__class__.__name__}."
            ) from exc

    def resume(
        self,
        run_id: str,
        *,
        approved: bool = True,
        message: str = "",
    ) -> AsyncIterator[AgentEvent]:
        return self._resume_and_stream(
            run_id,
            approved=approved,
            message=message,
        )

    async def _resume_and_stream(
        self,
        run_id: str,
        *,
        approved: bool,
        message: str,
    ) -> AsyncIterator[AgentEvent]:
        before = await self._json("GET", f"/agent/runs/{run_id}")
        if before.get("status") == "waiting_approval":
            await self._json(
                "POST",
                f"/agent/runs/{run_id}/resume",
                json={"approved": approved, "feedback": message or None},
                expected={202},
            )
        else:
            await self._json(
                "POST",
                f"/agent/runs/{run_id}/continue",
                json={"message": message},
            )
        async for event in self.events(run_id):
            yield event

    async def control(
        self,
        run_id: str,
        command: QueryCommand | str,
        *,
        message: str = "",
    ) -> QueryResult:
        action = QueryCommand(command).value
        if action not in {"pause", "cancel", "steer", "compact", "continue"}:
            raise ValueError(f"unsupported remote control action: {action}")
        body = {"instruction": message} if action == "compact" else {"message": message}
        await self._json(
            "POST",
            f"/agent/runs/{run_id}/{action}",
            json=body,
            expected={202} if action == "compact" else {200},
        )
        return await self.result(run_id)

    async def result(self, run_id: str) -> QueryResult:
        record = await self._json("GET", f"/agent/runs/{run_id}")
        result = record.get("result") or {}
        output = {
            "answer": result.get("answer") or "",
            "error": record.get("error"),
            "checkpoint_id": record.get("checkpoint_id"),
            "pending": record.get("pending_approval"),
        }
        return QueryResult(
            run_id=run_id,
            status=str(record["status"]),
            cursor=self._cursors.get(run_id, 0),
            output=output,
            resumable=str(record["status"])
            in {"waiting_approval", "waiting_input", "paused"},
        )

    def registry_text(self) -> str:
        context = self._require_context()
        connections = context.registry.get("connections") or []
        models = context.registry.get("models") or []
        selected_id = context.model_preference.get("preferred_model_id")
        lines = [f"Current model: {context.model_summary}", "Server model registry:"]
        for connection in connections:
            credential = (
                "configured" if connection.get("credential_configured") else "missing"
            )
            lines.append(
                f"  {connection.get('display_name') or connection.get('provider')} · "
                f"credential {credential} · {connection.get('status', 'unknown')}"
            )
            provider = connection.get("provider")
            for model in models:
                if model.get("provider") == provider:
                    state = "enabled" if model.get("enabled") else "disabled"
                    marker = "*" if model.get("id") == selected_id else " "
                    lines.append(
                        f"  {marker} {model.get('id')} · {provider}/{model.get('model')} · "
                        f"{state} · {model.get('status', 'unknown')}"
                    )
        if not connections:
            lines.append("  No Provider is configured. Add one in the web model settings.")
        lines.extend(
            [
                "Commands:",
                "  /models register <provider> <model>",
                "  /models use <model-id|provider/model>",
                "  /models auto [smart|quality|cost|latency]",
            ]
        )
        return "\n".join(lines)

    def _require_context(self) -> RemoteCliContext:
        if self.context is None:
            raise RuntimeError("Cogent HTTP client is not prepared")
        return self.context

    async def _json(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        response = await self._request(method, path, **kwargs)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CogentAPIError(
                f"Cogent returned an invalid JSON response for {method} {path}."
            ) from exc
        if not isinstance(payload, dict):
            raise CogentAPIError(f"Cogent returned an invalid response for {method} {path}.")
        return payload

    async def _request(
        self,
        method: str,
        path: str,
        *,
        purpose: str | None = None,
        expected: set[int] | None = None,
        **kwargs: Any,
    ) -> httpx.Response:
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.RequestError as exc:
            action = purpose or f"call {method} {path}"
            raise CogentAPIError(
                f"Could not {action} at {self.api_url}. Start it with "
                "`docker compose up -d`, then run `uv run cogent` again."
            ) from exc
        accepted = expected or {200}
        if response.status_code not in accepted:
            raise self._response_error(response, purpose=purpose or f"call {method} {path}")
        return response

    def _response_error(self, response: httpx.Response, *, purpose: str) -> CogentAPIError:
        detail = ""
        try:
            payload = response.json()
            if isinstance(payload, dict):
                detail = str(payload.get("detail") or "")
        except ValueError:
            pass
        suffix = f": {detail}" if detail else ""
        return CogentAPIError(
            f"Could not {purpose} (HTTP {response.status_code}){suffix}"
        )


def default_api_url() -> str:
    configured = os.getenv("COGENT_API_URL")
    if configured:
        return configured
    port = os.getenv("SELF_HOSTED_PORT", "8000").strip() or "8000"
    return f"http://127.0.0.1:{port}/api/v1"


def normalize_api_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    if not normalized:
        raise ValueError("Cogent API URL cannot be empty")
    if not normalized.endswith("/api/v1"):
        normalized += "/api/v1"
    return normalized


def _select_workspace(
    workspaces: list[dict[str, Any]],
    *,
    requested: str | None,
    session_workspace: str | None,
    preferred: str | None,
    cwd: Path,
) -> dict[str, Any]:
    by_id = {str(item.get("id")): item for item in workspaces}
    for candidate, source in (
        (requested, "requested"),
        (session_workspace, "session"),
        (preferred, "web preference"),
    ):
        if candidate:
            if candidate in by_id:
                return by_id[candidate]
            if source == "requested":
                choices = ", ".join(sorted(by_id)) or "none"
                raise CogentAPIError(
                    f"Workspace {candidate!r} is not available on the Cogent server. "
                    f"Available Workspace IDs: {choices}."
                )

    cwd_name = cwd.resolve().name
    matches = [
        item
        for item in workspaces
        if item.get("id") == cwd_name
        or Path(str(item.get("root_path") or "/")).name == cwd_name
    ]
    if len(matches) == 1:
        return matches[0]
    if len(workspaces) == 1:
        return workspaces[0]
    choices = ", ".join(sorted(by_id)) or "none"
    if not workspaces:
        raise CogentAPIError(
            "No available Workspace is registered on the Cogent server. "
            "Add one in the web Workspace settings first."
        )
    raise CogentAPIError(
        "The Cogent server has multiple Workspaces and none matches this directory. "
        f"Run `uv run cogent --workspace-id <id>`; available IDs: {choices}."
    )


def _remote_context(
    *,
    api_url: str,
    session_id: str,
    workspace_id: str,
    workspace_root: str,
    registry: dict[str, Any],
    preference: dict[str, Any],
) -> RemoteCliContext:
    connections = list(registry.get("connections") or [])
    models = list(registry.get("models") or [])
    enabled_models = [item for item in models if item.get("enabled")]
    configured = [item for item in connections if item.get("credential_configured")]
    selected = next(
        (
            item
            for item in models
            if item.get("id") == preference.get("preferred_model_id")
        ),
        None,
    )
    if preference.get("mode") == "manual" and selected is not None:
        model_summary = f"{selected.get('provider')}/{selected.get('model')}"
    else:
        policy = str(preference.get("routing_policy") or "smart")
        model_summary = f"auto/{policy} · {len(enabled_models)} models"
    return RemoteCliContext(
        api_url=api_url,
        session_id=session_id,
        workspace_id=workspace_id,
        workspace_root=workspace_root,
        model_summary=model_summary,
        credential_summary=f"{len(configured)}/{len(connections)} credentials",
        registry=registry,
        model_preference=preference,
    )


def _resolve_model(registry: dict[str, Any], selector: str) -> dict[str, Any]:
    selector = selector.strip()
    if not selector:
        raise ValueError("model selector must not be blank")
    models = list(registry.get("models") or [])
    exact = [
        item
        for item in models
        if selector
        in {
            str(item.get("id") or ""),
            f"{item.get('provider')}/{item.get('model')}",
            f"{item.get('provider')}:{item.get('model')}",
        }
    ]
    if len(exact) == 1:
        return exact[0]
    by_name = [item for item in models if item.get("model") == selector]
    if len(by_name) == 1:
        return by_name[0]
    if len(by_name) > 1:
        choices = ", ".join(
            sorted(f"{item.get('provider')}/{item.get('model')}" for item in by_name)
        )
        raise CogentAPIError(
            f"Model name {selector!r} is ambiguous; use one of: {choices}."
        )
    raise CogentAPIError(
        f"Model {selector!r} is not registered. Run `/models` to list models or "
        "`/models register <provider> <model>` first."
    )


__all__ = [
    "CogentAPIError",
    "CogentHTTPClient",
    "DEFAULT_API_URL",
    "RemoteCliContext",
    "default_api_url",
    "normalize_api_url",
]
