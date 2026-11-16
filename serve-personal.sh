#!/bin/sh
set -eu
JG_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$JG_ROOT"
export ENABLE_OPENAI=false
exec /app/.venv/bin/python -m uvicorn jg.personal:app --factory --host 127.0.0.1 --port 8765 --workers 1
