@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>nul
if %ERRORLEVEL% EQU 0 (
  set "PYCMD=py -3"
) else (
  set "PYCMD=python"
)
if not exist ".venv\Scripts\python.exe" (
  echo Creating local Python environment...
  %PYCMD% -m venv .venv
  if errorlevel 1 goto :error
)
".venv\Scripts\python.exe" -m pip install -r requirements.txt
if errorlevel 1 goto :error
echo.
echo Myco OS Cloud will be available at http://127.0.0.1:8000
echo Leave this window open while using it. Press Ctrl+C to stop.
echo To access from other devices, deploy the app to a cloud host: README.md
start "" "http://127.0.0.1:8000"
".venv\Scripts\python.exe" -m uvicorn app:app --host 127.0.0.1 --port 8000
exit /b 0
:error
echo.
echo Python setup failed. Install Python 3.11 or newer and try again.
pause
exit /b 1
