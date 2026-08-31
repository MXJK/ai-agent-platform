from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from ai_agent_platform.agents import CodingAgentRuntime, GameAgentRuntime
from ai_agent_platform.agents.coding import (
    AgentChangeSummary,
    AgentRunMetrics,
    AgentRunRecord,
    AgentRunResult,
    InMemoryAgentRunStore,
)
from ai_agent_platform.agents.coding.models import AgentCheckpoint
from ai_agent_platform.api.routes.agent_runs import create_agent_runs_router
from ai_agent_platform.core import Settings
from ai_agent_platform.domain import (
    QueryCommand,
    QueryLifecycle,
    QueryParams,
    QueryStateError,
)
from ai_agent_platform.integrations.tools import ToolRegistry
from ai_agent_platform.repositories import (
    InMemoryQueryUnitOfWork,
    InMemorySessionRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.schemas import AgentRunStatusResponse
from ai_agent_platform.services import (
    AgentRunService,
    ExecutionContextFactory,
    QueryService,
    SessionService,
    WorkspaceService,
)


class _CaptureQueue:
    def __init__(self, on_submit=None) -> None:
        self.payloads: list[dict[str, object]] = []
        self.names: list[str] = []
        self._on_submit = on_submit

    def submit(self, _name: str, _handler, **payload: object) -> None:
        if self._on_submit is not None:
            self._on_submit(payload)
        self.payloads.append(payload)
        self.names.append(_name)

    def close(self) -> None:
        pass


class QueryContractTests(unittest.TestCase):
    def test_lifecycle_state_machine_covers_suspensions_and_terminals(self) -> None:
        allowed = {
            QueryCommand.RESUME: {"waiting_approval"},
            QueryCommand.CONTINUE: {"waiting_input", "paused"},
            QueryCommand.PAUSE: {"running"},
            QueryCommand.COMPACT: {"running", "paused"},
            QueryCommand.STEER: set(QueryLifecycle.ACTIVE_STATUSES)
            | set(QueryLifecycle.SUSPENDED_STATUSES),
            QueryCommand.CANCEL: set(QueryLifecycle.ACTIVE_STATUSES)
            | set(QueryLifecycle.SUSPENDED_STATUSES),
        }
        for command, statuses in allowed.items():
            for status in QueryLifecycle.ALL_STATUSES:
                if status in statuses:
                    QueryLifecycle.assert_command(command, status)
                else:
                    with self.assertRaises(QueryStateError):
                        QueryLifecycle.assert_command(command, status)

        for status in QueryLifecycle.TERMINAL_STATUSES:
            self.assertFalse(QueryLifecycle.is_resumable(status))
            self.assertIsNotNone(QueryLifecycle.status_event(status))
        for status in QueryLifecycle.SUSPENDED_STATUSES:
            self.assertTrue(QueryLifecycle.is_resumable(status))
            self.assertIsNotNone(QueryLifecycle.status_event(status))

    def test_paused_compact_is_persisted_then_resumed_through_worker_queue(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="inspect app.py",
                    workspace_id="workspace_main",
                )
            )
            paused = replace(
                record,
                status="paused",
                checkpoint_id="checkpoint_paused",
                latest_node="plan_tools",
                next_nodes=["plan_tools"],
            )
            kernel["run_store"].save(paused)
            kernel["queue"].names.clear()
            kernel["queue"].payloads.clear()

            updated = service.execute(
                QueryCommand.COMPACT,
                run_id=record.run_id,
                message="preserve migration state",
            )

            self.assertEqual(updated.status, "running")
            self.assertEqual(updated.control_action, "resume")
            self.assertEqual(
                updated.pending_compaction["instruction"],
                "preserve migration state",
            )
            self.assertEqual(kernel["queue"].names, ["agent_resume"])
            self.assertEqual(
                kernel["queue"].payloads[0]["run_id"], record.run_id
            )

    def test_old_http_api_maps_to_query_params_and_preserves_response_schema(self) -> None:
        record = AgentRunRecord(
            run_id="run_http",
            thread_id="run_http",
            conversation_id="sess_http",
            workspace_id="workspace_main",
            workspace_root="/workspace",
            status="queued",
            checkpoint_id=None,
            latest_node=None,
            next_nodes=["setup_workspace"],
            trace=[],
        )

        class Stub:
            def __init__(self) -> None:
                self.params = None

            def start(self, params):
                self.params = params
                return record

        service = Stub()
        app = FastAPI()
        app.include_router(
            create_agent_runs_router(service, Settings()),  # type: ignore[arg-type]
            prefix="/api/v1",
        )
        with TestClient(app) as client:
            response = client.post(
                "/api/v1/agent/runs",
                json={
                    "conversation_id": "sess_http",
                    "message": "inspect app.py",
                    "workspace_id": "workspace_main",
                    "focus_files": ["app.py"],
                    "model": "demo",
                },
            )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json(),
            AgentRunStatusResponse.from_domain(record).model_dump(),
        )
        self.assertIsInstance(service.params, QueryParams)
        self.assertEqual(service.params.entrypoint, "api")
        self.assertEqual(service.params.focus_files, ("app.py",))
        self.assertEqual(service.params.metadata_dict()["transport"], "http")

    def test_checkpoint_history_and_restore_http_contract(self) -> None:
        record = AgentRunRecord(
            run_id="run_source",
            thread_id="run_source",
            conversation_id="sess_source",
            workspace_id="workspace_main",
            workspace_root="/workspace",
            status="paused",
            checkpoint_id="checkpoint_current",
            latest_node="plan_tools",
            next_nodes=["plan_tools"],
            trace=[],
        )
        branch = replace(
            record,
            run_id="run_branch",
            thread_id="run_branch",
            conversation_id="sess_branch",
            status="queued",
            checkpoint_id="checkpoint_branch",
        )
        checkpoint = AgentCheckpoint(
            checkpoint_id="checkpoint_history",
            parent_checkpoint_id="checkpoint_parent",
            created_at="2026-08-23T12:00:00+00:00",
            step=7,
            source="loop",
            next_nodes=["plan_tools"],
            latest_node="merge_evidence",
            summary="Evidence merged.",
            interrupt=None,
            changed_files=[],
            tool_call_count=3,
            can_restore=True,
            is_current=False,
        )

        class Stub:
            def list_checkpoints_for_actor(self, run_id, actor, *, limit):
                self.list_call = (run_id, actor, limit)
                return record, [checkpoint]

            def restore_checkpoint(self, **kwargs):
                self.restore_call = kwargs
                return branch, SimpleNamespace(id="sess_branch")

        service = Stub()
        app = FastAPI()
        app.include_router(
            create_agent_runs_router(service, Settings()),  # type: ignore[arg-type]
            prefix="/api/v1",
        )
        with TestClient(app) as client:
            history = client.get(
                "/api/v1/agent/runs/run_source/checkpoints?limit=25"
            )
            restored = client.post(
                "/api/v1/agent/runs/run_source/checkpoints/"
                "checkpoint_history/restore",
                json={"mode": "fork", "message": "try another approach"},
            )

        self.assertEqual(history.status_code, 200)
        self.assertEqual(
            history.json()["checkpoints"][0]["checkpoint_id"],
            "checkpoint_history",
        )
        self.assertTrue(history.json()["checkpoints"][0]["can_restore"])
        self.assertEqual(restored.status_code, 202)
        self.assertEqual(restored.json()["run"]["run_id"], "run_branch")
        self.assertEqual(restored.json()["forked_conversation_id"], "sess_branch")
        self.assertEqual(service.restore_call["mode"], "fork")


class QueryServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_recent_runs_are_ordered_and_filtered_by_actor(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            alice_record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="alice run",
                    workspace_id="workspace_main",
                )
            )
            bob_session = kernel["session_service"].create_session("bob")
            bob_record = replace(
                alice_record,
                run_id="run_bob_recent",
                thread_id="run_bob_recent",
                conversation_id=bob_session.id,
            )
            kernel["run_store"].save(bob_record)

            self.assertEqual(
                [record.run_id for record in service.list_runs_for_actor(None, limit=2)],
                ["run_bob_recent", alice_record.run_id],
            )
            self.assertEqual(
                [record.run_id for record in service.list_runs_for_actor("alice", limit=10)],
                [alice_record.run_id],
            )

    async def test_recent_runs_skip_orphans_with_deleted_sessions(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            alice_record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="visible run",
                    workspace_id="workspace_main",
                )
            )
            orphan = replace(
                alice_record,
                run_id="run_orphan_recent",
                thread_id="run_orphan_recent",
                conversation_id="sess_deleted",
            )
            kernel["run_store"].save(orphan)

            self.assertEqual(
                [
                    record.run_id
                    for record in service.list_runs_for_actor("alice", limit=10)
                ],
                [alice_record.run_id],
            )

            app = FastAPI()
            app.include_router(
                create_agent_runs_router(
                    service,
                    Settings(auth_mode="single_user", single_user_id="alice"),
                ),
                prefix="/api/v1",
            )
            with TestClient(app) as client:
                response = client.get("/api/v1/agent/runs?limit=10")

            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                [record["run_id"] for record in response.json()["runs"]],
                [alice_record.run_id],
            )

    async def test_approval_decision_is_appended_before_resume(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="request a write",
                    workspace_id="workspace_main",
                )
            )
            waiting = replace(
                record,
                status="waiting_approval",
                latest_node="review_tool_plan",
                next_nodes=["review_tool_plan"],
                pending_approval={
                    "type": "tool_plan_review",
                    "tool_calls": [
                        {
                            "call_id": "call_write",
                            "name": "sandbox.write_file",
                            "arguments": {"path": "app.py"},
                        }
                    ],
                },
            )
            kernel["run_store"].save(waiting)

            service.resume_run(
                run_id=record.run_id,
                approved=False,
                feedback="不要修改这个文件",
            )
            _, events = service.events_for_actor(record.run_id, None)

            types = [event.type for event in events]
            self.assertLess(
                types.index("approval_decided"),
                types.index("run_resume_requested"),
            )
            decision = next(event for event in events if event.type == "approval_decided")
            self.assertFalse(decision.output_dict()["approved"])
            self.assertEqual(decision.output_dict()["feedback"], "不要修改这个文件")
            self.assertEqual(
                decision.output_dict()["request"]["tool_calls"][0]["call_id"],
                "call_write",
            )

    async def test_eval_flag_skips_user_and_project_memory_side_effects(self) -> None:
        class UserMemorySpy:
            enabled = True

            def __init__(self) -> None:
                self.context_calls = []
                self.capture_calls = []

            def context_for_user(self, *, user_id):
                self.context_calls.append(user_id)
                return "REAL PROFILE"

            def capture_user_message(self, **kwargs):
                self.capture_calls.append(kwargs)

        class ProjectMemorySpy:
            def __init__(self) -> None:
                self.extract_calls = []

            def extract_and_store(self, **kwargs):
                self.extract_calls.append(kwargs)

        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            user_memory = UserMemorySpy()
            project_memory = ProjectMemorySpy()
            queue = _CaptureQueue()
            context_factory = ExecutionContextFactory(
                session_service=kernel["session_service"],
                workspace_service=kernel["workspace_service"],
                auth_mode="disabled",
                entrypoint_type="api",
                tool_registry=kernel["registry"],
                user_memory_service=user_memory,
            )
            service = QueryService(
                runtime=kernel["runtime"],
                session_service=kernel["session_service"],
                workspace_service=kernel["workspace_service"],
                task_queue=queue,
                execution_context_factory=context_factory,
                query_uow=kernel["uow"],
                user_memory_service=user_memory,
                project_memory_service=project_memory,
            )
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="isolated eval",
                    workspace_id="workspace_main",
                    provider="fake",
                    model="registered-fake-v2",
                    evaluation=True,
                )
            )
            result = _result(record, answer="eval answer")
            kernel["run_store"].save(
                replace(record, status="completed", next_nodes=[], result=result)
            )
            service._record_assistant_message(result)

            self.assertEqual(queue.names, ["agent_run"])
            self.assertEqual(user_memory.context_calls, [])
            self.assertEqual(user_memory.capture_calls, [])
            self.assertEqual(project_memory.extract_calls, [])
            self.assertEqual(record.context_snapshot.session.controlled_history, ())
            self.assertEqual(
                record.context_snapshot.metadata.entrypoint_metadata[
                    "evaluation"
                ],
                {"isolated": True, "knowledge_base_ids": []},
            )
            messages = kernel["session_service"].list_messages(kernel["session_id"])
            self.assertEqual([item.role for item in messages], ["user"])

            ordinary = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="ordinary run",
                    workspace_id="workspace_main",
                )
            )

            self.assertIn("user_memory_extraction", queue.names)
            self.assertIn("REAL PROFILE", [
                item.content
                for item in ordinary.context_snapshot.session.controlled_history
            ])

    async def test_atomic_start_rolls_back_message_and_run_together(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            session_service = kernel["session_service"]
            original_add_message = session_service.add_message

            def fail_add_message(*_args, **_kwargs):
                raise RuntimeError("message persistence failed")

            session_service.add_message = fail_add_message
            try:
                with self.assertRaisesRegex(RuntimeError, "message persistence failed"):
                    kernel["service"].start(
                        QueryParams(
                            conversation_id=kernel["session_id"],
                            message="must be atomic",
                            workspace_id="workspace_main",
                        )
                    )
            finally:
                session_service.add_message = original_add_message

            self.assertEqual(kernel["run_store"]._runs, {})
            self.assertEqual(
                session_service.list_messages(kernel["session_id"]),
                [],
            )
            self.assertEqual(kernel["queue"].payloads, [])

    async def test_start_is_atomic_before_dispatch_and_query_is_async_iterable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            observed: list[tuple[AgentRunRecord, list]] = []
            kernel = _kernel(
                Path(temp_dir),
                on_submit=lambda payload: observed.append(
                    (
                        kernel["run_store"].get(str(payload["run_id"])),
                        kernel["session_service"].list_messages(
                            kernel["session_id"]
                        ),
                    )
                ),
            )
            service: QueryService = kernel["service"]
            params = QueryParams(
                conversation_id=kernel["session_id"],
                message="inspect app.py",
                workspace_id="workspace_main",
                focus_files=("app.py",),
                mode="auto",
                entrypoint="sdk",
                entrypoint_metadata={"client": "test-sdk"},
            )

            iterator = service.query(params)
            self.assertEqual(len(observed), 1)
            persisted_run, persisted_messages = observed[0]
            self.assertEqual(persisted_run.status, "queued")
            self.assertEqual(persisted_messages[-1].role, "user")
            self.assertEqual(
                persisted_messages[-1].source_run_id,
                persisted_run.run_id,
            )
            self.assertEqual(
                persisted_run.context_snapshot.metadata.entrypoint_metadata,
                {"client": "test-sdk"},
            )
            self.assertEqual(
                persisted_run.context_snapshot.session.model_selection.mode,
                "auto",
            )
            self.assertEqual(
                persisted_run.context_snapshot.tools.enabled_tools,
                ("demo.lookup",),
            )
            self.assertEqual(
                persisted_run.context_snapshot.metadata.schema_version,
                4,
            )
            self.assertIsNotNone(
                persisted_run.context_snapshot.execution_workspace
            )
            self.assertTrue(
                persisted_run.context_snapshot.tools.catalog_hash.startswith(
                    "sha256:"
                )
            )
            self.assertTrue(
                persisted_run.context_snapshot.tools.pool_hash.startswith(
                    "sha256:"
                )
            )

            event = await anext(iterator)
            self.assertEqual(event.run_id, persisted_run.run_id)
            self.assertEqual(event.type, "run_queued")
            self.assertGreater(event.sequence, 0)
            await iterator.aclose()
            self.assertEqual(service.get_run(event.run_id).status, "queued")

    async def test_cursor_recovery_and_encoder_are_shared(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="inspect",
                    workspace_id="workspace_main",
                )
            )
            store: InMemoryAgentRunStore = kernel["run_store"]
            store.save(
                replace(
                    record,
                    status="running",
                    trace=[
                        {
                            "step": 1,
                            "node": "inspect_repository",
                            "summary": "inspected",
                            "output": {"files": 1},
                        }
                    ],
                )
            )
            result = _result(record, answer="done")
            store.save(
                replace(
                    record,
                    status="completed",
                    latest_node="compose_answer",
                    next_nodes=[],
                    trace=result.trace,
                    result=result,
                )
            )

            _, all_events = service.events_for_actor(record.run_id, None)
            cursor = all_events[-2].sequence
            _, resumed = service.events_for_actor(
                record.run_id,
                None,
                after=cursor,
            )
            self.assertEqual([event.type for event in resumed], ["run_completed"])
            self.assertTrue(all(event.run_id == record.run_id for event in resumed))
            payload = service.event_encoder.to_payload(
                resumed[0],
                include_run_id=False,
            )
            self.assertNotIn("run_id", payload)
            self.assertIn("event: run_completed", service.event_encoder.encode_sse(resumed[0]))
            query_result = service.get_result(record.run_id)
            self.assertEqual(query_result.cursor, resumed[0].sequence)
            self.assertEqual(query_result.output_dict()["answer"], "done")

    async def test_final_message_and_worker_redelivery_are_idempotent(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="finish once",
                    workspace_id="workspace_main",
                )
            )
            result = _result(record, answer="one final answer")
            kernel["run_store"].save(
                replace(
                    record,
                    status="completed",
                    next_nodes=[],
                    result=result,
                )
            )

            service.execute_run_task(run_id=record.run_id, broker_redelivered=True)
            service._record_assistant_message(result)
            restarted = QueryService(
                runtime=kernel["runtime"],
                session_service=kernel["session_service"],
                workspace_service=kernel["workspace_service"],
                task_queue=_CaptureQueue(),
                query_uow=kernel["uow"],
            )
            restarted._record_assistant_message(result)
            restarted.execute_run_task(run_id=record.run_id, broker_redelivered=True)

            messages = kernel["session_service"].list_messages(kernel["session_id"])
            assistants = [message for message in messages if message.role == "assistant"]
            self.assertEqual(len(assistants), 1)
            self.assertEqual(assistants[0].source_run_id, record.run_id)

    async def test_agent_run_service_is_a_compatible_query_service_facade(self) -> None:
        self.assertTrue(issubclass(AgentRunService, QueryService))

    async def test_worker_restores_frozen_pool_after_unrelated_tool_is_registered(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="inspect",
                    workspace_id="workspace_main",
                )
            )
            registry: ToolRegistry = kernel["registry"]
            registry.register("demo.new", lambda: {"new": True})
            runtime = kernel["runtime"]
            runtime.run = Mock(return_value=_result(record, answer="done"))

            service.execute_run_task(run_id=record.run_id)

            runtime.run.assert_called_once()
            restored = runtime.run.call_args.kwargs["run_context"]
            self.assertEqual(restored.tools.enabled_tools, ("demo.lookup",))
            self.assertNotIn("demo.new", restored.tools.enabled_tools)

    async def test_worker_fails_safely_before_model_when_tool_definition_drifts(self) -> None:
        with TemporaryDirectory() as temp_dir:
            kernel = _kernel(Path(temp_dir))
            service: QueryService = kernel["service"]
            record = service.start(
                QueryParams(
                    conversation_id=kernel["session_id"],
                    message="inspect",
                    workspace_id="workspace_main",
                )
            )
            registry: ToolRegistry = kernel["registry"]
            registry.remove_provider("local")
            registry.register(
                "demo.lookup",
                lambda: {"value": 2},
                description="changed definition",
            )
            runtime = kernel["runtime"]
            runtime.run = Mock()

            service.execute_run_task(run_id=record.run_id)

            runtime.run.assert_not_called()
            failed = service.get_run(record.run_id)
            self.assertEqual(failed.status, "failed")
            self.assertEqual(
                failed.error,
                "The frozen effective tool pool could not be restored safely.",
            )
            self.assertIn(
                "tool_pool_restore_failed",
                [
                    event.type
                    for event in service.events_for_actor(
                        record.run_id, None
                    )[1]
                ],
            )


def _kernel(root: Path, on_submit=None) -> dict[str, object]:
    (root / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    session_repository = InMemorySessionRepository()
    session_service = SessionService(
        repository=session_repository,
        agent_runtime=GameAgentRuntime(),
    )
    session = session_service.create_session("alice")
    workspace_service = WorkspaceService(
        store=InMemoryWorkspaceRepository(),
        allowed_roots=(str(root),),
    )
    workspace_service.register(
        workspace_id="workspace_main",
        root_path=str(root),
    )
    registry = ToolRegistry()
    registry.register(
        "demo.lookup",
        lambda: {"value": 1},
        input_schema={"type": "object", "additionalProperties": False},
        output_schema={
            "type": "object",
            "properties": {"value": {"type": "integer"}},
            "required": ["value"],
            "additionalProperties": False,
        },
    )
    run_store = InMemoryAgentRunStore()
    runtime = CodingAgentRuntime(tool_registry=registry, run_store=run_store)
    context_factory = ExecutionContextFactory(
        session_service=session_service,
        workspace_service=workspace_service,
        auth_mode="disabled",
        entrypoint_type="api",
        tool_registry=registry,
        config_snapshot={"agent": {"approval_policy": "on_request"}},
    )
    uow = InMemoryQueryUnitOfWork(
        session_service=session_service,
        session_repository=session_repository,
        run_store=run_store,
    )
    queue = _CaptureQueue(on_submit=on_submit)
    service = QueryService(
        runtime=runtime,
        session_service=session_service,
        workspace_service=workspace_service,
        task_queue=queue,
        execution_context_factory=context_factory,
        query_uow=uow,
    )
    return {
        "service": service,
        "runtime": runtime,
        "run_store": run_store,
        "session_service": session_service,
        "session_id": session.id,
        "workspace_service": workspace_service,
        "uow": uow,
        "queue": queue,
        "registry": registry,
        "context_factory": context_factory,
    }


def _result(record: AgentRunRecord, *, answer: str) -> AgentRunResult:
    trace = [
        {
            "step": 1,
            "node": "inspect_repository",
            "summary": "inspected",
            "output": {"files": 1},
        }
    ]
    return AgentRunResult(
        run_id=record.run_id,
        thread_id=record.thread_id,
        conversation_id=record.conversation_id,
        workspace_id=record.workspace_id,
        status="completed",
        checkpoint_id="checkpoint_done",
        role="coding agent",
        objective="answer",
        intent="repository_question",
        context_route="repo",
        selected_knowledge_base_ids=[],
        answer=answer,
        graph_engine="langgraph",
        context_sources=[],
        tool_calls=[],
        tool_results=[],
        trace=trace,
        metrics=AgentRunMetrics(),
        change_summary=AgentChangeSummary(),
    )


if __name__ == "__main__":
    unittest.main()
