@echo off
chcp 65001 >nul
if not exist "%~dp0data\research" mkdir "%~dp0data\research"
echo stop>"%~dp0data\research\stop-capture.flag"
echo 已请求停止研究采集，等待中的程序将在数秒内释放摄像头连接。
pause
