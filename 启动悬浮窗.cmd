@echo off
rem 双击启动悬浮窗（用 pythonw 避免弹出命令行窗口）
cd /d "%~dp0"

if exist ".venv\Scripts\pythonw.exe" (
    start "" ".venv\Scripts\pythonw.exe" overlay.py
    exit /b 0
)

where py >nul 2>nul
if %errorlevel%==0 (
    start "" py -3 overlay.py
    exit /b 0
)

echo 找不到 Python 环境：.venv\Scripts\pythonw.exe 不存在。
echo 请先在本目录执行：uv sync
pause
