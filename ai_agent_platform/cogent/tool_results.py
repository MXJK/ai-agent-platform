from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .managed_files import ManagedFiles


class ToolResultFiles:
    def __init__(self, workspace_root: str, references: dict):
        self.files = ManagedFiles(Path(workspace_root).resolve())
        self.references = references

    def persist(self, run_id: str, response: dict) -> dict:
        if not run_id or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in run_id):
            raise ValueError('Invalid tool result run ID')
        raw = json.dumps(response, ensure_ascii=False, indent=2, default=str).encode()
        digest = hashlib.sha256(raw).hexdigest()
        key = hashlib.sha256(str(response.get('call_id') or 'result').encode()).hexdigest()
        relative = f'.cogent/sessions/{run_id}/tool-results/{key}.json'
        existing = self.files.read(relative, limit=max(len(raw), 8_000_000))
        if existing is not None and existing != raw:
            raise ValueError('A persisted tool result changed unexpectedly')
        if existing is None:
            self.files.write(relative, raw)
        self.references[relative] = {'sha256': digest, 'bytes': len(raw)}
        return {'persisted': True, 'path': relative, 'preview': raw[:2000].decode(errors='ignore'),
                'chars': len(raw.decode()), 'sha256': digest}

    def relative(self, path: str) -> str | None:
        candidate = Path(path)
        if candidate.is_absolute():
            try:
                candidate = candidate.relative_to(self.files.root)
            except ValueError:
                return None
        relative = candidate.as_posix()
        if relative not in self.references:
            return None
        return relative

    def read(self, path: str, *, offset=0, limit=2000) -> dict:
        relative = self.relative(path)
        if relative is None:
            raise PermissionError('The tool result is not in this conversation')
        ref = self.references[relative]
        raw = self.files.read(relative, limit=int(ref['bytes']))
        if raw is None or hashlib.sha256(raw).hexdigest() != ref['sha256']:
            raise ValueError('The persisted tool result is missing or has changed')
        lines = raw.decode().splitlines(keepends=True)
        offset, limit = max(0, int(offset)), max(1, min(int(limit), 2000))
        content = ''.join(lines[offset:offset + limit])[:50_000]
        return {'path': relative, 'content': content, 'start_line': offset + 1,
                'total_lines': len(lines), 'truncated': len(content) < len(''.join(lines[offset:]))}
