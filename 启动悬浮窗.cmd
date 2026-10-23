@echo off
rem Launch the overlay without a console window (pythonw = no black box).
rem pushd is used instead of "cd /d %~dp0": %~dp0 ends with a backslash,
rem which makes cmd mis-parse the closing quote and breaks the path.
setlocal
pushd "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" overlay.py
    popd
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 overlay.py
    popd
    exit /b 0
)

popd
echo [ERROR] .venv not found. Run "uv sync" in this folder first.
pause
