import asyncio

from fastapi.testclient import TestClient
from textual.widgets import Collapsible, Markdown, Static

from ai_agent_platform.cli import CliApplication, build_parser
from ai_agent_platform.cogent.tui import CogentApp
from ai_agent_platform.cogent.widgets import ChatInput, ToolCallBlock
from ai_agent_platform.domain import AgentEvent
from test_cogent_api import app_for


def test_cli_defaults_to_tui_and_supports_print():
    parser = build_parser()
    assert parser.parse_args([]).mode == 'tui'
    assert parser.parse_args(['--print', 'hello', 'world']).print_message == ['hello', 'world']
    assert parser.parse_args([
        '--permission-mode', 'bypassPermissions'
    ]).permission_mode == 'bypassPermissions'


def test_tui_shared_commands_streaming_and_collapsed_displayable_thinking(tmp_path):
    api = app_for(tmp_path)
    with TestClient(api):
        application = CliApplication(api.state.runtime, workspace_root=tmp_path, workspace_id='cli-test')

        async def scenario():
            app = CogentApp(application)
            async with app.run_test(size=(100, 40)) as pilot:
                await pilot.pause()
                assert "Cogent" in str(app.query_one("#title-bar", Static).content)
                assert app.query_one("#chat-input", ChatInput).placeholder == "Send a message…"
                assert "embedded runtime" in str(
                    app.query_one("#model-label", Static).content
                )
                assert any(item['name'] == 'help' for item in app.capabilities['commands'])
                assert 'mode: default' in str(app.query_one('#mode-label', Static).content)
                await app.submit('/permissions acceptEdits')
                await app.workers.wait_for_complete()
                assert application.permission_mode == 'acceptEdits'
                assert 'mode: acceptEdits' in str(app.query_one('#mode-label', Static).content)
                await pilot.press('shift+tab')
                assert application.permission_mode == 'plan'
                await app.submit('/permissions bypassPermissions')
                await app.workers.wait_for_complete()
                assert application.permission_mode == 'bypassPermissions'
                await app.submit('/models')
                assert any(
                    "embedded test runtime" in str(item.content)
                    for item in app.query(".system-message")
                )
                await app.submit('/help')
                await app.workers.wait_for_complete()
                assert app.run_status == 'completed'
                assert '/permissions' in app.answer_text[app.active_run_id]
                await app.submit('Hello Cogent')
                await app.workers.wait_for_complete()
                run = app.active_run_id
                assert api.state.runtime.coding_agent_runtime.get_run(
                    run
                ).runtime_state['permission_mode'] == 'bypassPermissions'
                assert 'fake model reply' in app.answer_text[run]
                assert isinstance(app.answers[run], Markdown)
                await app.render_event(AgentEvent(999, run, 'running', 'thinking_delta', '', {'text': 'Visible summary'}))
                await app.render_event(AgentEvent(1000, run, 'running', 'thinking_completed', '', {'text': 'Visible summary', 'signature': 'never show'}))
                panel = app.thinking[run]
                assert isinstance(panel, Collapsible) and panel.collapsed
                assert app.thinking_text[run] == 'Visible summary'
                assert 'never show' not in str(panel.query_one('.thinking-content', Static).content)
                await app.render_event(AgentEvent(1001, run, 'running', 'tool_result', '', {'call_id':'rejected', 'name':'[red]tool', 'ok':False, 'error':'[bold]not markup'}))
                block = app.tools[(run, 'rejected')]
                block.on_click()
                assert '[bold]not markup' in block._full_output
                await app.submit('/unknown-command')
                assert not app.busy
                assert 'Unknown or unavailable' in str(app.query_one('#activity', Static).content)

        asyncio.run(scenario())


def test_history_does_not_follow_workspace_symlink(tmp_path):
    outside = tmp_path / 'outside'
    outside.mkdir()
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    (workspace / '.cogent').symlink_to(outside, target_is_directory=True)
    widget = ChatInput()
    widget.load_history(str(workspace))
    widget._persist_entry('private input')
    assert not (outside / 'history').exists()
