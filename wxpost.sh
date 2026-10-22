#!/usr/bin/env bash
# wxpost 启动器（macOS / Linux / Windows Git Bash 通用）
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$HERE/.venv/bin/python"
[ -x "$PY" ] || PY="$HERE/.venv/Scripts/python.exe"
[ -x "$PY" ] || PY="$(command -v python3 || command -v python)"
exec "$PY" "$HERE/wxpost.py" "$@"
