import ast
from pathlib import Path


ROOT = Path(__file__).parents[1]


def imports(path):
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            yield from (item.name for item in node.names)
        elif isinstance(node, ast.ImportFrom):
            yield node.module or ''


def test_production_has_no_retired_graph_imports():
    violations = [(str(path.relative_to(ROOT)), module)
        for path in (ROOT / 'ai_agent_platform').rglob('*.py')
        for module in imports(path)
        if module.startswith('langgraph') or module == 'ai_agent_platform.agents.coding_agent']
    assert violations == []
    for path in ['pyproject.toml', 'requirements.txt', 'requirements.self-hosted.txt', '.env.example', 'docker-compose.yml']:
        assert 'langgraph' not in (ROOT / path).read_text().lower()


def test_cogent_has_no_retrieval_or_legacy_memory_imports():
    excluded = ['ai_agent_platform.rag', 'ai_agent_platform.knowledge',
        'ai_agent_platform.project_memory', 'ai_agent_platform.user_memory']
    for path in (ROOT / 'ai_agent_platform/cogent').rglob('*.py'):
        assert not any(module.startswith(tuple(excluded)) for module in imports(path)), path
    query = (ROOT / 'ai_agent_platform/services/query_service.py').read_text()
    assert 'CodingAgentRuntime' not in query
    assert 'from ai_agent_platform.cogent.protocol import AgentRuntime' in query


def test_cogent_state_and_prompt_have_no_agent_knowledge_routing():
    for name in ['state.py', 'prompts.py']:
        source = (ROOT / 'ai_agent_platform/cogent' / name).read_text()
        for retired in ['context_route', 'selected_knowledge_base_ids', 'rag_context', 'project_memory_sources']:
            assert retired not in source
