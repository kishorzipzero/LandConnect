#!/usr/bin/env sh
# Land Connect - start (macOS / Linux)
cd "$(dirname "$0")"
[ -d .venv ] || { python3 -m venv .venv && .venv/bin/pip install -r requirements.txt; }
[ -d data/demo/raw ] || .venv/bin/python -m landconnect.synth
[ -f output/demo/result.json ] || .venv/bin/python -m landconnect.pipeline demo
[ -d data/jayanagar/raw ] && [ ! -f output/jayanagar/result.json ] && .venv/bin/python -m landconnect.pipeline jayanagar
.venv/bin/python -m uvicorn landconnect.api:app --host 127.0.0.1 --port 8000
