@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 预演抓拍计划（不连接摄像头）
call "tools\run_python.cmd" "src\pipeline\流水线.py" --dry-run %*
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
