@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 仅抓图；计划位于 src\camera\read_camera.py 的 PLAN 区
"runtime\camera_python\python.exe" "src\camera\read_camera.py" %*
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
