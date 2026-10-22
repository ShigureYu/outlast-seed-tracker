@echo off
setlocal
chcp 65001 >nul
set PYTHONUTF8=1
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv\Scripts\python.exe not found.
    echo Recreate it with: uv venv --python 3.14 .venv
    echo Then install deps:  uv pip install --python .venv pyinstaller
    pause
    exit /b 1
)

".venv\Scripts\python.exe" "log_parser.py"
echo.
pause
