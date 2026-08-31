from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from jsonschema import Draft202012Validator

from ai_agent_platform.agents.coding.change_loop import ChangeLoopExecutor
from ai_agent_platform.agents.coding.evidence_executor import (
    EVIDENCE_CHILD_TOOLS,
    EVIDENCE_TOOL_NAME,
    MAX_EVIDENCE_CONCURRENCY,
    EvidenceExecutor,
    evidence_plan_schema,
    normalize_evidence_plan,
)
from ai_agent_platform.agents.coding.models import CodingAgentState
from ai_agent_platform.agents.coding.run_artifacts import read_run_artifact
from ai_agent_platform.agents.coding.runtime_support import build_run_metrics
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.agents.coding_agent import CodingAgentRuntime
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry
from ai_agent_platform.integrations.tools import ToolCall, ToolRegistry
from ai_agent_platform.token_counting import estimate_text_tokens


def _response(
    call: ToolCall,
    *,
    result: dict[str, object] | None = None,
    error: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "call_id": call.call_id,
        "name": call.name,
        "ok": error is None,
        "provider": "local",
        "permission_level": "read_only",
        "requires_approval": False,
        "duration_ms": 1,
        "output_truncated": False,
        "attempts": 1,
        "cached": False,
    }
    if error is None:
        payload["result"] = result or {}
    else:
        payload["error"] = error
        payload["error_code"] = "fixture_failure"
    return payload


class EvidencePlanTests(unittest.TestCase):
    def test_schema_is_strict_and_illegal_limits_use_safe_defaults(self) -> None:
        schema = evidence_plan_schema()
        errors = list(
            Draft202012Validator(schema).iter_errors(
                {"tools": ["sandbox.run_command"]}
            )
        )
        self.assertTrue(errors)
        self.assertFalse(schema["additionalProperties"])

        plan, deduplicated = normalize_evidence_plan(
            {
                "queries": ["Token usage", " token   usage "],
                "candidate_paths": ["./src/a.py", "src/a.py"],
                "max_files": 0,
                "max_depth": "unbounded",
                "max_results_per_query": 999,
                "max_chars_per_file": -1,
                "max_evidence_tokens": True,
            }
        )
        self.assertEqual(plan.queries, ["Token usage"])
        self.assertEqual(plan.candidate_paths, ["src/a.py"])
        self.assertEqual(plan.max_files, 8)
        self.assertEqual(plan.max_depth, 3)
        self.assertEqual(plan.max_results_per_query, 12)
        self.assertEqual(plan.max_chars_per_file, 8000)
        self.assertEqual(plan.max_evidence_tokens, 12000)
        self.assertEqual(deduplicated, 2)


class EvidenceExecutorTests(unittest.TestCase):
    def test_batch_deduplicates_queries_paths_and_file_content_with_partial_failure(self) -> None:
        batches: list[tuple[list[str], bool]] = []

        def execute(calls: list[ToolCall], parallel: bool) -> list[dict[str, object]]:
            batches.append(([call.name for call in calls], parallel))
            output: list[dict[str, object]] = []
            for call in calls:
                if call.name == "repo.list_files":
                    output.append(
                        _response(
                            call,
                            result={
                                "files": [
                                    "src/a.py",
                                    "src/duplicate.py",
                                    "node_modules/ignored.js",
                                    "deep/one/two/three/four.py",
                                ],
                                "truncated": False,
                            },
                        )
                    )
                elif call.name == "repo.find_files":
                    output.append(
                        _response(
                            call,
                            result={
                                "query": call.arguments["query"],
                                "matches": ["src/a.py"],
                                "truncated": False,
                            },
                        )
                    )
                elif call.name == "repo.search_code":
                    output.append(
                        _response(
                            call,
                            result={
                                "query": call.arguments["query"],
                                "matches": [
                                    {"path": "src/a.py", "line": 1, "text": "TOKEN"}
                                ],
                                "truncated": False,
                            },
                        )
                    )
                elif call.arguments["path"] == "src/missing.py":
                    output.append(_response(call, error="file does not exist"))
                else:
                    content = "TOKEN = 1\n" + "x" * 4000
                    output.append(
                        _response(
                            call,
                            result={
                                "path": call.arguments["path"],
                                "start_line": 1,
                                "end_line": 2,
                                "content": content,
                                "content_hash": "same-content",
                                "truncated": False,
                            },
                        )
                    )
            return output

        executor = EvidenceExecutor(execute)
        bundle, raw_results, artifacts = executor.collect(
            outer_call=ToolCall(
                call_id="collect_one",
                name=EVIDENCE_TOOL_NAME,
                arguments={
                    "queries": ["TOKEN", " token "],
                    "candidate_paths": [
                        "src/a.py",
                        "./src/a.py",
                        "src/duplicate.py",
                        "src/missing.py",
                    ],
                    "max_files": 3,
                    "max_depth": 3,
                    "max_evidence_tokens": 256,
                    "required_evidence": ["TOKEN", "missing-symbol"],
                },
            )
        )

        self.assertEqual(batches[0][0].count("repo.search_code"), 1)
        self.assertEqual(batches[0][0].count("repo.find_files"), 1)
        self.assertTrue(all(parallel for _, parallel in batches))
        self.assertTrue(all(len(names) <= MAX_EVIDENCE_CONCURRENCY for names, _ in batches))
        self.assertTrue(
            all(
                result["arguments"].get("max_depth") == 3
                for result in raw_results[:3]
            )
        )
        self.assertEqual(len(raw_results), 6)
        self.assertEqual(len(artifacts), len(raw_results))
        self.assertEqual(len(bundle["evidence"]), 1)
        self.assertEqual(bundle["coverage"], ["TOKEN"])
        self.assertEqual(bundle["unresolved"], ["missing-symbol"])
        self.assertEqual(len(bundle["errors"]), 1)
        self.assertGreaterEqual(bundle["deduplicated_count"], 4)
        self.assertLessEqual(
            estimate_text_tokens(
                json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            ),
            256,
        )
        self.assertTrue(bundle["truncated"])
        self.assertTrue(all(item.get("evidence_raw_result") for item in artifacts))
        self.assertTrue(all("arguments" in item for item in artifacts))

    def test_side_effect_tool_cannot_enter_executor(self) -> None:
        calls = 0

        def execute(_calls: list[ToolCall], _parallel: bool) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return []

        bundle, raw_results, artifacts = EvidenceExecutor(execute).collect(
            outer_call=ToolCall(
                call_id="collect_denied",
                name=EVIDENCE_TOOL_NAME,
                arguments={"tools": ["sandbox.run_command"]},
            )
        )
        self.assertEqual(calls, 0)
        self.assertEqual(raw_results, [])
        self.assertEqual(artifacts, [])
        self.assertEqual(bundle["errors"][0]["code"], "invalid_evidence_plan")

    def test_durable_child_identity_prevents_reexecution(self) -> None:
        counters = {name: 0 for name in EVIDENCE_CHILD_TOOLS}
        registry = ToolRegistry()

        def handler(name: str):
            def run(**arguments):
                counters[name] += 1
                if name == "repo.list_files":
                    return {"files": ["src/a.py"], "truncated": False}
                if name == "repo.find_files":
                    return {"query": arguments["query"], "matches": ["src/a.py"], "truncated": False}
                if name == "repo.search_code":
                    return {
                        "query": arguments["query"],
                        "matches": [{"path": "src/a.py", "line": 1, "text": "needle"}],
                        "truncated": False,
                    }
                return {
                    "path": arguments["path"],
                    "start_line": 1,
                    "end_line": 1,
                    "content": "needle\n",
                    "content_hash": "hash-a",
                    "truncated": False,
                }

            return run

        for name in sorted(EVIDENCE_CHILD_TOOLS):
            registry.register(
                name,
                handler(name),
                input_schema={"type": "object", "additionalProperties": True},
                permission_level="read_only",
            )
        store = InMemoryAgentRunStore()
        change_loop = ChangeLoopExecutor(
            tools=registry,
            planner=object(),
            run_store=store,
        )
        state: CodingAgentState = {
            "run_id": "run_evidence_replay",
            "conversation_id": "conversation",
            "workspace_id": "workspace",
            "workspace_root": ".",
            "approval_policy": "on_request",
        }
        executor = EvidenceExecutor(
            lambda calls, parallel: change_loop.execute_tool_calls(
                state, calls, parallel_read_only=parallel
            )
        )
        outer = ToolCall(
            call_id="collect_stable",
            name=EVIDENCE_TOOL_NAME,
            arguments={"queries": ["needle"], "max_files": 1},
        )
        first_bundle, first_results, first_artifacts = executor.collect(outer_call=outer)
        replay_bundle, replay_results, replay_artifacts = executor.collect(outer_call=outer)

        self.assertEqual(sum(counters.values()), 4)
        self.assertTrue(all(item.get("durable_replay") for item in replay_results))
        self.assertEqual(
            [item["id"] for item in first_artifacts],
            [item["id"] for item in replay_artifacts],
        )
        self.assertEqual(first_bundle["evidence"], replay_bundle["evidence"])


class EvidenceNativeTranscriptTests(unittest.TestCase):
    def test_native_transcript_gets_one_bundle_while_raw_results_are_artifacts_and_metrics(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "src").mkdir()
            (root / "src" / "a.py").write_text("TOKEN = 1\n", encoding="utf-8")
            (root / "src" / "deep").mkdir()
            (root / "src" / "deep" / "ignored.py").write_text(
                "TOKEN = 2\n", encoding="utf-8"
            )
            registry = create_coding_tool_registry()
            runtime = CodingAgentRuntime(tool_registry=registry, planner=object())
            outer = ToolCall(
                call_id="collect_native",
                name=EVIDENCE_TOOL_NAME,
                arguments={
                    "queries": ["TOKEN"],
                    "candidate_paths": ["src/a.py"],
                    "max_files": 4,
                    "max_depth": 1,
                    "max_evidence_tokens": 512,
                },
            )
            state: CodingAgentState = {
                "run_id": "",
                "conversation_id": "conversation",
                "workspace_id": "workspace",
                "workspace_root": str(root),
                "execution_root": str(root),
                "approval_policy": "on_request",
                "native_tool_loop_active": True,
                "native_pending_tool_calls": [outer],
                "native_parallel_read_batch": False,
                "native_tool_messages": [
                    {
                        "role": "assistant",
                        "content": "",
                        "tool_calls": [
                            {
                                "call_id": outer.call_id,
                                "name": outer.name,
                                "arguments": outer.arguments,
                            }
                        ],
                    }
                ],
                "tool_results": [],
                "artifacts": [],
                "trace": [],
                "errors": [],
            }

            update = runtime._tool_loop_nodes._inspect_repository(state)
            tool_messages = [
                item
                for item in update["native_tool_messages"]
                if item.get("role") == "tool"
            ]
            self.assertEqual(len(tool_messages), 1)
            self.assertEqual(tool_messages[0]["name"], EVIDENCE_TOOL_NAME)
            self.assertEqual(
                set(tool_messages[0]["content"]),
                {
                    "coverage",
                    "evidence",
                    "unresolved",
                    "errors",
                    "raw_result_count",
                    "deduplicated_count",
                    "truncated",
                },
            )
            self.assertTrue(update["tool_results"])
            self.assertTrue(
                all(item["name"] in EVIDENCE_CHILD_TOOLS for item in update["tool_results"])
            )
            self.assertNotIn("ignored.py", json.dumps(update["tool_results"]))
            self.assertEqual(
                build_run_metrics(update).tool_call_count,
                len({item["call_id"] for item in update["tool_results"]}),
            )
            artifact_id = tool_messages[0]["content"]["evidence"][0]["artifact_id"]
            readback = read_run_artifact(
                update["artifacts"],
                {"artifact_id": artifact_id, "max_tokens": 2000},
            )
            raw = "".join(item["content"] for item in readback["ranges"])
            self.assertIn("TOKEN = 1", raw)
            self.assertIn('"arguments"', raw)

    def test_existing_direct_read_still_produces_normal_tool_message(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "a.py").write_text("value = 1\n", encoding="utf-8")
            runtime = CodingAgentRuntime(
                tool_registry=create_coding_tool_registry(), planner=object()
            )
            call = ToolCall(
                call_id="direct_read",
                name="repo.read_file",
                arguments={"path": "a.py", "max_chars": 1000},
            )
            state: CodingAgentState = {
                "run_id": "",
                "conversation_id": "conversation",
                "workspace_id": "workspace",
                "workspace_root": str(root),
                "execution_root": str(root),
                "approval_policy": "on_request",
                "native_tool_loop_active": True,
                "native_pending_tool_calls": [call],
                "native_tool_messages": [],
                "tool_results": [],
                "artifacts": [],
                "trace": [],
                "errors": [],
            }
            update = runtime._tool_loop_nodes._inspect_repository(state)
            self.assertEqual(update["native_tool_messages"][-1]["name"], "repo.read_file")
            self.assertIn("value = 1", json.dumps(update["native_tool_messages"][-1]))


if __name__ == "__main__":
    unittest.main()
