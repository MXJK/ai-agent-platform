from __future__ import annotations

from dataclasses import dataclass, field
import difflib
import hashlib
import json
from pathlib import Path, PurePosixPath
import time

from ..managed_files import ManagedFiles

MAX_SNAPSHOTS = 100


class HistoryConflict(ValueError):
    pass


@dataclass
class Backup:
    before: str | None
    after: str | None


@dataclass
class Snapshot:
    id: str
    message_index: int
    run_id: str
    backups: dict[str, Backup] = field(default_factory=dict)
    timestamp: float = 0.0
    checkpoint_id: str | None = None


class FileHistory:
    """Only the execution-workspace snapshotter may supply file bytes to this journal."""

    def __init__(self, base_dir: str, session_id: str):
        self.root = Path(base_dir).resolve(strict=True)
        key = hashlib.sha256(session_id.encode()).hexdigest()
        self.files = ManagedFiles(self.root / '.cogent' / 'file-history' / key)

    def _load(self):
        raw = self.files.read('history.json', limit=32_000_000)
        return json.loads(raw) if raw else {'version': 1, 'snapshots': [], 'pending': {}}

    def _save(self, state):
        self.files.write('history.json', json.dumps(state, ensure_ascii=False).encode())

    def _store_bytes(self, files: dict[str, bytes]):
        result = {}
        for path, data in files.items():
            self._validate_path(path)
            digest = hashlib.sha256(data).hexdigest()
            if self.files.read('blobs/' + digest) is None:
                self.files.write('blobs/' + digest, data)
            result[path] = digest
        return result

    @staticmethod
    def _validate_path(path):
        relative = PurePosixPath(path)
        if any(char in path for char in '\x00\n\r\t'):
            raise ValueError('History path contains unsupported control characters')
        if relative.is_absolute() or '..' in relative.parts or not relative.parts:
            raise ValueError('History path must be workspace-relative')
        if '.git' in relative.parts or relative.parts[:1] == ('.cogent',) and relative.parts[1:2] != ('plans',):
            raise ValueError('History cannot mutate protected Cogent or Git state')

    def begin(self, operation: str, *, run_id: str, message_index: int, before: dict[str, bytes], checkpoint_id: str | None = None):
        with self.files.lock('.lock'):
            state = self._load()
            if any(item['id'] == operation for item in state['snapshots']):
                return
            if operation not in state['pending']:
                state['pending'][operation] = dict(id=operation, run_id=run_id, message_index=message_index,
                    before=self._store_bytes(before), timestamp=time.time(), checkpoint_id=checkpoint_id)
                self._save(state)

    def finish(self, operation: str, *, after: dict[str, bytes]) -> str:
        with self.files.lock('.lock'):
            state = self._load()
            if any(item['id'] == operation for item in state['snapshots']):
                return operation
            item = state['pending'].pop(operation)
            before = item.pop('before')
            post = self._store_bytes(after)
            item['backups'] = {path: {'before': before.get(path), 'after': post.get(path)}
                for path in sorted(before.keys() | post.keys()) if before.get(path) != post.get(path)}
            state['snapshots'].append(item)
            state['snapshots'] = state['snapshots'][-MAX_SNAPSHOTS:]
            self._save(state)
            return operation

    def get_snapshots(self) -> list[Snapshot]:
        return [Snapshot(**{**item, 'backups': {path: Backup(**value) for path, value in item['backups'].items()}})
                for item in self._load()['snapshots']]

    def has_snapshots(self):
        return bool(self.get_snapshots())

    def _blob(self, digest):
        if digest is None:
            return None
        if len(digest) != 64 or any(char not in '0123456789abcdef' for char in digest):
            raise ValueError('Invalid history hash')
        data = self.files.read('blobs/' + digest)
        if data is None or hashlib.sha256(data).hexdigest() != digest:
            raise HistoryConflict('File history data is missing or has changed')
        return data

    def preview(self, snapshot_id: str, current: dict[str, bytes]):
        snapshots = self.get_snapshots()
        index = next((i for i, item in enumerate(snapshots) if item.id == snapshot_id), None)
        if index is None:
            raise ValueError('Unknown or expired file snapshot')
        target, expected = {}, {}
        for item in snapshots[index:]:
            for path, backup in item.backups.items():
                self._validate_path(path)
                target.setdefault(path, backup.before)
                expected[path] = backup.after
        conflicts = [path for path, digest in expected.items()
                     if (hashlib.sha256(current[path]).hexdigest() if path in current else None) != digest]
        if conflicts:
            raise HistoryConflict('Files changed outside the Agent history: ' + ', '.join(sorted(conflicts)))
        patch = []
        for path, digest in target.items():
            before, after = current.get(path), self._blob(digest)
            if before == after:
                continue
            try:
                left = before.decode('utf-8') if before is not None else ''
                right = after.decode('utf-8') if after is not None else ''
            except UnicodeDecodeError as exc:
                raise HistoryConflict(f'Binary rewind is not supported: {path}') from exc
            if left == right == '':
                header = f'diff --git {json.dumps("a/" + path, ensure_ascii=False)} {json.dumps("b/" + path, ensure_ascii=False)}\n'
                patch.append(header + ('new file mode 100644\nindex 0000000..e69de29\n'
                    if before is None else 'deleted file mode 100644\nindex e69de29..0000000\n'))
                continue
            for line in difflib.unified_diff(left.splitlines(keepends=True), right.splitlines(keepends=True),
                    fromfile=f'a/{path}' if before is not None else '/dev/null',
                    tofile=f'b/{path}' if after is not None else '/dev/null'):
                patch.append(line if line.endswith('\n') else line + '\n\\ No newline at end of file\n')
        return {'snapshot_id': snapshot_id, 'run_id': snapshots[index].run_id,
                'checkpoint_id': snapshots[index].checkpoint_id,
                'message_index': snapshots[index].message_index, 'expected_hashes': expected,
                'target_hashes': target, 'patch': ''.join(patch)}
