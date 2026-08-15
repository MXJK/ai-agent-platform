#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ALEMBIC="${PROJECT_ROOT}/.venv/bin/alembic"
CELERY="${PROJECT_ROOT}/.venv/bin/celery"
COMPOSE_SERVICES=(postgres qdrant redis gateway)
WORKER_PID=""

cd "${PROJECT_ROOT}"

require_command() {
  local command_name="$1"
  if ! command -v "${command_name}" >/dev/null 2>&1; then
    echo "缺少必需命令：${command_name}" >&2
    exit 1
  fi
}

require_file() {
  local file_path="$1"
  if [[ ! -x "${file_path}" ]]; then
    echo "缺少可执行文件：${file_path}" >&2
    echo "请先创建 .venv 并安装 requirements.txt。" >&2
    exit 1
  fi
}

load_app_runtime_env() {
  local exports
  exports="$("${PYTHON}" - <<'PY'
import os
import shlex
from pathlib import Path

dotenv = {}
dotenv_path = Path(".env")
if dotenv_path.exists():
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        dotenv[name.strip()] = value.strip().strip("\"'")

values = {
    "APP_HOST": os.getenv("APP_HOST", dotenv.get("APP_HOST", "127.0.0.1")),
    "APP_PORT": os.getenv("APP_PORT", dotenv.get("APP_PORT", "8001")),
    "APP_RELOAD": os.getenv("APP_RELOAD", dotenv.get("APP_RELOAD", "1")),
}
try:
    app_port = int(values["APP_PORT"])
except ValueError as exc:
    raise SystemExit("APP_PORT 必须是 1-65535 的整数。") from exc
if not 1 <= app_port <= 65535:
    raise SystemExit("APP_PORT 必须是 1-65535 的整数。")
if values["APP_RELOAD"] not in {"0", "1"}:
    raise SystemExit("APP_RELOAD 必须是 0 或 1。")
for name, value in values.items():
    print(f"export {name}={shlex.quote(value)}")
PY
)"
  eval "${exports}"
}

validate_runtime_config() {
  "${PYTHON}" - <<'PY'
import os
from pathlib import Path

from ai_agent_platform.core import Settings, validate_bind_host

settings = Settings.from_env()
dotenv = {}
dotenv_path = Path(".env")
if dotenv_path.exists():
    for raw_line in dotenv_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        dotenv[name.strip()] = value.strip().strip("\"'")

gateway_auth_mode = os.getenv(
    "GATEWAY_AUTH_MODE",
    dotenv.get("GATEWAY_AUTH_MODE", "disabled"),
).strip().lower()
app_host = os.getenv("APP_HOST", dotenv.get("APP_HOST", "127.0.0.1"))
if gateway_auth_mode not in {"disabled", "local", "oidc"}:
    raise SystemExit(
        "GATEWAY_AUTH_MODE 必须是 disabled、local 或 oidc。"
    )
validate_bind_host(
    host=app_host,
    auth_mode=settings.auth_mode,
)
if gateway_auth_mode in {"local", "oidc"} and settings.auth_mode != "trusted_header":
    raise SystemExit(
        f"GATEWAY_AUTH_MODE={gateway_auth_mode} 要求 AUTH_MODE=trusted_header。"
    )
if gateway_auth_mode == "local":
    validate_bind_host(host=app_host, auth_mode="disabled")
elif settings.auth_mode == "trusted_header" and gateway_auth_mode == "disabled":
    raise SystemExit(
        "AUTH_MODE=trusted_header 要求启用 GATEWAY_AUTH_MODE=local 或 oidc。"
    )
expected = {
    "session_repository": "postgres",
    "agent_run_store": "postgres",
    "document_store": "postgres",
    "workspace_store": "postgres",
    "langgraph_checkpointer": "postgres",
    "rag_vector_store": "qdrant",
    "task_queue_backend": "celery",
}
actual = {name: getattr(settings, name) for name in expected}
invalid = {
    name: {"actual": actual[name], "expected": expected_value}
    for name, expected_value in expected.items()
    if actual[name] != expected_value
}
if invalid:
    details = ", ".join(
        f"{name}={values['actual']}（应为 {values['expected']}）"
        for name, values in invalid.items()
    )
    raise SystemExit(f"持久化运行配置不完整：{details}")

print("持久化运行配置检查通过。")
PY
}

load_postgres_compose_env() {
  local exports
  exports="$("${PYTHON}" - <<'PY'
import shlex
from urllib.parse import unquote, urlparse

from ai_agent_platform.core import Settings

database_url = Settings.from_env().database_url
parsed = urlparse(database_url)
if parsed.scheme not in {"postgres", "postgresql"}:
    raise SystemExit("DATABASE_URL 必须使用 postgres 或 postgresql scheme。")
if not parsed.username or parsed.password is None or not parsed.path.strip("/"):
    raise SystemExit("DATABASE_URL 必须包含 PostgreSQL 用户、密码和数据库名。")

values = {
    "POSTGRES_DB": unquote(parsed.path.strip("/")),
    "POSTGRES_USER": unquote(parsed.username),
    "POSTGRES_PASSWORD": unquote(parsed.password),
}
for name, value in values.items():
    print(f"export {name}={shlex.quote(value)}")
PY
)"
  eval "${exports}"
}

wait_for_services() {
  "${PYTHON}" - <<'PY'
import time

import httpx
import psycopg
from redis import Redis

from ai_agent_platform.core import Settings

settings = Settings.from_env()
deadline = time.monotonic() + 60
last_errors: dict[str, str] = {}

while time.monotonic() < deadline:
    ready = set()

    try:
        with psycopg.connect(settings.database_url, connect_timeout=2) as connection:
            connection.execute("SELECT 1").fetchone()
        ready.add("PostgreSQL")
    except Exception as exc:
        last_errors["PostgreSQL"] = str(exc)

    try:
        with httpx.Client(timeout=2, trust_env=False) as client:
            response = client.get(f"{settings.qdrant_url}/collections")
            response.raise_for_status()
        ready.add("Qdrant")
    except Exception as exc:
        last_errors["Qdrant"] = str(exc)

    try:
        if Redis.from_url(
            settings.redis_url,
            socket_connect_timeout=2,
            socket_timeout=2,
        ).ping():
            ready.add("Redis")
    except Exception as exc:
        last_errors["Redis"] = str(exc)

    if ready == {"PostgreSQL", "Qdrant", "Redis"}:
        print("PostgreSQL、Qdrant、Redis 已就绪。")
        break

    time.sleep(1)
else:
    details = "; ".join(
        f"{service}: {error}" for service, error in sorted(last_errors.items())
    )
    raise SystemExit(f"等待依赖服务超时。{details}")
PY
}

cleanup() {
  local exit_code=$?
  trap - EXIT INT TERM
  if [[ -n "${WORKER_PID}" ]] && kill -0 "${WORKER_PID}" 2>/dev/null; then
    echo
    echo "正在停止 Celery Worker..."
    kill "${WORKER_PID}" 2>/dev/null || true
    wait "${WORKER_PID}" 2>/dev/null || true
  fi
  exit "${exit_code}"
}

require_command docker
require_file "${PYTHON}"
require_file "${ALEMBIC}"
require_file "${CELERY}"
load_app_runtime_env
docker compose version >/dev/null
validate_runtime_config
load_postgres_compose_env
docker compose config --quiet

if [[ "${1:-}" == "--check" ]]; then
  if [[ $# -ne 1 ]]; then
    echo "用法：./scripts/start.sh --check | --apply-migrations" >&2
    exit 2
  fi
  echo "启动脚本检查通过。"
  exit 0
fi

if [[ $# -ne 1 || "${1:-}" != "--apply-migrations" ]]; then
  echo "启动 PostgreSQL runtime 前必须由操作者显式授权待处理的 Alembic 迁移。" >&2
  echo "确认迁移计划后运行：./scripts/start.sh --apply-migrations" >&2
  echo "仅检查依赖和配置：./scripts/start.sh --check" >&2
  exit 2
fi

echo "正在启动 PostgreSQL、Qdrant、Redis 和本地网关..."
docker compose up -d --build "${COMPOSE_SERVICES[@]}"
wait_for_services

echo "已收到显式授权，正在应用数据库迁移..."
"${ALEMBIC}" upgrade head

trap cleanup EXIT INT TERM

echo "正在启动 Celery Worker..."
"${CELERY}" \
  -A ai_agent_platform.workers.celery_app:celery_app \
  worker \
  --loglevel="${CELERY_LOG_LEVEL:-INFO}" &
WORKER_PID=$!

sleep 1
if ! kill -0 "${WORKER_PID}" 2>/dev/null; then
  echo "Celery Worker 启动失败。" >&2
  wait "${WORKER_PID}"
fi

API_ARGS=(
  "${PYTHON}"
  -m uvicorn
  ai_agent_platform.api.entrypoint:app
  --host "${APP_HOST:-127.0.0.1}"
  --port "${APP_PORT:-8001}"
)
if [[ "${APP_RELOAD:-1}" == "1" ]]; then
  API_ARGS+=(--reload)
fi

echo "正在启动 FastAPI upstream：http://${APP_HOST:-127.0.0.1}:${APP_PORT:-8001}"
echo "浏览器入口：http://127.0.0.1:8000（兼容入口：http://127.0.0.1:8080）"
echo "按 Ctrl+C 停止 API 和 Celery Worker；基础服务和网关容器会继续运行。"
"${API_ARGS[@]}"
