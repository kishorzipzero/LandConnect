@echo off
REM Land Connect - one-click start (Windows)
cd /d "%~dp0"
if not exist .venv (
  echo Creating virtual environment...
  py -3 -m venv .venv || python -m venv .venv
  .venv\Scripts\python.exe -m pip install --upgrade pip
  .venv\Scripts\python.exe -m pip install -r requirements.txt
)
if not exist data\demo\raw (
  echo Generating the synthetic benchmark ward...
  .venv\Scripts\python.exe -m landconnect.synth
)
if not exist output\demo\result.json .venv\Scripts\python.exe -m landconnect.pipeline demo
if exist data\jayanagar\raw if not exist output\jayanagar\result.json .venv\Scripts\python.exe -m landconnect.pipeline jayanagar
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn landconnect.api:app --host 127.0.0.1 --port 8000
