from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from ai_agent_platform.integrations.rag import HashingEmbeddingProvider
from ai_agent_platform.project_memory.extractor import RuleBasedMemoryExtractor
from ai_agent_platform.project_memory.service import ProjectMemoryService
from ai_agent_platform.project_memory.vector import InMemoryMemoryVectorStore
from ai_agent_platform.repositories import (
    InMemoryProjectMemoryRepository,
    InMemoryWorkspaceRepository,
)
from ai_agent_platform.services import WorkspaceService


DEFAULT_CASES_PATH = Path(__file__).with_name("memory_cases.json")


@dataclass(frozen=True)
class MemoryEvalReport:
    candidate_precision: float
    recall_at_6: float
    cross_workspace_leaks: int
    extraction_cases: int
    retrieval_cases: int
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def load_memory_eval_suite(path: Path = DEFAULT_CASES_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def run_memory_eval_suite(suite: dict[str, Any]) -> MemoryEvalReport:
    with TemporaryDirectory() as temp_dir:
        root = Path(temp_dir)
        workspace_service = WorkspaceService(
            store=InMemoryWorkspaceRepository(),
            allowed_roots=(str(root),),
        )
        repository = InMemoryProjectMemoryRepository()
        service = ProjectMemoryService(
            repository=repository,
            workspace_service=workspace_service,
            embedding_provider=HashingEmbeddingProvider(dimensions=64),
            vector_store=InMemoryMemoryVectorStore(),
            extractor=RuleBasedMemoryExtractor(),
            enabled=True,
            default_mode="auto",
            candidate_threshold=0.60,
            auto_threshold=0.85,
            recall_limit=20,
            result_limit=6,
            max_context_chars=3000,
        )
        workspace_ids = {
            str(item["workspace_id"]) for item in suite.get("memories", [])
        }
        for workspace_id in workspace_ids:
            workspace_root = root / workspace_id
            workspace_root.mkdir()
            workspace_service.register(
                workspace_id=workspace_id,
                root_path=str(workspace_root),
            )
            service.ensure_workspace_admin(
                workspace_id=workspace_id,
                actor_user_id="eval_runner",
            )
        memory_ids: dict[str, str] = {}
        memory_workspaces: dict[str, str] = {}
        for fixture in suite.get("memories", []):
            stored = service.create_manual(
                workspace_id=str(fixture["workspace_id"]),
                actor_user_id="eval_runner",
                kind=str(fixture["kind"]),
                title=str(fixture["title"]),
                content=str(fixture["content"]),
                importance=3,
            )
            fixture_id = str(fixture["id"])
            memory_ids[fixture_id] = stored.id
            memory_workspaces[stored.id] = stored.workspace_id

        recalls: list[float] = []
        cross_workspace_leaks = 0
        for case in suite.get("queries", []):
            workspace_id = str(case["workspace_id"])
            retrieved = service.retrieve(
                workspace_id=workspace_id,
                actor_user_id="eval_runner",
                query=str(case["query"]),
            )
            retrieved_ids = {item.memory.id for item in retrieved[:6]}
            expected_ids = {
                memory_ids[str(item)] for item in case.get("expected_ids", [])
            }
            if expected_ids:
                recalls.append(len(retrieved_ids & expected_ids) / len(expected_ids))
            cross_workspace_leaks += sum(
                memory_workspaces[memory_id] != workspace_id
                for memory_id in retrieved_ids
            )

        extractor = RuleBasedMemoryExtractor()
        true_predictions = 0
        predictions = 0
        extraction_cases = list(suite.get("extraction_cases", []))
        for case in extraction_cases:
            result = extractor.extract(
                user_message=str(case["user_message"]),
                assistant_message="",
                source_type="chat",
                verified=False,
            )
            predictions += len(result.candidates)
            if case.get("expected_candidate"):
                true_predictions += len(result.candidates)
        candidate_precision = (
            true_predictions / predictions if predictions else 0.0
        )
        recall_at_6 = sum(recalls) / len(recalls) if recalls else 0.0

    thresholds = suite.get("thresholds", {})
    failures: list[str] = []
    if candidate_precision < float(
        thresholds.get("min_candidate_precision", 0.9)
    ):
        failures.append(
            f"candidate_precision={candidate_precision:.3f} is below threshold"
        )
    if recall_at_6 < float(thresholds.get("min_recall_at_6", 0.85)):
        failures.append(f"recall_at_6={recall_at_6:.3f} is below threshold")
    if cross_workspace_leaks > int(
        thresholds.get("max_cross_workspace_leaks", 0)
    ):
        failures.append(
            f"cross_workspace_leaks={cross_workspace_leaks} exceeds threshold"
        )
    return MemoryEvalReport(
        candidate_precision=candidate_precision,
        recall_at_6=recall_at_6,
        cross_workspace_leaks=cross_workspace_leaks,
        extraction_cases=len(extraction_cases),
        retrieval_cases=len(recalls),
        failures=tuple(failures),
    )


def format_memory_report(report: MemoryEvalReport) -> str:
    status = "PASS" if report.passed else "FAIL"
    lines = [
        f"Project Memory Eval: {status}",
        f"Candidate precision: {report.candidate_precision:.3f}",
        f"Recall@6: {report.recall_at_6:.3f}",
        f"Cross-workspace leaks: {report.cross_workspace_leaks}",
        (
            f"Cases: extraction={report.extraction_cases}; "
            f"retrieval={report.retrieval_cases}"
        ),
    ]
    lines.extend(f"- {failure}" for failure in report.failures)
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run offline project-memory quality gates."
    )
    parser.add_argument("--cases", type=Path, default=DEFAULT_CASES_PATH)
    args = parser.parse_args(argv)
    report = run_memory_eval_suite(load_memory_eval_suite(args.cases))
    print(format_memory_report(report))
    return 0 if report.passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
