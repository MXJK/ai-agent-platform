"""Bounded synchronous actions for trusted, process-owned configuration."""
from __future__ import annotations

import os
import signal
import subprocess
import tempfile
import time
from urllib.request import Request, build_opener, HTTPRedirectHandler

from .models import Action, ActionResult, HookContext, VARIABLE

OUTPUT_LIMIT = 8_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def execute_command(action: Action, ctx: HookContext) -> ActionResult:
    env = os.environ.copy()
    # Shell expands environment values as data, without evaluating command
    # substitutions embedded in the tool arguments. Scripts should quote them.
    def substitute(match):
        name = f"COGENT_HOOK_VALUE_{len(values)}"
        values[name] = ctx.variable(match[1])
        return "${" + name + "}"

    values: dict[str, str] = {}
    command = VARIABLE.sub(substitute, action.command)
    env.update(values)
    env["COGENT_HOOK_CONTEXT"] = ctx.to_json()
    with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as stdin:
        stdin.write(ctx.to_json().encode())
        stdin.seek(0)
        proc = subprocess.Popen(command, shell=True, cwd=ctx.workspace_root or None,
                                env=env, stdin=stdin, stdout=output,
                                stderr=subprocess.STDOUT, start_new_session=True)
        failure = ""
        deadline = time.monotonic() + action.timeout
        try:
            while proc.poll() is None:
                if os.fstat(output.fileno()).st_size > 1_000_000:
                    failure = "Command output exceeded 1 MB"
                    break
                if time.monotonic() >= deadline:
                    failure = f"Command timed out after {action.timeout}s"
                    break
                time.sleep(0.01)
        finally:
            # Also clean up background descendants after the shell exits.
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            proc.wait()
        output.seek(0)
        text = output.read(OUTPUT_LIMIT + 1).decode(errors="replace")
        return ActionResult(failure or text[:OUTPUT_LIMIT].strip(),
                            not failure and proc.returncode == 0)


def execute_http(action: Action, ctx: HookContext) -> ActionResult:
    body = ctx.expand(action.body).encode() if action.body else ctx.to_json().encode()
    headers = {key: ctx.expand(value) for key, value in action.headers.items()}
    headers.setdefault("Content-Type", "application/json")
    req = Request(action.url, data=None if action.method == "GET" else body,
                  headers=headers, method=action.method)
    with build_opener(_NoRedirect).open(req, timeout=action.timeout) as response:
        text = response.read(OUTPUT_LIMIT).decode(errors="replace")
        return ActionResult(f"HTTP {response.status}: {text}"[:OUTPUT_LIMIT],
                            200 <= response.status < 300)


def execute_action(action: Action, ctx: HookContext) -> ActionResult:
    if action.type == "prompt":
        return ActionResult(ctx.expand(action.message)[:OUTPUT_LIMIT])
    if action.type == "command":
        return execute_command(action, ctx)
    if action.type == "http":
        return execute_http(action, ctx)
    return ActionResult("Unsupported hook action", False)
