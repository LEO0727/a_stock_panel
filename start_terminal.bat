@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  set "PYTHON=%~dp0.venv\Scripts\python.exe"
) else (
  set "PYTHON=python"
)
set "PYTHONPATH=%~dp0src"
"%PYTHON%" -m astock_panel.cli --watch
endlocal
