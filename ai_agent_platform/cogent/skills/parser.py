from __future__ import annotations
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal
import yaml
log = logging.getLogger(__name__)
VALID_NAME_RE = re.compile('^[a-z][a-z0-9\\-]*$')
VALID_MODES = {'inline'}
VALID_CONTEXTS = {'full', 'recent', 'none'}

class SkillParseError(Exception):
    pass

@dataclass
class SkillDef:
    name: str
    description: str
    prompt_body: str = ''
    mode: Literal['inline', 'fork'] = 'inline'
    model: str | None = None
    context: Literal['full', 'recent', 'none'] = 'full'
    source_path: Path | None = None
    is_directory: bool = False

def parse_frontmatter(raw: str) -> tuple[dict, str]:
    stripped = raw.lstrip()
    if not stripped.startswith('---'):
        raise SkillParseError('Missing YAML frontmatter (must start with ---)')
    end = stripped.find('---', 3)
    if end == -1:
        raise SkillParseError('Unclosed YAML frontmatter (missing closing ---)')
    yaml_block = stripped[3:end]
    body = stripped[end + 3:].lstrip('\n')
    try:
        meta = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        raise SkillParseError(f'Invalid YAML in frontmatter: {e}') from e
    if not isinstance(meta, dict):
        raise SkillParseError('Frontmatter must be a YAML mapping')
    return (meta, body)

def resolve_mode_and_context(meta: dict) -> tuple[str, str]:
    mode = meta.get('mode')
    context = meta.get('context', 'full')
    if context == 'fork':
        if not mode:
            mode = 'fork'
        context = 'full'
    return (mode or 'inline', context)

def _validate_meta(meta: dict, source: str='') -> None:
    ctx = f' in {source}' if source else ''
    if 'name' not in meta:
        raise SkillParseError(f"Missing required field 'name'{ctx}")
    if 'description' not in meta:
        raise SkillParseError(f"Missing required field 'description'{ctx}")
    name = meta['name']
    if not isinstance(name, str) or not VALID_NAME_RE.match(name):
        raise SkillParseError(f"Invalid skill name '{name}'{ctx}: must be lowercase letters, digits, and hyphens, starting with a letter")
    mode, context = resolve_mode_and_context(meta)
    if mode == 'fork':
        raise SkillParseError('unsupported_skill_execution: fork Skills are not supported')
    if mode not in VALID_MODES:
        raise SkillParseError(f"Invalid mode '{mode}'{ctx}: must be one of {VALID_MODES}")
    if context not in VALID_CONTEXTS:
        raise SkillParseError(f"Invalid context '{context}'{ctx}: must be one of {VALID_CONTEXTS}")

def parse_skill_file(path: Path) -> SkillDef:
    try:
        raw = path.read_text(encoding='utf-8')
    except OSError as e:
        raise SkillParseError(f'Cannot read skill file {path}: {e}') from e
    meta, body = parse_frontmatter(raw)
    _validate_meta(meta, str(path))
    mode, context = resolve_mode_and_context(meta)
    return SkillDef(name=meta['name'], description=meta['description'], prompt_body=body, mode=mode, model=meta.get('model'), context=context, source_path=path, is_directory=False)

def substitute_arguments(prompt_body: str, args: str) -> str:
    if '$ARGUMENTS' in prompt_body:
        return prompt_body.replace('$ARGUMENTS', args)
    if args.strip():
        return prompt_body + '\n\n## User Request\n\n' + args
    return prompt_body
