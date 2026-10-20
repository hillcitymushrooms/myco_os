#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
if [ ! -d .venv ]; then python3 -m venv .venv; fi
. .venv/bin/activate
python -m pip install -r requirements.txt
printf '\nMyco OS Cloud available at http://127.0.0.1:8000\nCtrl+C to stop.\n'
exec python -m uvicorn app:app --host 127.0.0.1 --port 8000
