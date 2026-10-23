@echo off
rem 把测试库新增的 seed 并入主库（主库已有的记录不会被改动）
setlocal
pushd "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run "uv sync" in this folder first.
    popd
    pause
    exit /b 1
)
".venv\Scripts\python.exe" sync_db.py merge %*
popd
echo.
pause
