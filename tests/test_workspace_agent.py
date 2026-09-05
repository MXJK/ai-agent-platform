from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from ai_agent_platform.agents.coding.context import load_project_instructions
from ai_agent_platform.integrations.tools import ToolCall, ToolExecutionContext
from ai_agent_platform.repositories import InMemoryWorkspaceRepository
from ai_agent_platform.services import WorkspaceNotFoundError, WorkspaceRootConflictError, WorkspaceService, WorkspaceValidationError
from ai_agent_platform.tools import register_repository_tools
from ai_agent_platform.integrations.tools import ToolRegistry

class WorkspaceServiceTests(unittest.TestCase):

    def test_translates_concurrent_unique_root_conflict(self) -> None:

        class UniqueViolation(Exception):
            sqlstate = '23505'

        class RacingStore(InMemoryWorkspaceRepository):

            def upsert(self, *, workspace_id: str, root_path: str):
                raise UniqueViolation('unique root')
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            service = WorkspaceService(store=RacingStore(), allowed_roots=(str(root),))
            with self.assertRaisesRegex(WorkspaceRootConflictError, 'already registered'):
                service.register(workspace_id='racing', root_path=str(root))

    def test_update_only_affects_new_root_snapshots_and_isolates_workspaces(self) -> None:
        with TemporaryDirectory() as temp_dir:
            allowed = Path(temp_dir)
            first = allowed / 'first'
            second = allowed / 'second'
            first.mkdir()
            second.mkdir()
            service = WorkspaceService(store=InMemoryWorkspaceRepository(), allowed_roots=(str(allowed),))
            service.register(workspace_id='alpha', root_path=str(first))
            old_snapshot = service.resolve_for_run('alpha')
            service.register(workspace_id='alpha', root_path=str(second))
            service.register(workspace_id='beta', root_path=str(first))
            self.assertEqual(old_snapshot, str(first.resolve()))
            self.assertEqual(service.resolve_for_run('alpha'), str(second.resolve()))
            self.assertEqual(service.resolve_for_run('beta'), str(first.resolve()))

    def test_rejects_root_outside_allowed_boundary(self) -> None:
        with TemporaryDirectory() as allowed_dir, TemporaryDirectory() as outside_dir:
            service = WorkspaceService(store=InMemoryWorkspaceRepository(), allowed_roots=(allowed_dir,))
            with self.assertRaises(WorkspaceValidationError):
                service.register(workspace_id='outside', root_path=outside_dir)

    def test_remove_hides_workspace_and_reregister_restores_it(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / 'project'
            workspace.mkdir()
            service = WorkspaceService(store=InMemoryWorkspaceRepository(), allowed_roots=(str(root),))
            created = service.register(workspace_id='project', root_path=str(workspace))
            removed = service.remove('project')
            self.assertIsNotNone(removed.removed_at)
            self.assertEqual(service.list(), [])
            with self.assertRaises(WorkspaceNotFoundError):
                service.get('project')
            with self.assertRaises(WorkspaceNotFoundError):
                service.resolve_for_run('project')
            restored = service.register(workspace_id='project', root_path=str(workspace))
            self.assertIsNone(restored.removed_at)
            self.assertEqual(restored.created_at, created.created_at)
            self.assertEqual(restored.revision, created.revision)

    def test_purge_ephemeral_leaves_no_workspace_tombstone(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / 'eval'
            workspace.mkdir()
            service = WorkspaceService(store=InMemoryWorkspaceRepository(), allowed_roots=(str(root),))
            service.register(workspace_id='eval', root_path=str(workspace))
            self.assertTrue(service.purge_ephemeral('eval'))
            self.assertEqual(service.list_including_removed(), [])

    def test_reports_registered_workspace_path_availability(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / 'project'
            workspace.mkdir()
            service = WorkspaceService(store=InMemoryWorkspaceRepository(), allowed_roots=(str(root),))
            service.register(workspace_id='project', root_path=str(workspace))
            self.assertTrue(service.is_available('project'))
            workspace.rmdir()
            self.assertFalse(service.is_available('project'))

class RepositoryToolTests(unittest.TestCase):

    def test_find_search_and_line_read_use_live_workspace(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            source = root / 'src' / 'service.py'
            source.parent.mkdir()
            source.write_text('first = 1\nclass WorkspaceService:\n    pass\nlast = 4\n', encoding='utf-8')
            registry = ToolRegistry()
            register_repository_tools(registry)
            context = ToolExecutionContext(conversation_id='session', workspace_id='workspace', workspace_root=str(root))
            found = registry.execute(ToolCall(name='repo.find_files', arguments={'query': 'service.py'}), context=context)
            searched = registry.execute(ToolCall(name='repo.search_code', arguments={'query': 'WorkspaceService'}), context=context)
            read = registry.execute(ToolCall(name='repo.read_file', arguments={'path': 'src/service.py', 'start_line': 2, 'end_line': 3, 'max_chars': 100}), context=context)
            self.assertEqual(found.result['matches'], ['src/service.py'])
            self.assertEqual(searched.result['matches'][0]['line'], 2)
            self.assertEqual(read.result['start_line'], 2)
            self.assertEqual(read.result['end_line'], 3)
            self.assertIn('WorkspaceService', read.result['content'])
            self.assertEqual(len(read.result['content_hash']), 64)
            source.write_text('UPDATED = True\n', encoding='utf-8')
            latest = registry.execute(ToolCall(name='repo.read_file', arguments={'path': 'src/service.py'}), context=context)
            self.assertIn('UPDATED', latest.result['content'])

    def test_rejects_sensitive_binary_large_and_symlink_escape(self) -> None:
        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as outside_dir:
            root = Path(temp_dir)
            (root / '.env').write_text('SECRET=value', encoding='utf-8')
            (root / 'binary.dat').write_bytes(b'\x00binary')
            outside = Path(outside_dir) / 'secret.txt'
            outside.write_text('secret', encoding='utf-8')
            (root / 'link.txt').symlink_to(outside)
            registry = ToolRegistry()
            register_repository_tools(registry)
            context = ToolExecutionContext('session', 'workspace', str(root))
            for path in ('.env', 'binary.dat', 'link.txt'):
                result = registry.execute(ToolCall(name='repo.read_file', arguments={'path': path}), context=context)
                self.assertFalse(result.ok, path)

    def test_list_files_skips_ignored_directories_and_symlink_escapes(self) -> None:
        with TemporaryDirectory() as temp_dir, TemporaryDirectory() as outside_dir:
            root = Path(temp_dir)
            (root / 'README.md').write_text('# Demo\n', encoding='utf-8')
            ignored = root / '.venv-backup' / 'bin'
            ignored.mkdir(parents=True)
            (ignored / 'python').symlink_to(Path(outside_dir) / 'python')
            outside = Path(outside_dir) / 'secret.txt'
            outside.write_text('secret', encoding='utf-8')
            (root / 'escape-link.txt').symlink_to(outside)
            registry = ToolRegistry()
            register_repository_tools(registry)
            context = ToolExecutionContext('session', 'workspace', str(root))
            result = registry.execute(ToolCall(name='repo.list_files', arguments={}), context=context)
        self.assertTrue(result.ok)
        self.assertEqual(result.result['files'], ['README.md'])
        self.assertFalse(result.result['truncated'])

    def test_search_falls_back_when_ripgrep_is_unavailable(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'app.xyz').write_text('needle = 1\n', encoding='utf-8')
            registry = ToolRegistry()
            register_repository_tools(registry)
            context = ToolExecutionContext('session', 'workspace', str(root))
            with patch('ai_agent_platform.tools.repository.shutil.which', return_value=None):
                result = registry.execute(ToolCall(name='repo.search_code', arguments={'query': 'needle'}), context=context)
            self.assertTrue(result.ok)
            self.assertEqual(result.result['engine'], 'python')
            self.assertEqual(result.result['matches'][0]['path'], 'app.xyz')

class ProjectInstructionTests(unittest.TestCase):

    def test_nested_override_and_multi_directory_scopes(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'AGENTS.md').write_text('root rules', encoding='utf-8')
            backend = root / 'backend'
            frontend = root / 'frontend'
            backend.mkdir()
            frontend.mkdir()
            (backend / 'AGENTS.md').write_text('ignored backend', encoding='utf-8')
            (backend / 'AGENTS.override.md').write_text('backend override', encoding='utf-8')
            (frontend / 'AGENTS.md').write_text('frontend rules', encoding='utf-8')
            (backend / 'app.py').write_text('', encoding='utf-8')
            (frontend / 'app.js').write_text('', encoding='utf-8')
            sources = load_project_instructions(workspace_root=str(root), focus_files=['backend/app.py', 'frontend/app.js'], max_chars=16000)
            self.assertEqual([source.path for source in sources], ['AGENTS.md', 'backend/AGENTS.override.md', 'frontend/AGENTS.md'])
            self.assertNotIn('ignored backend', [source.text for source in sources])
            self.assertIn('backend', sources[1].reason)
            self.assertIn('frontend', sources[2].reason)

    def test_instruction_budget_is_enforced(self) -> None:
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / 'AGENTS.md').write_text('x' * 200, encoding='utf-8')
            sources = load_project_instructions(workspace_root=str(root), focus_files=[], max_chars=32)
            self.assertEqual(sum((len(item.text) for item in sources)), 32)
            self.assertTrue(sources[0].truncated)
