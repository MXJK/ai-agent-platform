from __future__ import annotations
import logging
import os
from pathlib import Path
import yaml
from ai_agent_platform.cogent.commands.registry import Command, CommandContext, CommandType
log = logging.getLogger(__name__)

def _split_frontmatter(content: str) -> tuple[dict, str]:
    stripped = content.lstrip()
    if not stripped.startswith('---'):
        return ({}, content)
    parts = stripped.split('---', 2)
    if len(parts) < 3:
        return ({}, content)
    try:
        meta = yaml.safe_load(parts[1])
    except yaml.YAMLError:
        return ({}, content)
    if not isinstance(meta, dict):
        return ({}, content)
    return (meta, parts[2])

def _first_non_header_line(body: str) -> str:
    for line in body.split('\n'):
        line = line.strip()
        if line and (not line.startswith('#')):
            return line
    return ''

def _make_prompt_handler(body: str):

    async def handler(ctx: CommandContext) -> None:
        if '$ARGUMENTS' in body:
            result = body.replace('$ARGUMENTS', ctx.args)
        elif ctx.args.strip():
            result = body + '\n\n## User Request\n\n' + ctx.args
        else:
            result = body
        ctx.ui.send_user_message(result)
    return handler

def load_dir(directory: str) -> list[Command]:
    if not directory:
        return []
    dir_path = Path(directory)
    if not dir_path.is_dir():
        return []
    commands: list[Command] = []
    for root, _dirs, files in os.walk(directory):
        for fname in sorted(files):
            if not fname.endswith('.md'):
                continue
            fpath = Path(root) / fname
            cmd = _parse_command_file(dir_path, fpath)
            if cmd is not None:
                commands.append(cmd)
    return commands

def _parse_command_file(base_dir: Path, path: Path) -> Command | None:
    try:
        data = path.read_text(encoding='utf-8')
    except OSError:
        return None
    try:
        rel = path.relative_to(base_dir)
    except ValueError:
        return None
    parts = list(rel.parts)
    parts[-1] = parts[-1].removesuffix('.md')
    parts = [p.lower().replace(' ', '-') for p in parts]
    name = ':'.join(parts)
    if not name:
        return None
    meta, body = _split_frontmatter(data)
    body = body.strip()
    description = meta.get('description', '')
    if not description:
        description = _first_non_header_line(body)
    aliases = meta.get('aliases', [])
    if not isinstance(aliases, list):
        aliases = []
    arg_prompt = meta.get('argument-hint', '')
    return Command(name=name, description=description or f'Custom command: {name}', type=CommandType.PROMPT, handler=_make_prompt_handler(body), aliases=aliases, arg_prompt=arg_prompt)

def load_user_commands(work_dir: str) -> list[Command]:
    dirs: list[str] = []
    home = Path.home()
    dirs.append(str(home / '.cogent' / 'commands'))
    dirs.append(str(Path(work_dir) / '.cogent' / 'commands'))
    merged: dict[str, Command] = {}
    order: list[str] = []
    for d in dirs:
        for cmd in load_dir(d):
            if cmd.name not in merged:
                order.append(cmd.name)
            merged[cmd.name] = cmd
    return [merged[n] for n in order]
