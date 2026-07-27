from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi.testclient import TestClient

from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.rag import RetrievalMetrics, evaluate_retrieval
from ai_agent_platform.main import create_app


DEFAULT_CASES_PATH = Path(__file__).with_name("agent_cases.json")


@dataclass(frozen=True)
class CheckResult:
    name: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    case_type: str
    checks: list[CheckResult]
    retrieved_files: list[str] | None = None
    expected_files: list[str] | None = None

    @property
    def passed(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class EvalReport:
    results: list[CaseResult]
    retrieval_metrics: RetrievalMetrics | None = None

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def total_count(self) -> int:
        return len(self.results)

    @property
    def pass_rate(self) -> float:
        return self.passed_count / self.total_count if self.total_count else 0.0


def load_eval_suite(path: Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_eval_suite(suite: dict[str, Any]) -> EvalReport:
    workspace_id = str(suite.get("workspace_id") or "workspace_main")
    knowledge_base_id = str(suite.get("knowledge_base_id") or "eval_docs")
    fixtures = list(suite.get("fixtures", []))
    with TemporaryDirectory() as temp_dir:
        workspace_root = Path(temp_dir)
        for fixture in fixtures:
            target = workspace_root / fixture["filename"]
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(fixture["content"], encoding="utf-8")
        app = create_app(
            settings=Settings(
                llm_provider="fake",
                embedding_provider="local",
                rag_vector_store="memory",
                workspace_allowed_roots=(str(workspace_root),),
            )
        )
        with TestClient(app) as client:
            client.put(
                f"/api/v1/workspaces/{workspace_id}",
                json={"root_path": str(workspace_root)},
            ).raise_for_status()
            client.post(
                "/api/v1/knowledge-bases",
                json={
                    "id": knowledge_base_id,
                    "name": "Evaluation documents",
                    "description": "Source fixtures used by the offline evaluation suite.",
                    "tags": ["evaluation", "source"],
                },
            ).raise_for_status()
            _ingest_fixtures(
                client=client,
                knowledge_base_id=knowledge_base_id,
                fixtures=fixtures,
            )
            results = [
                _run_case(
                    client=client,
                    workspace_id=workspace_id,
                    knowledge_base_id=knowledge_base_id,
                    case=case,
                )
                for case in suite.get("cases", [])
            ]
    retrieval_results = [result for result in results if result.expected_files]
    retrieval_metrics = evaluate_retrieval(
        rankings=[result.retrieved_files or [] for result in retrieval_results],
        relevant_documents=[
            set(result.expected_files or []) for result in retrieval_results
        ],
        k=5,
    )
    return EvalReport(results=results, retrieval_metrics=retrieval_metrics)


def format_report(report: EvalReport) -> str:
    lines = [
        "Agent Eval Report",
        f"Passed: {report.passed_count}/{report.total_count} ({report.pass_rate:.0%})",
    ]
    if report.retrieval_metrics is not None:
        metrics = report.retrieval_metrics
        lines.append(
            f"Retrieval: Recall@{metrics.k}={metrics.recall_at_k:.3f}; "
            f"MRR={metrics.mean_reciprocal_rank:.3f}; "
            f"cases={metrics.evaluated_cases}"
        )
    for result in report.results:
        status = "PASS" if result.passed else "FAIL"
        lines.append(f"- {status} {result.case_id} [{result.case_type}]")
        for check in result.checks:
            check_status = "ok" if check.passed else "miss"
            lines.append(f"  - {check_status} {check.name}: {check.detail}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run offline Agent eval cases.")
    parser.add_argument(
        "--cases",
        type=Path,
        default=DEFAULT_CASES_PATH,
        help="Path to eval case JSON file.",
    )
    args = parser.parse_args(argv)
    report = run_eval_suite(load_eval_suite(args.cases))
    print(format_report(report))
    return 0 if report.passed else 1


def _ingest_fixtures(
    *,
    client: TestClient,
    knowledge_base_id: str,
    fixtures: list[dict[str, Any]],
) -> None:
    for fixture in fixtures:
        response = client.post(
            f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
            json={
                "filename": fixture["filename"],
                "content": fixture["content"],
            },
        )
        if response.status_code != 201:
            raise RuntimeError(f"failed to ingest {fixture['filename']}: {response.text}")


def _run_case(
    *,
    client: TestClient,
    workspace_id: str,
    knowledge_base_id: str,
    case: dict[str, Any],
) -> CaseResult:
    case_type = str(case.get("type"))
    if case_type == "agent":
        checks, retrieved_files = _run_agent_case(
            client=client,
            workspace_id=workspace_id,
            case=case,
        )
    elif case_type == "search":
        checks, retrieved_files = _run_search_case(
            client=client,
            knowledge_base_id=knowledge_base_id,
            case=case,
        )
    else:
        retrieved_files = []
        checks = [
            CheckResult(
                name="case_type",
                passed=False,
                detail=f"unsupported type {case_type}",
            )
        ]
    return CaseResult(
        case_id=str(case.get("id") or "<missing-id>"),
        case_type=case_type,
        checks=checks,
        retrieved_files=retrieved_files,
        expected_files=[str(item) for item in case.get("expected_files", [])],
    )


def _run_agent_case(
    *,
    client: TestClient,
    workspace_id: str,
    case: dict[str, Any],
) -> tuple[list[CheckResult], list[str]]:
    session_response = client.post("/api/v1/sessions", json={"user_id": "eval_runner"})
    session_response.raise_for_status()
    run_response = client.post(
        "/api/v1/agent/runs",
        json={
            "conversation_id": session_response.json()["id"],
            "workspace_id": workspace_id,
            "message": case["message"],
        },
    )
    run_response.raise_for_status()
    status = _wait_for_run(client, run_response.json()["run_id"])
    result = status.get("result") or {}
    checks = [
        _equals_check("status", status.get("status"), case.get("expected_status")),
        _equals_check("intent", result.get("intent"), case.get("expected_intent")),
        _contains_all_check(
            "tools",
            [item["name"] for item in result.get("tool_calls", [])],
            case.get("expected_tools", []),
        ),
        _contains_any_file_check(
            "retrieval",
            [item.get("path") for item in result.get("context_sources", [])],
            case.get("expected_files", []),
        ),
        _answer_keywords_check(
            result.get("answer", ""),
            case.get("expected_answer_keywords", []),
        ),
    ]
    if case.get("expected_pending_approval") is not None:
        checks.append(
            _equals_check(
                "pending_approval",
                bool(status.get("pending_approval")),
                bool(case.get("expected_pending_approval")),
            )
        )
    retrieved_files = [
        str(item["path"])
        for item in result.get("context_sources", [])
        if item.get("path")
    ]
    return (
        [check for check in checks if check.detail != "skipped"],
        retrieved_files,
    )


def _run_search_case(
    *,
    client: TestClient,
    knowledge_base_id: str,
    case: dict[str, Any],
) -> tuple[list[CheckResult], list[str]]:
    response = client.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/search",
        json={"query": case["query"], "limit": 5, "recall_limit": 12},
    )
    response.raise_for_status()
    results = response.json()["results"]
    filenames = [item.get("filename") for item in results]
    symbols = [
        symbol
        for item in results
        for symbol in item.get("symbols", [])
    ]
    return (
        [
            _contains_any_file_check(
                "retrieval", filenames, case.get("expected_files", [])
            ),
            _contains_all_check("symbols", symbols, case.get("expected_symbols", [])),
        ],
        [str(item) for item in filenames if item],
    )


def _wait_for_run(client: TestClient, run_id: str) -> dict[str, Any]:
    terminal_statuses = {"completed", "failed", "waiting_approval"}
    for _ in range(100):
        response = client.get(f"/api/v1/agent/runs/{run_id}")
        response.raise_for_status()
        body = response.json()
        if body["status"] in terminal_statuses:
            return body
        time.sleep(0.02)
    raise TimeoutError(f"agent run {run_id} did not finish")


def _equals_check(name: str, actual: Any, expected: Any) -> CheckResult:
    if expected is None:
        return CheckResult(name=name, passed=True, detail="skipped")
    return CheckResult(
        name=name,
        passed=actual == expected,
        detail=f"expected={expected!r} actual={actual!r}",
    )


def _contains_all_check(
    name: str,
    actual_values: list[Any],
    expected_values: list[Any],
) -> CheckResult:
    if not expected_values:
        return CheckResult(name=name, passed=True, detail="skipped")
    actual = {str(value) for value in actual_values}
    missing = [value for value in expected_values if str(value) not in actual]
    return CheckResult(
        name=name,
        passed=not missing,
        detail=f"missing={missing} actual={sorted(actual)}",
    )


def _contains_any_file_check(
    name: str,
    actual_files: list[Any],
    expected_files: list[Any],
) -> CheckResult:
    if not expected_files:
        return CheckResult(name=name, passed=True, detail="skipped")
    actual = {str(value) for value in actual_files if value}
    passed = any(str(expected) in actual for expected in expected_files)
    return CheckResult(
        name=name,
        passed=passed,
        detail=f"expected_any={expected_files} actual={sorted(actual)}",
    )


def _answer_keywords_check(answer: str, expected_keywords: list[str]) -> CheckResult:
    if not expected_keywords:
        return CheckResult(name="answer_keywords", passed=True, detail="skipped")
    missing = [
        keyword
        for keyword in expected_keywords
        if keyword.lower() not in answer.lower()
    ]
    return CheckResult(
        name="answer_keywords",
        passed=not missing,
        detail=f"missing={missing}",
    )


if __name__ == "__main__":
    sys.exit(main())
