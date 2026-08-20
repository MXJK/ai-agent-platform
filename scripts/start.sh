#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/.." && pwd)"

cd "${PROJECT_ROOT}"

if ! command -v docker >/dev/null 2>&1; then
  echo "缺少必需命令：docker" >&2
  exit 1
fi
docker compose version >/dev/null

case "${1:-}" in
  --check)
    if [[ $# -ne 1 ]]; then
      echo "用法：./scripts/start.sh [--check]" >&2
      exit 2
    fi
    docker compose config --quiet
    echo "单实例 Docker 自托管配置检查通过。"
    ;;
  "")
    docker compose up -d --build
    echo "浏览器入口：http://127.0.0.1:${SELF_HOSTED_PORT:-8000}"
    echo "查看状态：docker compose ps"
    echo "查看日志：docker compose logs -f app"
    ;;
  *)
    echo "用法：./scripts/start.sh [--check]" >&2
    exit 2
    ;;
esac
