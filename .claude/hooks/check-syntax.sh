#!/usr/bin/env bash
# PostToolUse(Edit|Write): fast, side-effect-free syntax check on edited Python
# files, plus session markers telling the Stop hook what verification to run.
# Written for bash 3.2 (macOS default): no mapfile, no associative arrays.
set -uo pipefail

payload="$(cat)"
state_dir="${TMPDIR:-/tmp}/claude-verify"
mkdir -p "$state_dir"

field() {
  printf '%s' "$payload" | /usr/bin/python3 -c "
import json, sys
data = json.load(sys.stdin)
if '$1' == 'session':
    print(data.get('session_id', 'nosession'))
else:
    print(data.get('tool_input', {}).get('file_path', ''))
" 2>/dev/null
}

session="$(field session)"
file="$(field file)"
[ -n "$file" ] || exit 0

# Anything under the package can change what the running app serves.
case "$file" in
  */ai_agent_platform/*) touch "$state_dir/$session.app" ;;
esac

case "$file" in
  *.py) touch "$state_dir/$session.tests" ;;
  *) exit 0 ;;
esac

[ -f "$file" ] || exit 0

py="${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python"
[ -x "$py" ] || py=/usr/bin/python3

if ! out="$("$py" -c '
import ast, sys

path = sys.argv[1]
try:
    ast.parse(open(path, encoding="utf-8").read(), filename=path)
except SyntaxError as exc:
    print("%s:%s:%s: %s" % (path, exc.lineno, exc.offset, exc.msg))
    if exc.text:
        print("    " + exc.text.rstrip())
    sys.exit(1)
' "$file" 2>&1)"; then
  printf 'Syntax error, fix before continuing:\n%s\n' "$out" >&2
  exit 2
fi
exit 0
