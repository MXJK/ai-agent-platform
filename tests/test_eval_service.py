from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.evaluation.faults import ToolFaultController
from ai_agent_platform.evaluation.models import (
    EvalBaseline,
    EvalSuiteMetrics,
    utc_now,
)
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
                    "tool_results": [],
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
    def test_run_completes_and_becomes_the_provider_baseline(self) -> None:
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
        self.assertTrue(detail["is_baseline"])
        self.assertEqual(detail["alerts"], [])
        self.assertEqual(detail["metrics"]["pass_rate"], 1.0)
        self.assertEqual(detail["metrics"]["citation_accuracy"], 1.0)
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
                second = client.post(
                    "/api/v1/evals/runs", json={"provider": "fake"}
                ).json()["run_id"]
                detail = _await_run(client, second)

                listing = client.get("/api/v1/evals/runs").json()
                pinned = client.post(f"/api/v1/evals/runs/{second}/baseline")
                repinned = client.get(f"/api/v1/evals/runs/{second}").json()

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

    def test_baseline_is_per_provider(self) -> None:
        repository = InMemoryEvalRepository()
        metrics = EvalSuiteMetrics(1.0, 0.0, 1.0, 0.0, 1.0, 1.0)
        repository.set_baseline(
            EvalBaseline("fake", "eval_1", metrics, utc_now())
        )

        self.assertEqual(repository.get_baseline("fake").run_id, "eval_1")
        self.assertIsNone(repository.get_baseline("deepseek"))


if __name__ == "__main__":
    unittest.main()
