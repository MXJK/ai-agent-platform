from pathlib import Path
from tempfile import TemporaryDirectory
from dataclasses import replace
from types import SimpleNamespace
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.evaluation.faults import ToolFaultController
from ai_agent_platform.evaluation.models import (
    EVAL_STATUS_COMPLETED,
    SEVERITY_CRITICAL,
    EvalAlert,
    EvalBaseline,
    EvalCaseRecord,
    EvalRunRecord,
    EvalSuiteMetrics,
    utc_now,
)
from ai_agent_platform.evaluation.service import EvalService
from ai_agent_platform.evaluation.suite import load_suite
from ai_agent_platform.evaluation.trajectory import check_constraints
from ai_agent_platform.main import create_app
from ai_agent_platform.repositories import InMemoryEvalRepository


def _app(temp_dir: str, **overrides):
    settings = Settings(
        llm_provider="fake",
        embedding_provider="local",
        rag_vector_store="memory",
        workspace_allowed_roots=(temp_dir,),
        eval_workspace_root=str(Path(temp_dir) / ".agent-evals"),
        **overrides,
    )
    return create_app(settings=settings)


def _await_run(client: TestClient, run_id: str, timeout: float = 120.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        body = client.get(f"/api/v1/evals/runs/{run_id}").json()
        if body["status"] != "running":
            return body
        time.sleep(0.1)
    raise TimeoutError(f"eval run {run_id} did not finish")


class ToolFaultControllerTests(unittest.TestCase):
    def test_fault_only_fires_inside_the_scoped_workspace(self) -> None:
        controller = ToolFaultController()
        controller.arm("repo.read_file", workspace_id="eval_ws")

        self.assertFalse(controller.consume("repo.read_file", "user_ws"))
        self.assertFalse(controller.consume("repo.search_code", "eval_ws"))
        self.assertTrue(controller.consume("repo.read_file", "eval_ws"))
        self.assertFalse(controller.consume("repo.read_file", "eval_ws"))

    def test_a_fault_must_declare_a_workspace(self) -> None:
        with self.assertRaises(ValueError):
            ToolFaultController().arm("repo.read_file", workspace_id="")

    def test_disarming_stops_further_injection(self) -> None:
        controller = ToolFaultController()
        controller.arm("repo.read_file", workspace_id="eval_ws", occurrences=5)
        controller.disarm()

        self.assertFalse(controller.consume("repo.read_file", "eval_ws"))


class ProviderAwareStepCeilingTests(unittest.TestCase):
    def test_a_provider_override_replaces_the_base_ceiling(self) -> None:
        from ai_agent_platform.evaluation.trajectory import RunObservation

        observation = RunObservation.from_run_status(
            "case",
            {
                "status": "completed",
                "result": {
                    "tool_calls": [
                        {
                            "call_id": f"c{index}",
                            "name": "repo.read_file",
                            "arguments": {"path": f"{index}.py"},
                            "source": "planner",
                        }
                        for index in range(20)
                    ],
                    "tool_results": [
                        {
                            "call_id": f"c{index}",
                            "name": "repo.read_file",
                            "ok": True,
                        }
                        for index in range(20)
                    ],
                    "trace": [],
                    "context_sources": [],
                    "answer": "",
                },
            },
        )
        case = {"max_steps": 10, "max_steps_by_provider": {"deepseek": 30}}

        fake = {
            item.name: item.passed
            for item in check_constraints(observation, case, provider="fake")
        }
        deepseek = {
            item.name: item.passed
            for item in check_constraints(observation, case, provider="deepseek")
        }

        self.assertFalse(fake["max_steps"])
        self.assertTrue(deepseek["max_steps"])

    def test_calls_without_results_do_not_consume_the_ceiling(self) -> None:
        from ai_agent_platform.evaluation.trajectory import RunObservation

        observation = RunObservation.from_run_status(
            "case",
            {
                "status": "completed",
                "result": {
                    "tool_calls": [{
                        "call_id": "proposed", "name": "repo.read_file",
                        "arguments": {"path": "a.py"}, "source": "model",
                    }],
                    "tool_results": [], "trace": [],
                    "context_sources": [], "answer": "",
                },
            },
        )

        verdict = {
            item.name: item
            for item in check_constraints(observation, {"max_steps": 0})
        }

        self.assertTrue(verdict["max_steps"].passed)
        self.assertEqual(len(observation.executed_calls), 0)


class EvalSuiteTests(unittest.TestCase):
    def test_default_suite_is_well_formed(self) -> None:
        suite = load_suite()

        self.assertTrue(suite.cases)
        self.assertEqual(len(set(suite.case_ids)), len(suite.cases))
        self.assertTrue(
            any(case.get("fault_injection") for case in suite.cases),
            "the suite must exercise failure recovery",
        )
        self.assertTrue(
            any(case.get("verify_citations") for case in suite.cases),
            "the suite must exercise citation verification",
        )

    def test_a_case_without_a_message_is_rejected(self) -> None:
        import json

        with TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "cases.json"
            path.write_text(
                json.dumps({"cases": [{"id": "broken"}]}),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_suite(path)


class EvalApiTests(unittest.TestCase):
    def test_run_completes_without_implicitly_becoming_baseline(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(
                _app(temp_dir, eval_fault_injection_enabled=True)
            ) as client:
                catalogue = client.get("/api/v1/evals/catalogue").json()
                self.assertTrue(catalogue["fault_injection_enabled"])
                self.assertEqual(
                    [item["provider"] for item in catalogue["providers"]],
                    ["fake"],
                )

                started = client.post(
                    "/api/v1/evals/runs",
                    json={"provider": "fake"},
                )
                self.assertEqual(started.status_code, 202)
                run_id = started.json()["run_id"]
                detail = _await_run(client, run_id)

        self.assertEqual(detail["status"], "completed", detail["error"])
        self.assertEqual(detail["passed_cases"], detail["total_cases"])
        self.assertFalse(detail["is_baseline"])
        self.assertEqual(detail["baseline_run_id"], "")
        self.assertEqual(detail["alerts"], [])
        self.assertEqual(detail["metrics"]["pass_rate"], 1.0)
        self.assertEqual(detail["metrics"]["citation_content_accuracy"], 1.0)
        self.assertEqual(detail["metrics"]["answer_path_grounding_rate"], 1.0)
        self.assertEqual(detail["metrics"]["fully_grounded_case_rate"], 1.0)
        self.assertEqual(detail["metrics"]["failure_recovery_rate"], 1.0)
        self.assertGreater(detail["metrics"]["budget_cap_rate"], 0.0)

    def test_injected_fault_is_observed_as_a_real_failed_call(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(
                _app(temp_dir, eval_fault_injection_enabled=True)
            ) as client:
                run_id = client.post(
                    "/api/v1/evals/runs",
                    json={"provider": "fake"},
                ).json()["run_id"]
                detail = _await_run(client, run_id)

        injected = [
            case for case in detail["cases"] if case["metrics"]["failed_calls"]
        ]
        self.assertTrue(injected, "no case observed a failed tool call")
        for case in injected:
            self.assertEqual(case["metrics"]["failure_recovery"], "recovered")

    def test_failure_recovery_is_not_claimed_when_injection_is_off(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(_app(temp_dir)) as client:
                catalogue = client.get("/api/v1/evals/catalogue").json()
                self.assertFalse(catalogue["fault_injection_enabled"])
                run_id = client.post(
                    "/api/v1/evals/runs",
                    json={"provider": "fake"},
                ).json()["run_id"]
                detail = _await_run(client, run_id)

        # Reporting 1.0 here would claim a capability that was never exercised.
        self.assertIsNone(detail["metrics"]["failure_recovery_rate"])

    def test_second_concurrent_start_is_rejected(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(_app(temp_dir)) as client:
                first = client.post("/api/v1/evals/runs", json={"provider": "fake"})
                second = client.post("/api/v1/evals/runs", json={"provider": "fake"})
                self.assertEqual(first.status_code, 202)
                self.assertEqual(second.status_code, 409)
                _await_run(client, first.json()["run_id"])

    def test_unregistered_provider_is_refused_before_spending_anything(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(_app(temp_dir)) as client:
                response = client.post(
                    "/api/v1/evals/runs",
                    json={"provider": "anthropic"},
                )

        self.assertEqual(response.status_code, 400)
        self.assertIn("no enabled model is registered", response.json()["detail"])

    def test_fixture_workspace_is_removed_after_the_run(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(_app(temp_dir)) as client:
                run_id = client.post(
                    "/api/v1/evals/runs",
                    json={"provider": "fake"},
                ).json()["run_id"]
                _await_run(client, run_id)
                workspaces = client.get("/api/v1/workspaces").json()
            leftovers = list((Path(temp_dir) / ".agent-evals").glob("*"))

        self.assertEqual(leftovers, [])
        names = workspaces if isinstance(workspaces, list) else workspaces.get(
            "workspaces", []
        )
        self.assertFalse([item for item in names if item["id"].startswith("eval_")])

    def test_history_and_baseline_pinning(self) -> None:
        with TemporaryDirectory() as temp_dir:
            with TestClient(_app(temp_dir)) as client:
                first = client.post(
                    "/api/v1/evals/runs", json={"provider": "fake"}
                ).json()["run_id"]
                _await_run(client, first)
                first_pinned = client.post(
                    f"/api/v1/evals/runs/{first}/baseline"
                )
                second = client.post(
                    "/api/v1/evals/runs", json={"provider": "fake"}
                ).json()["run_id"]
                detail = _await_run(client, second)

                listing = client.get("/api/v1/evals/runs").json()
                pinned = client.post(f"/api/v1/evals/runs/{second}/baseline")
                repinned = client.get(f"/api/v1/evals/runs/{second}").json()

        self.assertEqual(first_pinned.status_code, 200)
        self.assertEqual(detail["baseline_run_id"], first)
        self.assertEqual(detail["deltas"]["pass_rate"], 0.0)
        self.assertEqual([item["run_id"] for item in listing["runs"]], [second, first])
        self.assertEqual(pinned.status_code, 200)
        self.assertEqual(repinned["baseline"]["run_id"], second)


class EvalRepositoryTests(unittest.TestCase):
    def test_in_memory_repository_orders_newest_first_and_filters(self) -> None:
        from ai_agent_platform.evaluation.models import EvalRunRecord

        repository = InMemoryEvalRepository()
        for index, provider in enumerate(["fake", "deepseek", "fake"]):
            repository.create_run(
                EvalRunRecord(
                    run_id=f"eval_{index}",
                    suite_id="l1",
                    provider=provider,
                    model="",
                    status="completed",
                    started_at=utc_now(),
                )
            )

        newest = [item.run_id for item in repository.list_runs()]
        only_fake = [
            item.run_id for item in repository.list_runs(provider="fake")
        ]

        self.assertEqual(newest, ["eval_2", "eval_1", "eval_0"])
        self.assertEqual(only_fake, ["eval_2", "eval_0"])

    def test_baseline_key_includes_model_suite_and_evaluator(self) -> None:
        repository = InMemoryEvalRepository()
        metrics = EvalSuiteMetrics(1.0, 0.0, 1.0, 0.0, 1.0, 1.0)
        repository.set_baseline(
            EvalBaseline(
                provider="fake",
                model="fake-v1",
                suite_id="l1-v2",
                evaluator_version="2.0",
                schema_version=2,
                run_id="eval_1",
                metrics=metrics,
                pinned_at=utc_now(),
            )
        )

        self.assertEqual(
            repository.get_baseline("fake", "fake-v1", "l1-v2", "2.0").run_id,
            "eval_1",
        )
        self.assertIsNone(
            repository.get_baseline("fake", "fake-v2", "l1-v2", "2.0")
        )
        self.assertIsNone(
            repository.get_baseline("fake", "fake-v1", "l1-v1", "2.0")
        )
        self.assertIsNone(
            repository.get_baseline("fake", "fake-v1", "l1-v2", "1.0")
        )


class EvalIsolationAndBaselineTests(unittest.TestCase):
    def test_schema_mismatched_baseline_is_not_comparable(self) -> None:
        repository = InMemoryEvalRepository()
        metrics = EvalSuiteMetrics(1.0, 0.0, 1.0, 0.0, None, None)
        repository.set_baseline(
            EvalBaseline(
                provider="fake",
                model="fake-v1",
                suite_id="l1_trajectory_v2",
                evaluator_version="2.0",
                schema_version=1,
                run_id="eval_old_schema",
                metrics=metrics,
                pinned_at=utc_now(),
            )
        )
        service = EvalService(
            repository=repository,
            query_service=None,
            session_service=None,
            workspace_service=None,
            workspace_root="/tmp/evals",
        )

        self.assertIsNone(
            service.get_baseline(
                "fake",
                "fake-v1",
                "l1_trajectory_v2",
                "2.0",
                2,
            )
        )

    def test_critical_run_requires_force_to_pin(self) -> None:
        repository = InMemoryEvalRepository()
        metrics = EvalSuiteMetrics(0.0, 0.0, 1.0, 0.0, None, None)
        record = EvalRunRecord(
            run_id="eval_critical",
            suite_id="l1_trajectory_v2",
            provider="fake",
            model="fake-v1",
            status=EVAL_STATUS_COMPLETED,
            started_at=utc_now(),
            total_cases=1,
            completed_cases=1,
            cases=(EvalCaseRecord("case", False, "failed", "run_1"),),
            metrics=metrics,
            alerts=(
                EvalAlert(
                    kind="case", severity=SEVERITY_CRITICAL,
                    metric="pass_rate", message="failed",
                ),
            ),
        )
        repository.create_run(record)
        service = EvalService(
            repository=repository,
            query_service=None,
            session_service=None,
            workspace_service=None,
            workspace_root="/tmp/evals",
        )

        with self.assertRaisesRegex(ValueError, "force=true"):
            service.pin_baseline(record.run_id)
        baseline = service.pin_baseline(record.run_id, force=True)

        self.assertTrue(baseline.forced)
        self.assertEqual(baseline.key, ("fake", "fake-v1", "l1_trajectory_v2", "2.0"))

    def test_available_models_keeps_multiple_models_per_provider(self) -> None:
        registry = SimpleNamespace(
            list_models=lambda: [
                {"provider": "fake", "model": "fake-v1", "display_name": "V1", "enabled": True},
                {"provider": "fake", "model": "fake-v2", "display_name": "V2", "enabled": True},
            ]
        )
        service = EvalService(
            repository=InMemoryEvalRepository(), query_service=None,
            session_service=None, workspace_service=None,
            workspace_root="/tmp/evals", model_registry=registry,
        )

        self.assertEqual(
            [(item["provider"], item["model"]) for item in service.available_providers()],
            [("fake", "fake-v1"), ("fake", "fake-v2")],
        )

    def test_eval_submission_is_explicitly_isolated_and_session_is_deleted(self) -> None:
        class QuerySpy:
            def __init__(self) -> None:
                self.submissions = []

            def submit_run(self, **kwargs):
                self.submissions.append(kwargs)
                return SimpleNamespace(run_id="agent_eval_1")

            def get_run(self, run_id):
                return {
                    "run_id": run_id,
                    "status": "completed",
                    "pending_approval": None,
                    "errors": [],
                    "result": {
                        "tool_calls": [], "tool_results": [], "trace": [],
                        "context_sources": [], "answer": "done", "errors": [],
                        "metrics": {"total_tokens": 7, "elapsed_ms": 3},
                    },
                }

        class SessionSpy:
            def __init__(self) -> None:
                self.deleted = []

            def create_session(self, user_id):
                return SimpleNamespace(id="eval_session", user_id=user_id)

            def delete_session(self, session_id):
                self.deleted.append(session_id)
                return True

        class WorkspaceSpy:
            def __init__(self) -> None:
                self.removed = []
                self.purged = []

            def register(self, **kwargs):
                return kwargs

            def remove(self, workspace_id):
                self.removed.append(workspace_id)

            def purge_ephemeral(self, workspace_id):
                self.purged.append(workspace_id)
                return True

        class MemorySpy:
            def __init__(self) -> None:
                self.admins = []
                self.deleted = []

            def ensure_workspace_admin(self, **kwargs):
                self.admins.append(kwargs)

            def delete_workspace_state(self, **kwargs):
                self.deleted.append(kwargs["workspace_id"])

        suite = replace(
            load_suite(),
            fixtures=({"filename": "README.md", "content": "fixture\n"},),
            cases=({
                "id": "isolated", "message": "inspect", "expected_status": "completed",
            },),
        )
        query = QuerySpy()
        sessions = SessionSpy()
        workspaces = WorkspaceSpy()
        memory = MemorySpy()
        with TemporaryDirectory() as temp_dir:
            service = EvalService(
                repository=InMemoryEvalRepository(), query_service=query,
                session_service=sessions, workspace_service=workspaces,
                memory_service=memory,
                workspace_root=temp_dir, actor_user_id="real_owner", suite=suite,
                poll_interval_seconds=0, status_serializer=lambda value: value,
            )
            detail = service.start_run(
                provider="fake", model="registered-fake-v2", blocking=True,
            )

        submission = query.submissions[0]
        self.assertTrue(submission["evaluation"])
        self.assertEqual(submission["provider"], "fake")
        self.assertEqual(submission["model"], "registered-fake-v2")
        self.assertNotEqual(submission["actor_user_id"], "real_owner")
        self.assertTrue(submission["actor_user_id"].startswith("eval-principal:"))
        self.assertEqual(memory.admins[0]["actor_user_id"], submission["actor_user_id"])
        self.assertEqual(sessions.deleted, ["eval_session"])
        workspace_id = f"eval_{detail.run_id}"
        self.assertEqual(memory.deleted, [workspace_id])
        self.assertEqual(workspaces.purged, [workspace_id])
        self.assertEqual(workspaces.removed, [])


if __name__ == "__main__":
    unittest.main()
