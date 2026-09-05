import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import httpx
from google.genai import types
from ai_agent_platform.core import Settings
from ai_agent_platform.integrations.llm import LLMClient, LLMProviderError, LLMUsage, _anthropic_tool_messages, _deepseek_tool_messages, _effective_model_output_limit, collect_llm_usage, _google_tool_contents, _json_arguments, _openai_tool_input
from ai_agent_platform.integrations.model_router import ModelCapabilities, ModelConfig, ModelRouter
from ai_agent_platform.integrations.tools import ToolSpec

def _tool_spec(name: str='repo.read_file') -> ToolSpec:
    return ToolSpec(name=name, description='Read one file.', input_schema={'type': 'object', 'properties': {'path': {'type': 'string'}}, 'required': ['path'], 'additionalProperties': False}, output_schema={'type': 'object'}, provider='local')

def _runtime_artifact_messages() -> list[dict[str, object]]:
    return [{'role': 'user', 'content': 'continue after collecting artifacts'}, {'role': 'assistant', 'content': 'Collecting runtime-managed change artifacts.', 'tool_calls': [{'call_id': 'runtime_status', 'name': 'sandbox.workspace_status', 'arguments': {}}, {'call_id': 'runtime_diff', 'name': 'sandbox.git_diff', 'arguments': {}}]}, {'role': 'tool', 'call_id': 'runtime_status', 'name': 'sandbox.workspace_status', 'content': {'ok': True, 'result': {'changed_files': ['index.html']}}}, {'role': 'tool', 'call_id': 'runtime_diff', 'name': 'sandbox.git_diff', 'content': {'ok': True, 'result': {'diff': '+<title>Game</title>'}}}]

class NativeProviderMappingTests(unittest.TestCase):

    def test_effective_output_limit_uses_phase_model_and_context_minimum(self) -> None:
        model = ModelConfig(provider='deepseek', model='deepseek-test', context_window_tokens=10000, max_output_tokens=8192)
        self.assertEqual(_effective_model_output_limit(model, input_tokens=1000, requested_output_tokens=16384), 8192)
        self.assertEqual(_effective_model_output_limit(model, input_tokens=9500, requested_output_tokens=16384), 500)

    def test_runtime_artifact_tool_history_is_safe_for_every_provider(self) -> None:
        messages = _runtime_artifact_messages()
        aliases = {'sandbox.workspace_status': 'sandbox_workspace_status', 'sandbox.git_diff': 'sandbox_git_diff'}
        openai_items = _openai_tool_input(messages, aliases)
        openai_calls = [item for item in openai_items if item.get('type') == 'function_call']
        self.assertEqual([item['name'] for item in openai_calls], ['sandbox_workspace_status', 'sandbox_git_diff'])
        self.assertEqual([item['call_id'] for item in openai_items if item.get('type') == 'function_call_output'], ['runtime_status', 'runtime_diff'])
        anthropic_messages = _anthropic_tool_messages(messages, aliases)
        anthropic_assistant = anthropic_messages[1]
        self.assertEqual(anthropic_assistant['role'], 'assistant')
        self.assertEqual([block['name'] for block in anthropic_assistant['content'] if block['type'] == 'tool_use'], ['sandbox_workspace_status', 'sandbox_git_diff'])
        self.assertEqual([block['tool_use_id'] for block in anthropic_messages[2]['content']], ['runtime_status', 'runtime_diff'])
        deepseek_messages = _deepseek_tool_messages(messages, aliases)
        deepseek_assistant = deepseek_messages[1]
        self.assertEqual(deepseek_assistant['reasoning_content'], '')
        self.assertEqual([item['function']['name'] for item in deepseek_assistant['tool_calls']], ['sandbox_workspace_status', 'sandbox_git_diff'])
        google_contents = _google_tool_contents(messages, types, aliases)
        google_parts = [part for content in google_contents for part in content.parts]
        self.assertTrue(any(('runtime_status' in (part.text or '') for part in google_parts)))
        self.assertTrue(all((part.function_call is None for part in google_parts)))
        self.assertTrue(all((part.function_response is None for part in google_parts)))

    def test_http_provider_error_keeps_safe_detail_and_redacts_credentials(self) -> None:

        class FakeResponse:
            status_code = 400

            @staticmethod
            def json():
                return {'error': {'message': 'The `reasoning_content` field is required; api_key=diagnostic-placeholder'}}

        class FakeClient:

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            @staticmethod
            def post(url, *, headers, json):
                del url, headers, json
                return FakeResponse()
        client = LLMClient(Settings())
        with patch('ai_agent_platform.integrations.llm.httpx.Client', return_value=FakeClient()), self.assertRaises(LLMProviderError) as raised:
            client._post_json('https://provider.example/v1/messages', headers={}, payload={'messages': []})
        self.assertIn('HTTP 400', str(raised.exception))
        self.assertIn('reasoning_content', str(raised.exception))
        self.assertIn('api_key=[REDACTED]', str(raised.exception))
        self.assertNotIn('diagnostic-placeholder', str(raised.exception))
        self.assertEqual(raised.exception.code, 'llm_http_error')

    def test_malformed_tool_arguments_expose_safe_retry_diagnostics(self) -> None:
        value = '{"path":"index.html","content":"unterminated'
        with self.assertRaises(LLMProviderError) as raised:
            _json_arguments(value, finish_reason='length', usage=LLMUsage(input_tokens=20, output_tokens=4096))
        error = raised.exception
        self.assertTrue(error.retryable)
        self.assertEqual(error.code, 'tool_arguments_truncated')
        self.assertEqual(error.finish_reason, 'length')
        self.assertEqual(error.tool_argument_chars, len(value))
        self.assertIsInstance(error.json_error_position, int)
        self.assertNotIn('unterminated', str(error))

    def test_deepseek_retries_truncated_arguments_and_records_failed_usage(self) -> None:

        class RecordingLedger:

            def __init__(self) -> None:
                self.authorizations: list[dict[str, object]] = []
                self.records: list[dict[str, object]] = []

            def authorize(self, **kwargs):
                self.authorizations.append(dict(kwargs))
                return SimpleNamespace(provider=kwargs['requested_provider'], model=kwargs['requested_model'], max_output_tokens=kwargs['max_output_tokens'], budget_decision='allowed', budget_reason=None)

            def record(self, **kwargs):
                self.records.append(dict(kwargs))
        ledger = RecordingLedger()
        router = ModelRouter([ModelConfig(provider='deepseek', model='deepseek-v4-flash', context_window_tokens=128000, max_output_tokens=8192, capabilities=ModelCapabilities(tool_calling=True, structured_output=True))])
        client = LLMClient(Settings(llm_provider='deepseek', llm_model='deepseek-v4-flash', llm_max_retries=1, llm_retry_policy_json='{"tool_output_truncated": 1}'), usage_ledger=ledger, model_router=router, credential_resolver=lambda provider: 'test-key' if provider == 'deepseek' else None)
        responses = [{'model': 'deepseek-v4-flash', 'choices': [{'finish_reason': 'length', 'message': {'content': None, 'tool_calls': [{'id': 'truncated_1', 'function': {'name': 'sandbox_apply_patch', 'arguments': '{"patch":"*** Begin Patch'}}]}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 4096}}, {'model': 'deepseek-v4-flash', 'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [{'id': 'recovered_1', 'function': {'name': 'sandbox_apply_patch', 'arguments': '{"patch":"small patch"}'}}]}}], 'usage': {'prompt_tokens': 24, 'completion_tokens': 12}}]
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            del url, headers
            payloads.append(payload)
            return responses.pop(0)
        with patch.object(client, '_post_json', side_effect=fake_post), patch('ai_agent_platform.integrations.llm.time.sleep'), collect_llm_usage() as usage:
            decision = client.decide_tools([{'role': 'user', 'content': 'create the app'}], [_tool_spec('sandbox.apply_patch')], max_output_tokens=16384)
        self.assertEqual(decision.tool_calls[0].call_id, 'recovered_1')
        self.assertEqual(decision.tool_calls[0].arguments, {'patch': 'small patch'})
        self.assertEqual([item['max_tokens'] for item in payloads], [8192, 8192])
        self.assertTrue(any(('exactly one tool call' in str(message.get('content') or '') for message in payloads[1]['messages'])))
        self.assertEqual(len(ledger.records), 2)
        self.assertEqual(usage.input_tokens, 44)
        self.assertEqual(usage.output_tokens, 4108)
        self.assertEqual(usage.request_count, 2)
        self.assertEqual(usage.retry_count, 1)

    def test_finalization_uses_selected_models_declared_output_limit(self) -> None:
        router = ModelRouter([ModelConfig(provider='deepseek', model='deepseek-v4-flash', context_window_tokens=128000, max_output_tokens=8192, capabilities=ModelCapabilities(tool_calling=True))])
        client = LLMClient(Settings(llm_provider='deepseek', llm_model='deepseek-v4-flash', llm_max_output_tokens=2048), model_router=router, credential_resolver=lambda provider: 'test-key' if provider == 'deepseek' else None)
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            del url, headers
            payloads.append(payload)
            return {'model': 'deepseek-v4-flash', 'choices': [{'finish_reason': 'stop', 'message': {'content': '完整最终回答'}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 8}}
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.finalize_tools([{'role': 'user', 'content': 'summarize the result'}], reason='completed', use_model_max_output_tokens=True)
        self.assertEqual(decision.text, '完整最终回答')
        self.assertEqual(payloads[0]['max_tokens'], 8192)

    def test_finalization_falls_back_to_default_when_model_has_no_output_limit(self) -> None:
        router = ModelRouter([ModelConfig(provider='deepseek', model='deepseek-chat', context_window_tokens=128000, capabilities=ModelCapabilities(tool_calling=True))])
        client = LLMClient(Settings(llm_provider='deepseek', llm_model='deepseek-chat', llm_max_output_tokens=3072), model_router=router, credential_resolver=lambda provider: 'test-key' if provider == 'deepseek' else None)
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            del url, headers
            payloads.append(payload)
            return {'model': 'deepseek-chat', 'choices': [{'finish_reason': 'stop', 'message': {'content': '完成'}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 2}}
        with patch.object(client, '_post_json', side_effect=fake_post):
            client.finalize_tools([{'role': 'user', 'content': 'summarize'}], reason='completed', use_model_max_output_tokens=True)
        self.assertEqual(payloads[0]['max_tokens'], 3072)

    def test_glm_native_tool_decision_uses_chat_completions_layer(self) -> None:
        router = ModelRouter([ModelConfig(provider='glm', model='glm-4.6', context_window_tokens=128000, max_output_tokens=8192, capabilities=ModelCapabilities(tool_calling=True, structured_output=True))])
        client = LLMClient(Settings(llm_provider='glm', llm_model='glm-4.6'), model_router=router, credential_resolver=lambda provider: 'test-key' if provider == 'glm' else None)
        requests: list[tuple[str, dict[str, object], dict[str, object]]] = []

        def fake_post(url, *, headers, payload):
            requests.append((url, headers, payload))
            return {'model': 'glm-4.6', 'choices': [{'finish_reason': 'tool_calls', 'message': {'content': None, 'tool_calls': [{'id': 'glm_call_1', 'function': {'name': 'repo_read_file', 'arguments': '{"path": "README.md"}'}}]}}], 'usage': {'prompt_tokens': 30, 'completion_tokens': 12}}
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.decide_tools([{'role': 'user', 'content': 'read the readme'}], [_tool_spec('repo.read_file')], max_output_tokens=8192)
        self.assertEqual(decision.provider, 'glm')
        self.assertEqual(decision.model, 'glm-4.6')
        self.assertEqual(decision.stop_reason, 'tool_calls')
        self.assertEqual(len(requests), 1)
        url, headers, payload = requests[0]
        self.assertEqual(url, 'https://open.bigmodel.cn/api/paas/v4/chat/completions')
        self.assertEqual(headers['Authorization'], 'Bearer test-key')
        self.assertEqual(payload['model'], 'glm-4.6')
        call = decision.tool_calls[0]
        self.assertEqual(call.call_id, 'glm_call_1')
        self.assertEqual(call.name, 'repo.read_file')
        self.assertEqual(call.arguments, {'path': 'README.md'})
        self.assertEqual(call.source, 'glm_native')
        assert decision.usage is not None
        self.assertEqual(decision.usage.input_tokens, 30)
        self.assertEqual(decision.usage.output_tokens, 12)
        replayed = _deepseek_tool_messages([{'role': 'user', 'content': 'read the readme'}, {'role': 'assistant', 'provider': 'glm', 'content': decision.text, 'tool_calls': [{'call_id': call.call_id, 'name': call.name, 'arguments': call.arguments}], 'provider_items': decision.provider_items}, {'role': 'tool', 'call_id': call.call_id, 'name': call.name, 'content': {'ok': True}}], {'repo.read_file': 'repo_read_file'}, provider='glm')
        self.assertEqual(replayed[1]['tool_calls'][0]['id'], 'glm_call_1')
        self.assertEqual(replayed[1]['tool_calls'][0]['function']['name'], 'repo_read_file')
        self.assertEqual(replayed[-1]['tool_call_id'], 'glm_call_1')

    def test_domestic_finalize_omits_unsupported_tool_choice_none(self) -> None:
        for provider in ('glm', 'minimax', 'doubao'):
            with self.subTest(provider=provider):
                client = LLMClient(Settings(llm_provider=provider, llm_model=f'{provider}-test'), credential_resolver=lambda item, expected=provider: 'test-key' if item == expected else None)
                with patch.object(client, '_native_tool_response', return_value={'model': f'{provider}-test', 'choices': [{'finish_reason': 'stop', 'message': {'content': 'done'}}]}) as request:
                    decision = client._decide_chat_completions_tools(provider, [{'role': 'user', 'content': 'finish'}], [_tool_spec('repo.read_file')], {'repo_read_file': 'repo.read_file'}, f'{provider}-test', max_output_tokens=1024, disable_tool_calls=True)
                self.assertEqual(decision.text, 'done')
                payload = request.call_args.kwargs['payload']
                self.assertNotIn('tools', payload)
                self.assertNotIn('tool_choice', payload)
                if provider == 'minimax':
                    self.assertIs(payload['reasoning_split'], True)

    def test_chat_completion_history_does_not_leak_provider_private_fields(self) -> None:
        messages = [{'role': 'assistant', 'provider': 'minimax', 'content': '', 'tool_calls': [{'call_id': 'call_1', 'name': 'repo.read_file', 'arguments': {'path': 'README.md'}}], 'provider_items': [{'role': 'assistant', 'content': '', 'reasoning_details': [{'text': 'private'}], 'tool_calls': []}]}]
        aliases = {'repo.read_file': 'repo_read_file'}
        minimax = _deepseek_tool_messages(messages, aliases, provider='minimax')
        glm = _deepseek_tool_messages(messages, aliases, provider='glm')
        self.assertIn('reasoning_details', minimax[0])
        self.assertNotIn('reasoning_details', glm[0])
        self.assertNotIn('reasoning_content', glm[0])
        self.assertEqual(glm[0]['tool_calls'][0]['id'], 'call_1')

    def test_google_converts_foreign_tool_history_to_text_without_signatures(self) -> None:
        contents = _google_tool_contents([{'role': 'user', 'content': 'inspect the workspace'}, {'role': 'assistant', 'provider': 'deepseek', 'content': '', 'tool_calls': [{'call_id': 'deepseek_call_1', 'name': 'repo.list_files', 'arguments': {'path': ''}}]}, {'role': 'tool', 'call_id': 'deepseek_call_1', 'name': 'repo.list_files', 'content': {'ok': True, 'result': {'files': []}}}], types, {'repo.list_files': 'repo_list_files'})
        parts = [part for content in contents for part in content.parts]
        self.assertTrue(any(('previous provider' in (part.text or '') for part in parts)))
        self.assertTrue(any(('deepseek_call_1' in (part.text or '') for part in parts)))
        self.assertTrue(all((part.function_call is None for part in parts)))
        self.assertTrue(all((part.function_response is None for part in parts)))

    def test_native_provider_can_select_namespaced_mcp_tool(self) -> None:
        client = LLMClient(Settings(llm_provider='openai', llm_model='test-openai'), credential_resolver=lambda provider: 'test-key' if provider == 'openai' else None)
        mcp_spec = _tool_spec('mcp.github.search_code')
        response = {'model': 'test-openai', 'status': 'completed', 'output': [{'type': 'function_call', 'call_id': 'mcp_call_1', 'name': 'mcp_github_search_code', 'arguments': '{"path":"README.md"}'}]}

        def fake_post(url, *, headers, payload):
            if url.endswith('/input_tokens'):
                return {'input_tokens': 8}
            return response
        with patch.object(client, '_post_json', side_effect=fake_post) as post:
            decision = client.decide_tools([{'role': 'user', 'content': 'search GitHub'}], [mcp_spec])
        self.assertEqual(decision.tool_calls[0].name, 'mcp.github.search_code')
        self.assertEqual(decision.tool_calls[0].call_id, 'mcp_call_1')
        self.assertEqual(post.call_args.kwargs['payload']['tools'][0]['name'], 'mcp_github_search_code')

    def test_openai_uses_native_tools_and_preserves_call_id_for_result(self) -> None:
        client = LLMClient(Settings(llm_provider='openai', llm_model='test-openai'), credential_resolver=lambda provider: 'test-key' if provider == 'openai' else None)
        responses = [{'model': 'test-openai', 'status': 'completed', 'output': [{'type': 'function_call', 'id': 'fc_1', 'call_id': 'call_1', 'name': 'repo_read_file', 'arguments': '{"path":"app.py"}'}], 'usage': {'input_tokens': 10, 'output_tokens': 4}}, {'model': 'test-openai', 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'app.py contains value=1'}]}], 'usage': {'input_tokens': 20, 'output_tokens': 6}}]
        payloads: list[dict[str, object]] = []

        def fake_post(url, *, headers, payload):
            if url.endswith('/input_tokens'):
                return {'input_tokens': 10}
            payloads.append(payload)
            return responses.pop(0)
        with patch.object(client, '_post_json', side_effect=fake_post):
            first = client.decide_tools([{'role': 'user', 'content': 'read app.py'}], [_tool_spec()])
            second = client.decide_tools([{'role': 'user', 'content': 'read app.py'}, {'role': 'assistant', 'content': first.text, 'provider': first.provider, 'provider_items': first.provider_items, 'tool_calls': [{'call_id': first.tool_calls[0].call_id, 'name': first.tool_calls[0].name, 'arguments': first.tool_calls[0].arguments}]}, {'role': 'tool', 'call_id': 'call_1', 'name': 'repo.read_file', 'content': {'ok': True, 'result': {'content': 'value=1'}}}], [_tool_spec()])
        self.assertEqual(first.tool_calls[0].name, 'repo.read_file')
        self.assertEqual(first.tool_calls[0].call_id, 'call_1')
        self.assertEqual(payloads[0]['tools'][0]['name'], 'repo_read_file')
        self.assertTrue(payloads[0]['parallel_tool_calls'])
        result_item = next((item for item in payloads[1]['input'] if item.get('type') == 'function_call_output'))
        self.assertEqual(result_item['call_id'], 'call_1')
        self.assertEqual(second.text, 'app.py contains value=1')

    def test_openai_finalization_preserves_tool_transcript_and_disables_tools(self) -> None:
        client = LLMClient(Settings(llm_provider='openai', llm_model='test-openai'), credential_resolver=lambda provider: 'test-key' if provider == 'openai' else None)
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith('/input_tokens'):
                return {'input_tokens': 12}
            captured.update(payload)
            return {'model': 'test-openai', 'status': 'completed', 'output': [{'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'Grounded final answer.'}]}]}
        messages = [{'role': 'user', 'content': 'read app.py'}, {'role': 'assistant', 'content': '', 'tool_calls': [{'call_id': 'call_final', 'name': 'repo.read_file', 'arguments': {'path': 'app.py'}}]}, {'role': 'tool', 'call_id': 'call_final', 'name': 'repo.read_file', 'content': {'ok': True, 'result': {'content': 'value=1'}}}]
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.finalize_tools(messages, reason='hard_tool_round_budget', tools=[_tool_spec()])
        self.assertEqual(decision.text, 'Grounded final answer.')
        self.assertEqual(captured['tools'][0]['name'], 'repo_read_file')
        self.assertFalse(captured['parallel_tool_calls'])
        self.assertEqual(captured['tool_choice'], 'none')
        self.assertTrue(any((item.get('type') == 'function_call_output' for item in captured['input'])))

    def test_finalization_without_tool_definitions_flattens_tool_blocks(self) -> None:
        client = LLMClient(Settings(llm_provider='anthropic', llm_model='test-claude'), credential_resolver=lambda provider: 'test-key' if provider == 'anthropic' else None)
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith('count_tokens'):
                return {'input_tokens': 24}
            captured.update(payload)
            return {'model': 'test-claude', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Grounded final answer.'}], 'usage': {'input_tokens': 24, 'output_tokens': 4}}
        messages = [{'role': 'user', 'content': 'read app.py'}, {'role': 'assistant', 'content': '', 'provider': 'anthropic', 'provider_items': [{'type': 'tool_use', 'id': 'toolu_final', 'name': 'repo_read_file', 'input': {'path': 'app.py'}}], 'tool_calls': [{'call_id': 'toolu_final', 'name': 'repo.read_file', 'arguments': {'path': 'app.py'}}]}, {'role': 'tool', 'call_id': 'toolu_final', 'name': 'repo.read_file', 'content': {'ok': True, 'result': {'content': 'value=1'}}}]
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.finalize_tools(messages, reason='permission_denied', tools=[])
        self.assertEqual(decision.text, 'Grounded final answer.')
        self.assertNotIn('tools', captured)
        block_types = [[block.get('type') for block in message['content']] for message in captured['messages']]
        self.assertEqual(block_types, [['text'], ['text'], ['text']])
        self.assertIn('value=1', json.dumps(captured['messages'], ensure_ascii=False))

    def test_anthropic_finalization_keeps_tool_definitions_for_replayed_blocks(self) -> None:
        client = LLMClient(Settings(llm_provider='anthropic', llm_model='test-claude'), credential_resolver=lambda provider: 'test-key' if provider == 'anthropic' else None)
        captured: dict[str, object] = {}
        count_payload: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            del headers
            if url.endswith('count_tokens'):
                count_payload.update(payload)
                return {'input_tokens': 24}
            captured.update(payload)
            return {'model': 'test-claude', 'stop_reason': 'end_turn', 'content': [{'type': 'text', 'text': 'Grounded final answer.'}], 'usage': {'input_tokens': 24, 'output_tokens': 4}}
        messages = [{'role': 'user', 'content': 'read app.py'}, {'role': 'assistant', 'content': '', 'tool_calls': [{'call_id': 'toolu_final', 'name': 'repo.read_file', 'arguments': {'path': 'app.py'}}]}, {'role': 'tool', 'call_id': 'toolu_final', 'name': 'repo.read_file', 'content': {'ok': True, 'result': {'content': 'value=1'}}}]
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.finalize_tools(messages, reason='hard_tool_round_budget', tools=[_tool_spec()])
        self.assertEqual(decision.text, 'Grounded final answer.')
        self.assertEqual(captured['tools'][0]['name'], 'repo_read_file')
        self.assertEqual(captured['tool_choice'], {'type': 'none'})
        self.assertEqual([item['name'] for item in count_payload['tools']], ['repo_read_file'])
        block_types = [[block.get('type') for block in message['content']] for message in captured['messages']]
        self.assertEqual(block_types, [['text'], ['tool_use'], ['tool_result']])

    def test_anthropic_maps_tool_use_and_usage(self) -> None:
        client = LLMClient(Settings(llm_provider='anthropic', llm_model='test-claude'), credential_resolver=lambda provider: 'test-key' if provider == 'anthropic' else None)
        captured: dict[str, object] = {}

        def fake_post(url, *, headers, payload):
            if url.endswith('/count_tokens'):
                return {'input_tokens': 11}
            captured.update(payload)
            return {'model': 'test-claude', 'stop_reason': 'tool_use', 'content': [{'type': 'tool_use', 'id': 'toolu_1', 'name': 'repo_read_file', 'input': {'path': 'app.py'}}], 'usage': {'input_tokens': 11, 'output_tokens': 5}}
        with patch.object(client, '_post_json', side_effect=fake_post):
            decision = client.decide_tools([{'role': 'user', 'content': 'read app.py'}], [_tool_spec()])
        self.assertEqual(decision.tool_calls[0].call_id, 'toolu_1')
        self.assertEqual(decision.tool_calls[0].name, 'repo.read_file')
        self.assertEqual(decision.usage, LLMUsage(input_tokens=11, output_tokens=5))
        self.assertEqual(captured['tools'][0]['name'], 'repo_read_file')
        self.assertFalse(captured['tool_choice']['disable_parallel_tool_use'])

    def test_google_maps_function_call_and_provider_content(self) -> None:
        response_content = types.Content(role='model', parts=[types.Part(function_call=types.FunctionCall(id='google_call_1', name='repo_read_file', args={'path': 'app.py'}))])
        response = SimpleNamespace(candidates=[SimpleNamespace(content=response_content, finish_reason='STOP')], usage_metadata=SimpleNamespace(prompt_token_count=9, candidates_token_count=3, thoughts_token_count=1))

        class FakeModels:

            def __init__(self) -> None:
                self.kwargs: dict[str, object] = {}
                self.count_kwargs: dict[str, object] = {}

            def generate_content(self, **kwargs):
                self.kwargs = kwargs
                return response

            def count_tokens(self, **kwargs):
                self.count_kwargs = kwargs
                return SimpleNamespace(total_tokens=9)
        fake_client = SimpleNamespace(models=FakeModels(), close=lambda: None)
        client = LLMClient(Settings(llm_provider='google', llm_model='gemini-test'), credential_resolver=lambda provider: 'test-key' if provider == 'google' else None)
        with patch('google.genai.Client', return_value=fake_client):
            decision = client.decide_tools([{'role': 'system', 'content': 'follow repository policy'}, {'role': 'user', 'content': 'read app.py'}], [_tool_spec()])
        self.assertEqual(decision.tool_calls[0].call_id, 'google_call_1')
        self.assertEqual(decision.tool_calls[0].name, 'repo.read_file')
        self.assertEqual(decision.usage.total_tokens, 13)
        self.assertTrue(decision.provider_items)
        self.assertEqual(fake_client.models.kwargs['config'].system_instruction, 'follow repository policy')
        self.assertIsNone(fake_client.models.count_kwargs['config'].system_instruction)
        self.assertIsNone(fake_client.models.count_kwargs['config'].tools)
        count_context = fake_client.models.count_kwargs['contents'][0].parts[0].text
        self.assertIn('follow repository policy', count_context)
        self.assertIn('repo_read_file', count_context)
        self.assertIn('"path"', count_context)
        config = fake_client.models.kwargs['config']
        self.assertEqual(config.tools[0].function_declarations[0].name, 'repo_read_file')

class NativeAnswerStreamingTests(unittest.TestCase):

    def _client(self, provider):
        return LLMClient(Settings(llm_provider=provider, llm_model='stream-test', llm_max_retries=2), credential_resolver=lambda _: 'test-key', sleep=lambda _: None)

    def _http_turn(self, provider, events, *, on_delta=None, finalize=False):
        client = self._client(provider)
        deltas = []
        observed = {}
        test_case = self

        class ResponseStream(httpx.SyncByteStream):

            def __iter__(self):
                for event in events:
                    if callable(event):
                        event(deltas)
                    elif isinstance(event, Exception):
                        raise event
                    else:
                        yield ('data: ' + json.dumps(event, ensure_ascii=False) + '\n\n').encode()

            def close(self):
                observed['closed'] = True

        def respond(request):
            payload = json.loads(request.content)
            observed['payload'] = payload
            test_case.assertTrue(payload['stream'])
            return httpx.Response(200, stream=ResponseStream())

        def collect(text):
            deltas.append(text)
            if on_delta:
                on_delta(text)
        http_client = httpx.Client(transport=httpx.MockTransport(respond))
        with patch('httpx.Client', return_value=http_client), patch.object(client, '_count_tool_input_tokens', return_value=(10, 'test')):
            if finalize:
                decision = client.finalize_tools([{'role': 'user', 'content': 'explain'}], tools=[_tool_spec()], reason='soft_budget', on_delta=collect)
            else:
                decision = client.decide_tools([{'role': 'user', 'content': 'explain'}], [_tool_spec()], on_delta=collect)
        self.assertTrue(observed['closed'])
        return (decision, deltas, observed['payload'])

    def _text_events(self, provider, probe):
        if provider == 'openai':
            return [{'type': 'response.output_text.delta', 'delta': '第一段'}, probe, {'type': 'response.output_text.delta', 'delta': '，第二段'}, {'type': 'response.completed', 'response': {'status': 'completed', 'output': [{'type': 'message', 'content': [{'type': 'output_text', 'text': '第一段，第二段'}]}], 'usage': {'input_tokens': 10, 'output_tokens': 6}}}]
        if provider == 'anthropic':
            return [{'type': 'message_start', 'message': {'usage': {'input_tokens': 10}}}, {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'text', 'text': ''}}, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '第一段'}}, probe, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'text_delta', 'text': '，第二段'}}, {'type': 'content_block_stop', 'index': 0}, {'type': 'message_delta', 'delta': {'stop_reason': 'end_turn'}, 'usage': {'output_tokens': 6}}, {'type': 'message_stop'}]
        return [{'choices': [{'index': 0, 'delta': {'reasoning_content': 'private thought'}}]}, {'choices': [{'index': 0, 'delta': {'content': '第一段'}}]}, probe, {'choices': [{'index': 0, 'delta': {'content': '，第二段'}}]}, {'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}, {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 6}}]

    def test_http_native_answers_arrive_before_completion_and_preserve_usage(self):
        for provider in ('openai', 'anthropic', 'deepseek'):
            for finalize in (False, True):
                with self.subTest(provider=provider, finalize=finalize):

                    def probe(deltas):
                        self.assertEqual(deltas, ['第一段'])
                    decision, deltas, payload = self._http_turn(provider, self._text_events(provider, probe), finalize=finalize)
                    self.assertEqual(''.join(deltas), decision.text)
                    self.assertEqual(decision.text, '第一段，第二段')
                    self.assertEqual(decision.usage, LLMUsage(10, 6))
                    self.assertFalse(decision.tool_calls)
                    if finalize:
                        self.assertEqual(payload['tool_choice'], {'type': 'none'} if provider == 'anthropic' else 'none')

    def test_deepseek_split_dsml_never_streams_protocol_text(self):
        raw = 'Inspecting. <｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="repo_read_file"><｜｜DSML｜｜parameter name="path" string="true">README.md</｜｜DSML｜｜parameter></｜｜DSML｜｜invoke></｜｜DSML｜｜tool_calls>'
        events = [{'choices': [{'index': 0, 'delta': {'content': char}}]} for char in raw] + [{'choices': [{'index': 0, 'delta': {}, 'finish_reason': 'stop'}]}, {'choices': [], 'usage': {'prompt_tokens': 10, 'completion_tokens': 20}}]
        decision, deltas, _ = self._http_turn('deepseek', events)
        self.assertEqual(''.join(deltas), 'Inspecting. ')
        self.assertNotIn('DSML', ''.join(deltas))
        self.assertEqual(decision.tool_calls[0].name, 'repo.read_file')
        self.assertEqual(decision.tool_calls[0].arguments, {'path': 'README.md'})

    def test_native_tool_stream_keeps_arguments_and_signed_private_blocks(self):
        fixtures = {'openai': [{'type': 'response.function_call_arguments.delta', 'delta': '{"path":'}, {'type': 'response.completed', 'response': {'output': [{'type': 'reasoning', 'id': 'r1', 'encrypted_content': 'opaque'}, {'type': 'function_call', 'call_id': 'c1', 'name': 'repo_read_file', 'arguments': '{"path":"README.md"}'}]}}], 'anthropic': [{'type': 'message_start', 'message': {}}, {'type': 'content_block_start', 'index': 0, 'content_block': {'type': 'thinking', 'thinking': '', 'signature': ''}}, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'thinking_delta', 'thinking': 'private'}}, {'type': 'content_block_delta', 'index': 0, 'delta': {'type': 'signature_delta', 'signature': 'opaque'}}, {'type': 'content_block_start', 'index': 1, 'content_block': {'type': 'tool_use', 'id': 'c1', 'name': 'repo_read_file', 'input': {}}}, {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '{"path":'}}, {'type': 'content_block_delta', 'index': 1, 'delta': {'type': 'input_json_delta', 'partial_json': '"README.md"}'}}, {'type': 'message_delta', 'delta': {'stop_reason': 'tool_use'}}, {'type': 'message_stop'}], 'deepseek': [{'choices': [{'delta': {'reasoning_content': 'private'}}]}, {'choices': [{'delta': {'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'repo_read_file', 'arguments': '{"path":'}}]}}]}, {'choices': [{'delta': {'tool_calls': [{'index': 0, 'function': {'arguments': '"README.md"}'}}]}, 'finish_reason': 'tool_calls'}]}]}
        for provider, events in fixtures.items():
            with self.subTest(provider=provider):
                decision, deltas, _ = self._http_turn(provider, events)
                self.assertEqual(deltas, [])
                self.assertEqual(decision.tool_calls[0].call_id, 'c1')
                self.assertEqual(decision.tool_calls[0].name, 'repo.read_file')
                self.assertEqual(decision.tool_calls[0].arguments, {'path': 'README.md'})
                self.assertIn('opaque' if provider != 'deepseek' else 'private', json.dumps(decision.provider_items))

    def test_minimax_native_stream_keeps_split_reasoning_private_and_replayable(self):
        events = [{'choices': [{'delta': {'reasoning_content': 'private ', 'reasoning_details': [{'type': 'reasoning.text', 'id': 'reasoning-1', 'index': 0, 'text': 'private '}]}}]}, {'choices': [{'delta': {'reasoning_content': 'thought', 'reasoning_details': [{'id': 'reasoning-1', 'index': 0, 'text': 'thought'}], 'tool_calls': [{'index': 0, 'id': 'c1', 'function': {'name': 'repo_read_file', 'arguments': '{"path":"README.md"}'}}]}, 'finish_reason': 'tool_calls'}]}]
        decision, deltas, payload = self._http_turn('minimax', events)
        self.assertEqual(deltas, [])
        self.assertIs(payload['reasoning_split'], True)
        message = decision.provider_items[0]
        self.assertEqual(message['reasoning_content'], 'private thought')
        self.assertEqual(message['reasoning_details'][0]['text'], 'private thought')
        self.assertEqual(decision.tool_calls[0].call_id, 'c1')

    def test_partial_stream_failure_is_not_retried_or_switched(self):
        client = self._client('deepseek')
        deltas = []

        def attempt(*args, on_delta, **kwargs):
            on_delta('partial')
            raise LLMProviderError('connection lost', code='llm_read_error', retryable=True)
        with patch.object(client, '_count_tool_input_tokens', return_value=(10, 'test')), patch.object(client, '_decide_tools_once', side_effect=attempt) as request:
            with self.assertRaises(LLMProviderError) as caught:
                client.decide_tools([{'role': 'user', 'content': 'hello'}], [_tool_spec()], on_delta=deltas.append)
        self.assertEqual(request.call_count, 1)
        self.assertFalse(caught.exception.retryable)
        self.assertEqual(deltas, ['partial'])
        failure = caught.exception.route_trace['failures'][-1]
        self.assertEqual(failure['provider'], 'deepseek')
        self.assertTrue(failure['after_stream_start'])

    def test_missing_terminal_event_is_not_accepted_as_a_complete_answer(self):
        for provider in ('openai', 'anthropic', 'deepseek'):
            with self.subTest(provider=provider):
                events = self._text_events(provider, lambda _: None)
                cutoff = next((i for i, event in enumerate(events) if callable(event)))
                with self.assertRaises(LLMProviderError) as caught:
                    self._http_turn(provider, events[:cutoff])
                self.assertEqual(caught.exception.code, 'llm_stream_incomplete')
                self.assertFalse(caught.exception.retryable)

    def test_google_native_stream_preserves_parts_and_filters_thought_text(self):
        deltas = []
        client = self._client('google')

        def chunks(**kwargs):
            yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[types.Part(text='private', thought=True), types.Part(text='第一段')]))])
            self.assertEqual(deltas, ['第一段'])
            yield types.GenerateContentResponse(candidates=[types.Candidate(content=types.Content(parts=[types.Part(text='，第二段', thought_signature=b'signed')]), finish_reason='STOP')], usage_metadata=types.GenerateContentResponseUsageMetadata(prompt_token_count=10, candidates_token_count=6, thoughts_token_count=3))
        sdk_client = SimpleNamespace(models=SimpleNamespace(generate_content_stream=chunks), close=lambda: None)
        with patch('google.genai.Client', return_value=sdk_client):
            decision = client._decide_google_tools([{'role': 'user', 'content': 'hello'}], [_tool_spec()], {'repo_read_file': 'repo.read_file'}, 'stream-test', max_output_tokens=100, on_delta=deltas.append)
        self.assertEqual(decision.text, ''.join(deltas))
        self.assertEqual(decision.usage, LLMUsage(10, 6, 3))
        self.assertEqual(len(decision.provider_items[0]['parts']), 3)
        self.assertTrue(decision.provider_items[0]['parts'][-1]['thought_signature'])
