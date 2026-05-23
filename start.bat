@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0dist\AStockPanel\AStockPanel.exe" (
  start "" "%~dp0dist\AStockPanel\AStockPanel.exe"
  endlocal
  exit /b
)
if exist "%~dp0.venv\Scripts\pythonw.exe" (
  set "PYTHON=%~dp0.venv\Scripts\pythonw.exe"
) else (
  set "PYTHON=pythonw"
)
start "" "%PYTHON%" "%~dp0launch_gui.py"
endlocal
