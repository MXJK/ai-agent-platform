#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PYTHON="${PROJECT_ROOT}/.venv/bin/python"

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
    echo "请先创建 .venv 并安装 requirements.txt 与当前项目。" >&2
    exit 1
  fi
}

load_and_validate_local_env() {
  local exports
  exports="$("${PYTHON}" - <<'PY'
import os
import shlex
from pathlib import Path

from ai_agent_platform.core import Settings, validate_bind_host


def read_dotenv() -> dict[str, str]:
    values: dict[str, str] = {}
    path = Path(".env")
    if not path.exists():
        return values
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip("\"'")
    return values


dotenv = read_dotenv()
settings = Settings.from_env()
if settings.runtime_profile != "local":
    raise SystemExit(
        f"本地启动要求 RUNTIME_PROFILE=local，当前为 {settings.runtime_profile}。"
        "生产 profile 请使用 ./scripts/start.sh。"
    )

app_host = os.getenv("APP_HOST", dotenv.get("APP_HOST", "127.0.0.1"))
app_port_raw = os.getenv("APP_PORT", dotenv.get("APP_PORT", "8001"))
app_reload = os.getenv("APP_RELOAD", dotenv.get("APP_RELOAD", "1"))
gateway_auth_mode = os.getenv(
    "GATEWAY_AUTH_MODE",
    dotenv.get("GATEWAY_AUTH_MODE", "disabled"),
).strip().lower()
try:
    app_port = int(app_port_raw)
except ValueError as exc:
    raise SystemExit("APP_PORT 必须是 1-65535 的整数。") from exc
if not 1 <= app_port <= 65535:
    raise SystemExit("APP_PORT 必须是 1-65535 的整数。")
if app_reload not in {"0", "1"}:
    raise SystemExit("APP_RELOAD 必须是 0 或 1。")
if gateway_auth_mode not in {"disabled", "local"}:
    raise SystemExit("本地 profile 的 GATEWAY_AUTH_MODE 只能是 disabled 或 local。")
validate_bind_host(host=app_host, auth_mode=settings.auth_mode)
if gateway_auth_mode == "local":
    if settings.auth_mode != "trusted_header":
        raise SystemExit("GATEWAY_AUTH_MODE=local 要求 AUTH_MODE=trusted_header。")
    validate_bind_host(host=app_host, auth_mode="disabled")
elif settings.auth_mode == "trusted_header":
    raise SystemExit("AUTH_MODE=trusted_header 要求 GATEWAY_AUTH_MODE=local。")

values = {
    "APP_HOST": app_host,
    "APP_PORT": str(app_port),
    "APP_RELOAD": app_reload,
    "GATEWAY_AUTH_MODE": gateway_auth_mode,
}
if gateway_auth_mode == "local":
    values.update(
        {
            "GATEWAY_LISTEN_ADDRESS": os.getenv(
                "LOCAL_GATEWAY_LISTEN_ADDRESS",
                "127.0.0.1:8000",
            ),
            "GATEWAY_UPSTREAM_URL": os.getenv(
                "LOCAL_GATEWAY_UPSTREAM_URL",
                f"http://127.0.0.1:{app_port}",
            ),
            "GATEWAY_LOCAL_USER_ID": os.getenv(
                "GATEWAY_LOCAL_USER_ID",
                dotenv.get("GATEWAY_LOCAL_USER_ID", "demo_user"),
            ),
            "GATEWAY_TRUST_SECRET": os.getenv(
                "GATEWAY_TRUST_SECRET",
                dotenv.get("GATEWAY_TRUST_SECRET", ""),
            ),
        }
    )
for name, value in values.items():
    print(f"export {name}={shlex.quote(value)}")
PY
)"
  eval "${exports}"
}

require_file "${PYTHON}"
load_and_validate_local_env

if [[ "${GATEWAY_AUTH_MODE}" == "local" ]]; then
  require_command docker
  docker compose version >/dev/null
  docker compose --profile gateway config --quiet
fi

if [[ $# -gt 1 || ( $# -eq 1 && "${1}" != "--check" ) ]]; then
  echo "用法：./scripts/start-local.sh [--check]" >&2
  exit 2
fi
if [[ "${1:-}" == "--check" ]]; then
  echo "本地 SQLite profile 检查通过。"
  exit 0
fi

if [[ "${GATEWAY_AUTH_MODE}" == "local" ]]; then
  echo "正在启动本地 Go 网关容器：http://127.0.0.1:8000"
  docker compose --profile gateway up -d --build gateway
  echo "浏览器入口：http://127.0.0.1:8000"
  echo "API 停止后网关容器会继续运行；可用 docker compose stop gateway 停止。"
else
  echo "浏览器入口：http://${APP_HOST}:${APP_PORT}"
fi

API_ARGS=(--host "${APP_HOST}" --port "${APP_PORT}")
if [[ "${APP_RELOAD}" == "1" ]]; then
  API_ARGS+=(--reload)
fi

echo "正在启动本地 SQLite FastAPI；不会启动或迁移 PostgreSQL/Qdrant/Redis。"
"${PYTHON}" -m ai_agent_platform.api.entrypoint "${API_ARGS[@]}"
