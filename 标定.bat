@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo 读取 data\input 中的图片和同名 txt 真值，默认只报告
call "tools\run_python.cmd" "src\recognition\标定.py" %*
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
