from __future__ import annotations

import importlib.resources
import logging
from pathlib import Path

from .models import AgentDefinition
from .parser import AgentParseError, parse_agent_file, parse_frontmatter, validate_agent_metadata


log = logging.getLogger(__name__)
PROJECT_AGENTS_DIRECTORY = ".cogent/agents"


class AgentLoader:
    def __init__(
        self,
        workspace_root: str | Path,
        *,
        user_root: str | Path = "~/.cogent/agents",
        enable_verification: bool = True,
    ) -> None:
        self._workspace_root = Path(workspace_root).expanduser().resolve()
        self._user_root = Path(user_root).expanduser().resolve()
        self._enable_verification = enable_verification
        self._agents: dict[str, AgentDefinition] = {}

    def _scan(self, root: Path, source: str) -> list[AgentDefinition]:
        if not root.is_dir() or root.is_symlink():
            return []
        definitions: list[AgentDefinition] = []
        for path in sorted(root.iterdir()):
            if not path.is_file() or path.is_symlink() or path.suffix != ".md":
                continue
            try:
                definition = parse_agent_file(path, authorized_root=root)
            except AgentParseError as exc:
                log.warning("skipping invalid Agent definition %s: %s", path, exc)
                continue
            definition.source = source
            definitions.append(definition)
        return definitions

    def _builtins(self) -> list[AgentDefinition]:
        definitions: list[AgentDefinition] = []
        package = importlib.resources.files("ai_agent_platform.cogent.agents.builtins")
        for item in sorted(package.iterdir(), key=lambda value: value.name):
            if not item.name.endswith(".md"):
                continue
            try:
                metadata, body = parse_frontmatter(item.read_text(encoding="utf-8"))
                validate_agent_metadata(metadata, item.name)
                definition = AgentDefinition(
                    agent_type=str(metadata["name"]),
                    when_to_use=str(metadata["description"]),
                    system_prompt=body,
                    tools=[str(value) for value in metadata.get("tools", [])],
                    disallowed_tools=[
                        str(value) for value in metadata.get("disallowedTools", [])
                    ],
                    model=str(metadata.get("model", "inherit")),
                    max_turns=int(metadata.get("maxTurns") or 200),
                    permission_mode=str(metadata.get("permissionMode", "default"))
                    or "default",
                    background=bool(metadata.get("background", False)),
                    isolation=str(metadata.get("isolation", "")),
                )
            except Exception as exc:
                log.warning("skipping invalid built-in Agent %s: %s", item.name, exc)
                continue
            if definition.agent_type == "Verification" and not self._enable_verification:
                continue
            definitions.append(definition)
        return definitions

    def load_all(self) -> dict[str, AgentDefinition]:
        selected: dict[str, AgentDefinition] = {}
        sources = (
            (self._workspace_root / PROJECT_AGENTS_DIRECTORY, "project"),
            (self._user_root, "user"),
        )
        for root, source in sources:
            for definition in self._scan(root, source):
                selected.setdefault(definition.agent_type, definition)
        for definition in self._builtins():
            selected.setdefault(definition.agent_type, definition)
        self._agents = selected
        return dict(selected)

    def get(self, agent_type: str) -> AgentDefinition | None:
        cached = self._agents.get(agent_type)
        if cached is None:
            return None
        if cached.file_path is not None and cached.file_path.exists():
            try:
                reloaded = parse_agent_file(
                    cached.file_path,
                    authorized_root=cached.file_path.parent,
                )
            except AgentParseError as exc:
                log.warning("Agent hot reload failed for %s: %s", agent_type, exc)
            else:
                reloaded.source = cached.source
                self._agents[agent_type] = reloaded
                return reloaded
        return cached

    def list_agents(self) -> list[tuple[str, str]]:
        return [
            (definition.agent_type, definition.when_to_use)
            for definition in self._agents.values()
        ]
