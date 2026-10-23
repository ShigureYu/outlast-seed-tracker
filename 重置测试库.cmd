@echo off
rem 用主库覆盖测试库（实验前重置，测试库原有内容会丢弃）
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run "uv sync" in this folder first.
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" sync_db.py reset %*
popd
echo.
pause
