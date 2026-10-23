@echo off
rem 一键把测试库重置成主库的副本（实验前跑一次）。
rem 需要反向合并（测试库 -> 主库）时，在命令行加 --to-main。
setlocal
pushd "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run "uv sync" in this folder first.
    popd
    pause
    exit /b 1
)

".venv\Scripts\python.exe" sync_db.py %*

popd
echo.
pause
