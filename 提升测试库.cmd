@echo off
rem 把测试库里的有效数据合并进主库（只合并差异，不整文件覆盖）。
rem 冲突默认保留主库的值；要覆盖请用命令行加 --apply-local。
setlocal
pushd "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [ERROR] .venv not found. Run "uv sync" in this folder first.
    popd
    pause
    exit /b 1
)

".venv\Scripts\python.exe" sync_db.py --to-main %*

popd
echo.
pause
