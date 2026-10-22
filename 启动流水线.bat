@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 摄像头取帧、识别、保存读数；配置位于 config\pipeline.json
call "tools\run_python.cmd" "src\pipeline\流水线.py" %*
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
