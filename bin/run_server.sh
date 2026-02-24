#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ ! -d "${PROJECT_DIR}/.venv" ]]; then
  echo "Missing .venv. Create it with: python3 -m venv .venv && . .venv/bin/activate && python -m pip install -r requirements.txt" >&2
  exit 1
fi

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-3000}"
exec "${PROJECT_DIR}/.venv/bin/python" -m uvicorn py_app.main:app --host "$HOST" --port "$PORT"
