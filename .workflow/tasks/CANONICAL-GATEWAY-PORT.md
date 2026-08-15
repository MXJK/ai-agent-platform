# CANONICAL-GATEWAY-PORT

## Goal

Eliminate the misleading direct-FastAPI browser entry after trusted gateway authentication was enabled. Make port 8000 the canonical authenticated UI entry and move the internal FastAPI upstream to port 8001.

## Acceptance criteria

- `http://127.0.0.1:8000` is served by the authenticated local gateway and loads workspaces successfully.
- FastAPI listens on `127.0.0.1:8001`; direct protected API requests remain unauthorized.
- Port 8080 remains a temporary compatibility alias for existing local browser tabs.
- The startup script and Compose use one consistent API port, including `APP_PORT` overrides.
- Real-browser verification confirms that the canonical URL loads the existing session and workspace list without the previous error.
- Automated verification and user-facing documentation pass.

## Permitted scope

- Local Compose, runtime `.env`, startup defaults, and gateway/upstream port configuration.
- Startup/config regression tests if needed.
- README and interview handbook documentation required by the repository contract.

## Decisions

- Restore port 8000 as the only canonical browser URL, but assign it to the
  trusted local gateway rather than FastAPI.
- Move the loopback FastAPI upstream to port 8001. Keep 8080 mapped to the same
  gateway container as a compatibility alias for tabs opened during the previous
  local-direct-write task.
- Load only `APP_HOST`, `APP_PORT`, and `APP_RELOAD` from `.env` into the startup
  shell using quoted values. This keeps Uvicorn and Compose on the same upstream
  port while avoiding sourcing credentials from the whole file.
- Do not add a redirecting frontend shell on the internal API port. Protected
  requests on 8001 continue to fail closed without gateway identity.

## Verification

- `.venv/bin/python -m pytest -q`: `386 passed, 49 subtests passed`.
- `.venv/bin/python -m compileall ai_agent_platform tests evals`: passed.
- Dockerized `go test ./gateway/...`: passed.
- `node --check ai_agent_platform/static/app.js`: passed.
- `bash -n scripts/start.sh`: passed.
- `./scripts/start.sh --check`: passed with `.env` `APP_PORT=8001`.
- `docker compose --profile gateway config --quiet`: passed.
- Compose override check with `APP_PORT=8002`: resolved gateway upstream to
  `http://host.docker.internal:8002` and published both 8000 and 8080.
- `.venv/bin/python INTERVIEW_NOTES/validate.py`: validated 12 Markdown files and
  37 capabilities; existing dirty-worktree evidence review warnings were reported.
- `git diff --check`: passed.
- Live HTTP checks: ports 8000 and 8080 returned 200 for health and workspaces;
  the protected workspace endpoint on direct FastAPI port 8001 returned 401.
- Real in-app browser check at `http://127.0.0.1:8000`: restored session
  `sess_b2e453338f43`, showed `test-workspace` as the active context, and opened
  Workspace Settings with both `ai-agent-platform` and `test-workspace` listed.

## Result

Complete. The original 8000 URL is again a fully functional browser entry, now
served through the authenticated gateway. FastAPI runs internally on 8001, 8080
remains a compatibility alias, and the startup path keeps Compose/Uvicorn port
overrides consistent. The live API, worker, gateway, and browser have been
restarted on the new topology. README, README.en, and the interview handbook are
synchronized. No migration was added or executed. Commit and push were authorized
after verification; the resulting commit is reported in the final handoff.
