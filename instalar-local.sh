#!/bin/sh
set -eu
JG_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$JG_ROOT"
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
printf '%s\n' 'Instalado. Ejecute ./start-local.sh y abra http://127.0.0.1:8765'
