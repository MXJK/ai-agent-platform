#!/usr/bin/env bash
# Stop: verify before the turn ends, but only pay for what this session touched.
#   *.tests marker -> run the pytest suite (~45s)
#   *.app   marker -> confirm the hot-reloaded app container is still healthy
# Exit 2 blocks the stop and shows stderr to Claude. A consecutive-failure
# counter releases the stop after 3 attempts so a red suite cannot trap the
# session in a loop.
set -uo pipefail

payload="$(cat)"
state_dir="${TMPDIR:-/tmp}/claude-verify"
mkdir -p "$state_dir"

session="$(printf '%s' "$payload" | /usr/bin/python3 -c \
  'import json,sys; print(json.load(sys.stdin).get("session_id","nosession"))' 2>/dev/null)"
[ -n "$session" ] || session=nosession

tests_marker="$state_dir/$session.tests"
app_marker="$state_dir/$session.app"
block_marker="$state_dir/$session.blocks"

# Nothing relevant changed this turn: cost nothing.
[ -f "$tests_marker" ] || [ -f "$app_marker" ] || exit 0

blocks="$(cat "$block_marker" 2>/dev/null || echo 0)"
case "$blocks" in ''|*[!0-9]*) blocks=0 ;; esac
if [ "$blocks" -ge 3 ]; then
  echo "verify-and-deploy: still failing after $blocks attempts; releasing the stop." >&2
  rm -f "$tests_marker" "$app_marker" "$block_marker"
  exit 0
fi

project="${CLAUDE_PROJECT_DIR:-$(pwd)}"
port="${SELF_HOSTED_PORT:-8000}"

fail() {
  echo $((blocks + 1)) > "$block_marker"
  printf '%s\n' "$1" >&2
  exit 2
}

if [ -f "$tests_marker" ]; then
  # git worktrees do not carry the gitignored .venv, so fall back to the main
  # checkout's interpreter before giving up with an actionable message.
  py="$project/.venv/bin/python"
  if [ ! -x "$py" ]; then
    common_dir="$(cd "$project" && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)"
    main_root="${common_dir%/.git}"
    if [ -n "$main_root" ] && [ -x "$main_root/.venv/bin/python" ]; then
      py="$main_root/.venv/bin/python"
    else
      fail "verify-and-deploy: no .venv found at $project/.venv (git worktrees do not inherit it). Create one there, or run the suite manually."
    fi
  fi

  if ! out="$(cd "$project" && "$py" -m pytest -q 2>&1)"; then
    fail "$(printf 'pytest failed - fix before finishing:\n%s' "$(printf '%s' "$out" | tail -40)")"
  fi
fi

if [ -f "$app_marker" ]; then
  # The bind mount plus APP_RELOAD=1 means uvicorn reloads on its own; just
  # wait for it to come back rather than rebuilding the image.
  healthy=""
  i=0
  while [ "$i" -lt 10 ]; do
    if curl -fsS -m 3 "http://127.0.0.1:$port/api/v1/health" >/dev/null 2>&1; then
      healthy=1
      break
    fi
    i=$((i + 1))
    sleep 2
  done
  if [ -z "$healthy" ]; then
    logs="$(cd "$project" && docker compose logs --tail 40 app 2>&1)"
    fail "$(printf 'app container is not healthy after reload:\n%s' "$logs")"
  fi
fi

rm -f "$tests_marker" "$app_marker" "$block_marker"
exit 0
