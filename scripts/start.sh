#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"
ALEMBIC="${PROJECT_ROOT}/.venv/bin/alembic"
CELERY="${PROJECT_ROOT}/.venv/bin/celery"
COMPOSE_SERVICES=(postgres qdrant redis)
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

validate_runtime_config() {
  "${PYTHON}" - <<'PY'
from ai_agent_platform.core import Settings

settings = Settings.from_env()
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
docker compose version >/dev/null
docker compose config --quiet
validate_runtime_config

if [[ "${1:-}" == "--check" ]]; then
  echo "启动脚本检查通过。"
  exit 0
fi

if [[ $# -gt 0 ]]; then
  echo "未知参数：$1" >&2
  echo "用法：./scripts/start.sh [--check]" >&2
  exit 2
fi

echo "正在启动 PostgreSQL、Qdrant 和 Redis..."
docker compose up -d "${COMPOSE_SERVICES[@]}"
wait_for_services

echo "正在应用数据库迁移..."
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
  ai_agent_platform.main:app
  --host "${APP_HOST:-127.0.0.1}"
  --port "${APP_PORT:-8000}"
)
if [[ "${APP_RELOAD:-1}" == "1" ]]; then
  API_ARGS+=(--reload)
fi

echo "正在启动 FastAPI：http://${APP_HOST:-127.0.0.1}:${APP_PORT:-8000}"
echo "按 Ctrl+C 停止 API 和 Celery Worker；数据库容器会继续运行。"
"${API_ARGS[@]}"
