@echo off
setlocal
cd /d "%~dp0"
if not exist "%~dp0.venv\Scripts\python.exe" (
  python -m venv .venv
)
"%~dp0.venv\Scripts\python.exe" -m pip install --upgrade pip
"%~dp0.venv\Scripts\python.exe" -m pip install -r requirements.txt pyinstaller
"%~dp0.venv\Scripts\python.exe" -m PyInstaller ^
  -y ^
  --noconsole ^
  --name AStockPanel ^
  --paths "%~dp0src" ^
  --icon "%~dp0assets\app_icon.ico" ^
  --add-data "%~dp0assets;assets" ^
  --add-data "%~dp0config;config" ^
  "%~dp0launch_gui.py"
endlocal
