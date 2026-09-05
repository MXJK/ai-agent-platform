from __future__ import annotations
import re
from dataclasses import dataclass

@dataclass
class MemoryFile:
    path: str = ''
    name: str = ''
    description: str = ''
    type: str = ''

def parse_frontmatter(content: str) -> MemoryFile:
    mf = MemoryFile()
    m = _FRONTMATTER_RE.match(content)
    if not m:
        return mf
    for line in m.group(1).split('\n'):
        colon = line.find(':')
        if colon < 0:
            continue
        key = line[:colon].strip()
        val = line[colon + 1:].strip()
        if len(val) >= 2 and (val.startswith('"') and val.endswith('"') or (val.startswith("'") and val.endswith("'"))):
            val = val[1:-1]
        if key == 'name':
            mf.name = val
        elif key == 'description':
            mf.description = val
        elif key == 'type' and val in VALID_TYPES:
            mf.type = val
    return mf
MAX_ENTRYPOINT_LINES = 200
MAX_ENTRYPOINT_BYTES = 25000
VALID_TYPES = {'user', 'feedback', 'project', 'reference'}
_FRONTMATTER_RE = re.compile('\\A---\\s*\\n(.*?)\\n---\\s*\\n', re.DOTALL)
