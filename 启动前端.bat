@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 启动后打开 http://localhost:8787；关闭窗口即可停止服务
"runtime\node\node.exe" "tools\start_frontend.js" %*
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
