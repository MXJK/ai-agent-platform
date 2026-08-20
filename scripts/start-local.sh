#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

echo "start-local.sh 已并入单实例 Docker 自托管入口。" >&2
exec "${SCRIPT_DIR}/start.sh" "$@"
