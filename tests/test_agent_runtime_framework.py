from dataclasses import replace
import unittest
from ai_agent_platform.agents.coding.models import AgentRunRecord, AgentRunResult
from ai_agent_platform.agents.coding.run_artifacts import RUN_ARTIFACT_READ_TOOL
from ai_agent_platform.agents.coding.store import InMemoryAgentRunStore
from ai_agent_platform.integrations.tools import ToolCall

class AgentRuntimeFrameworkTests(unittest.TestCase):

    def test_in_memory_store_loads_latest_run_for_conversation(self) -> None:
        store = InMemoryAgentRunStore()
        base = AgentRunRecord(run_id='run_first', thread_id='run_first', conversation_id='session_1', workspace_id='workspace_main', workspace_root='/workspace', status='queued', checkpoint_id=None, latest_node=None, next_nodes=['setup_workspace'], trace=[])
        store.save(base)
        store.save(replace(base, run_id='run_other', conversation_id='session_2'))
        store.save(replace(base, run_id='run_latest', thread_id='run_latest'))
        latest = store.get_latest_for_conversation('session_1')
        assert latest is not None
        self.assertEqual(latest.run_id, 'run_latest')
        self.assertIsNone(store.get_latest_for_conversation('missing'))
        self.assertEqual([record.run_id for record in store.list_recent(limit=2)], ['run_latest', 'run_other'])

    def test_terminal_result_projects_tool_calls_and_results_into_audit_events(self) -> None:
        store = InMemoryAgentRunStore()
        base = AgentRunRecord(run_id='run_tools', thread_id='run_tools', conversation_id='session_1', workspace_id='workspace_main', workspace_root='/workspace', status='queued', checkpoint_id=None, latest_node=None, next_nodes=['setup_workspace'], trace=[])
        store.save(base)
        result = AgentRunResult(run_id=base.run_id, thread_id=base.thread_id, conversation_id=base.conversation_id, workspace_id=base.workspace_id, status='completed', checkpoint_id='checkpoint_done', role='coding agent', objective='inspect', intent='repository_question', context_route='repo', selected_knowledge_base_ids=[], answer='done', graph_engine='langgraph', context_sources=[], tool_calls=[ToolCall(name='repo.read_file', arguments={'path': 'app.py'}, call_id='call_read', source='model'), ToolCall(name=RUN_ARTIFACT_READ_TOOL, arguments={'artifact_id': 'tool_result_1234567890abcdef1234', 'offset_chars': 0, 'max_tokens': 128}, call_id='call_artifact_read', source='model')], tool_results=[{'call_id': 'call_read', 'name': 'repo.read_file', 'ok': True, 'result': {'content': 'VALUE = 1'}}, {'call_id': 'call_artifact_read', 'name': RUN_ARTIFACT_READ_TOOL, 'ok': True, 'result': {'artifact_id': 'tool_result_1234567890abcdef1234', 'view': 'page', 'returned_chars': 16, 'estimated_tokens': 4, 'sha256': 'abc123', 'ranges': [{'start_char': 0, 'end_char': 16, 'content': 'protected-value'}]}}], trace=[])
        store.save(replace(base, status='completed', latest_node='compose_answer', next_nodes=[], result=result))
        events = store.list_events(base.run_id)
        types = [event.type for event in events]
        self.assertLess(types.index('tool_selected'), types.index('tool_result'))
        self.assertLess(types.index('tool_result'), types.index('run_completed'))
        selected = next((event for event in events if event.type == 'tool_selected'))
        completed = next((event for event in events if event.type == 'tool_result'))
        artifact_read = next((event for event in events if event.type == 'tool_result' and event.output.get('name') == RUN_ARTIFACT_READ_TOOL))
        self.assertEqual(selected.output['arguments'], {'path': 'app.py'})
        self.assertEqual(completed.output['result']['content'], 'VALUE = 1')
        self.assertEqual(artifact_read.output['result']['artifact_id'], 'tool_result_1234567890abcdef1234')
        self.assertNotIn('protected-value', str(artifact_read.output))
        self.assertEqual(artifact_read.output['result']['ranges'], [{'start_char': 0, 'end_char': 16}])

    def test_terminal_run_cannot_be_overwritten_by_stale_active_snapshot(self) -> None:
        store = InMemoryAgentRunStore()
        terminal = AgentRunRecord(run_id='run_terminal', thread_id='run_terminal', conversation_id='session_1', workspace_id='workspace_main', workspace_root='/workspace', status='failed', checkpoint_id='failed-checkpoint', latest_node='runtime', next_nodes=[], trace=[], error='original failure')
        store.save(terminal)
        store.save(replace(terminal, status='running', checkpoint_id='stale-checkpoint', error=None))
        self.assertEqual(store.get(terminal.run_id), terminal)

    def test_repeated_suspension_transitions_keep_distinct_events(self) -> None:
        store = InMemoryAgentRunStore()
        record = AgentRunRecord(run_id='run_repeated_pause', thread_id='thread_repeated_pause', conversation_id='sess_repeated_pause', workspace_id='workspace_main', workspace_root='.', status='paused', checkpoint_id='checkpoint-one', latest_node='native_tool_loop', next_nodes=[], trace=[])
        store.save(record)
        store.save(replace(record, status='running', checkpoint_id='checkpoint-between'))
        store.save(replace(record, checkpoint_id='checkpoint-two'))
        pause_events = [event for event in store.list_events(record.run_id) if event.type == 'run_paused']
        self.assertEqual(len(pause_events), 2)
        self.assertLess(pause_events[0].sequence, pause_events[1].sequence)
