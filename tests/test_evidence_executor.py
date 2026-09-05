from __future__ import annotations
import json
import unittest
from jsonschema import Draft202012Validator
from ai_agent_platform.agents.coding.evidence_executor import EVIDENCE_TOOL_NAME, MAX_EVIDENCE_CONCURRENCY, EvidenceExecutor, evidence_plan_schema, normalize_evidence_plan
from ai_agent_platform.integrations.tools import ToolCall
from ai_agent_platform.token_counting import estimate_text_tokens

def _response(call: ToolCall, *, result: dict[str, object] | None=None, error: str | None=None) -> dict[str, object]:
    payload: dict[str, object] = {'call_id': call.call_id, 'name': call.name, 'ok': error is None, 'provider': 'local', 'permission_level': 'read_only', 'requires_approval': False, 'duration_ms': 1, 'output_truncated': False, 'attempts': 1, 'cached': False}
    if error is None:
        payload['result'] = result or {}
    else:
        payload['error'] = error
        payload['error_code'] = 'fixture_failure'
    return payload

class EvidencePlanTests(unittest.TestCase):

    def test_schema_is_strict_and_illegal_limits_use_safe_defaults(self) -> None:
        schema = evidence_plan_schema()
        errors = list(Draft202012Validator(schema).iter_errors({'tools': ['sandbox.run_command']}))
        self.assertTrue(errors)
        self.assertFalse(schema['additionalProperties'])
        plan, deduplicated = normalize_evidence_plan({'queries': ['Token usage', ' token   usage '], 'candidate_paths': ['./src/a.py', 'src/a.py'], 'max_files': 0, 'max_depth': 'unbounded', 'max_results_per_query': 999, 'max_chars_per_file': -1, 'max_evidence_tokens': True})
        self.assertEqual(plan.queries, ['Token usage'])
        self.assertEqual(plan.candidate_paths, ['src/a.py'])
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
                if call.name == 'repo.list_files':
                    output.append(_response(call, result={'files': ['src/a.py', 'src/duplicate.py', 'node_modules/ignored.js', 'deep/one/two/three/four.py'], 'truncated': False}))
                elif call.name == 'repo.find_files':
                    output.append(_response(call, result={'query': call.arguments['query'], 'matches': ['src/a.py'], 'truncated': False}))
                elif call.name == 'repo.search_code':
                    output.append(_response(call, result={'query': call.arguments['query'], 'matches': [{'path': 'src/a.py', 'line': 1, 'text': 'TOKEN'}], 'truncated': False}))
                elif call.arguments['path'] == 'src/missing.py':
                    output.append(_response(call, error='file does not exist'))
                else:
                    content = 'TOKEN = 1\n' + 'x' * 4000
                    output.append(_response(call, result={'path': call.arguments['path'], 'start_line': 1, 'end_line': 2, 'content': content, 'content_hash': 'same-content', 'truncated': False}))
            return output
        executor = EvidenceExecutor(execute)
        bundle, raw_results, artifacts = executor.collect(outer_call=ToolCall(call_id='collect_one', name=EVIDENCE_TOOL_NAME, arguments={'queries': ['TOKEN', ' token '], 'candidate_paths': ['src/a.py', './src/a.py', 'src/duplicate.py', 'src/missing.py'], 'max_files': 3, 'max_depth': 3, 'max_evidence_tokens': 256, 'required_evidence': ['TOKEN', 'missing-symbol']}))
        self.assertEqual(batches[0][0].count('repo.search_code'), 1)
        self.assertEqual(batches[0][0].count('repo.find_files'), 1)
        self.assertTrue(all((parallel for _, parallel in batches)))
        self.assertTrue(all((len(names) <= MAX_EVIDENCE_CONCURRENCY for names, _ in batches)))
        self.assertTrue(all((result['arguments'].get('max_depth') == 3 for result in raw_results[:3])))
        self.assertEqual(len(raw_results), 6)
        self.assertEqual(len(artifacts), len(raw_results))
        self.assertEqual(len(bundle['evidence']), 1)
        self.assertEqual(bundle['coverage'], ['TOKEN'])
        self.assertEqual(bundle['unresolved'], ['missing-symbol'])
        self.assertEqual(len(bundle['errors']), 1)
        self.assertGreaterEqual(bundle['deduplicated_count'], 4)
        self.assertLessEqual(estimate_text_tokens(json.dumps(bundle, ensure_ascii=False, sort_keys=True, separators=(',', ':'))), 256)
        self.assertTrue(bundle['truncated'])
        self.assertTrue(all((item.get('evidence_raw_result') for item in artifacts)))
        self.assertTrue(all(('arguments' in item for item in artifacts)))

    def test_side_effect_tool_cannot_enter_executor(self) -> None:
        calls = 0

        def execute(_calls: list[ToolCall], _parallel: bool) -> list[dict[str, object]]:
            nonlocal calls
            calls += 1
            return []
        bundle, raw_results, artifacts = EvidenceExecutor(execute).collect(outer_call=ToolCall(call_id='collect_denied', name=EVIDENCE_TOOL_NAME, arguments={'tools': ['sandbox.run_command']}))
        self.assertEqual(calls, 0)
        self.assertEqual(raw_results, [])
        self.assertEqual(artifacts, [])
        self.assertEqual(bundle['errors'][0]['code'], 'invalid_evidence_plan')
