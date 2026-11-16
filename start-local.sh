#!/bin/sh
set -eu
JG_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$JG_ROOT"
JG_PYTHON=${JG_PYTHON:-"$JG_ROOT/.venv/bin/python"}
if [ ! -x "$JG_PYTHON" ]; then
  printf '%s\n' 'Falta el entorno de Python. Ejecute instalar-local.sh primero.' >&2
  exit 1
fi
export ENABLE_OPENAI=false
exec "$JG_PYTHON" -m uvicorn jg.personal:app --factory --host 127.0.0.1 --port 8765 --workers 1
