import hashlib
import subprocess

import pytest

from ai_agent_platform.cogent.filehistory.history import FileHistory, HistoryConflict
from ai_agent_platform.cogent.managed_files import ManagedFiles
from ai_agent_platform.integrations.execution_workspace import _patch_paths


@pytest.mark.parametrize('created', [True, False])
@pytest.mark.parametrize('name', ['empty.txt', 'empty file.txt', '空文件.txt'])
def test_empty_file_rewind_patch_changes_existence(tmp_path, created, name):
    before, after = ({}, {name: b''}) if created else ({name: b''}, {})
    history = FileHistory(str(tmp_path), 'empty')
    history.begin('run:empty', run_id='run', message_index=1, before=before)
    history.finish('run:empty', after=after)
    if created:
        (tmp_path / name).write_bytes(b'')
    preview = history.preview('run:empty', after)
    assert _patch_paths(preview['patch']) == {name}
    result = subprocess.run(['git', 'apply', '--no-index', '-'], cwd=tmp_path,
        input=preview['patch'], text=True, capture_output=True)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / name).exists() is not created


def test_new_file_patch_header_is_not_treated_as_missing_header():
    assert _patch_paths('--- /dev/null\n+++ b/new.txt\n@@ -0,0 +1 @@\n+new\n') == {'new.txt'}


def test_durable_history_before_after_creation_deletion_and_conflicts(tmp_path):
    history = FileHistory(str(tmp_path), 'session')
    history.begin('run:1', run_id='run', message_index=3, before={'existing.py': b'before\n', 'deleted.py': b'gone\n'})
    history = FileHistory(str(tmp_path), 'session')
    history.finish('run:1', after={'existing.py': b'after\n', 'new.py': b'new'})
    snapshot = FileHistory(str(tmp_path), 'session').get_snapshots()[0]
    assert snapshot.message_index == 3
    assert snapshot.backups['new.py'].before is None
    assert snapshot.backups['deleted.py'].after is None
    preview = history.preview(snapshot.id, {'existing.py': b'after\n', 'new.py': b'new'})
    assert '+before' in preview['patch']
    assert '+gone' in preview['patch']
    assert '-new' in preview['patch']
    assert 'No newline' in preview['patch']
    with pytest.raises(HistoryConflict, match='outside the Agent'):
        history.preview(snapshot.id, {'existing.py': b'user edit', 'new.py': b'new'})
    history.finish('run:1', after={})
    assert len(history.get_snapshots()) == 1


def test_latest_hundred_snapshots_and_original_target_across_edits(tmp_path):
    history = FileHistory(str(tmp_path), 'session')
    for i in range(102):
        history.begin(str(i), run_id='run', message_index=i, before={'a': str(i).encode()})
        history.finish(str(i), after={'a': str(i+1).encode()})
    assert len(history.get_snapshots()) == 100
    assert history.get_snapshots()[0].id == '2'
    preview = history.preview('2', {'a': b'102'})
    assert preview['target_hashes']['a'] == hashlib.sha256(b'2').hexdigest()


def test_managed_files_refuse_symlinked_parent_and_traversal(tmp_path):
    workspace = tmp_path / 'workspace'
    workspace.mkdir()
    outside = tmp_path / 'outside'
    outside.mkdir()
    (workspace / '.cogent').symlink_to(outside, target_is_directory=True)
    managed = ManagedFiles(workspace / '.cogent' / 'memory')
    with pytest.raises(OSError):
        managed.write('test.md', b'data')
    assert not list(outside.iterdir())
    with pytest.raises(ValueError):
        ManagedFiles(workspace).write('../escape', b'no')
