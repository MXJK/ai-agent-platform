from __future__ import annotations
import unittest
from ai_agent_platform.agents.coding.run_artifacts import ArtifactReadError, RUN_ARTIFACT_READ_TOOL, build_run_tool_result_artifact, canonical_tool_result, read_run_artifact
from ai_agent_platform.agents.coding.tools import create_coding_tool_registry

class RunArtifactPrimitiveTests(unittest.TestCase):

    def setUp(self) -> None:
        self.result = {'call_id': 'unicode_call', 'name': 'mcp.demo.lookup', 'ok': True, 'result': {'text': '前缀🙂' * 1500, 'values': [3, 2, 1]}}
        self.artifact = build_run_tool_result_artifact(self.result)

    def test_runtime_tool_is_read_only_idempotent_and_strict(self) -> None:
        spec = create_coding_tool_registry().get_spec(RUN_ARTIFACT_READ_TOOL)
        self.assertIsNotNone(spec)
        assert spec is not None
        self.assertEqual(spec.provider, 'runtime')
        self.assertEqual(spec.permission_level, 'read_only')
        self.assertFalse(spec.requires_approval)
        self.assertTrue(spec.idempotent)
        self.assertFalse(spec.accepts_context)
        self.assertEqual(spec.input_schema['additionalProperties'], False)
        self.assertEqual(set(spec.input_schema['required']), {'artifact_id'})

    def test_consecutive_pages_reconstruct_canonical_unicode_json(self) -> None:
        offset = 0
        pages: list[str] = []
        while True:
            page = read_run_artifact([self.artifact], {'artifact_id': self.artifact['id'], 'offset_chars': offset, 'max_tokens': 64})
            pages.append(''.join((item['content'] for item in page['ranges'])))
            next_offset = page['next_offset_chars']
            if next_offset is None:
                break
            self.assertGreater(next_offset, offset)
            offset = next_offset
        self.assertEqual(''.join(pages), canonical_tool_result(self.result))

    def test_hash_corruption_and_untrusted_flags_fail_closed(self) -> None:
        artifact_id = self.artifact['id']
        for changed in ({**self.artifact, 'content_sha256': 'sha256:' + '0' * 64}, {**self.artifact, 'runtime_created': False}, {**self.artifact, 'model_readable': False}, {**self.artifact, 'type': 'mcp_output'}, {**self.artifact, 'call_id': 'different_call'}, {**self.artifact, 'name': 'different.tool'}, {**self.artifact, 'estimated_tokens': -1}):
            with self.assertRaises(ArtifactReadError) as caught:
                read_run_artifact([changed], {'artifact_id': artifact_id})
            self.assertEqual(caught.exception.code, 'artifact_not_found')

    def test_strict_arguments_and_offsets_have_non_oracle_errors(self) -> None:
        artifact_id = self.artifact['id']
        with self.assertRaises(ArtifactReadError) as invalid:
            read_run_artifact([self.artifact], {'artifact_id': artifact_id, 'run_id': 'another'})
        self.assertEqual(invalid.exception.code, 'artifact_not_found')
        with self.assertRaises(ArtifactReadError) as missing:
            read_run_artifact([self.artifact], {'artifact_id': 'tool_result_' + '0' * 20})
        self.assertEqual(missing.exception.code, 'artifact_not_found')
        with self.assertRaises(ArtifactReadError) as offset:
            read_run_artifact([self.artifact], {'artifact_id': artifact_id, 'offset_chars': self.artifact['content_chars']})
        self.assertEqual(offset.exception.code, 'artifact_offset_out_of_range')

    def test_head_tail_reports_exact_non_overlapping_ranges(self) -> None:
        result = read_run_artifact([self.artifact], {'artifact_id': self.artifact['id'], 'view': 'head_tail', 'offset_chars': 7, 'max_tokens': 64})
        canonical = canonical_tool_result(self.result)
        first, second = result['ranges']
        self.assertEqual(first['content'], canonical[first['start_char']:first['end_char']])
        self.assertEqual(second['content'], canonical[second['start_char']:second['end_char']])
        self.assertLessEqual(first['end_char'], second['start_char'])
