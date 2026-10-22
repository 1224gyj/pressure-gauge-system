@echo off
chcp 65001 >nul
cd /d "%~dp0"
set "SOURCE_DIR=%~1"
if not defined SOURCE_DIR set "SOURCE_DIR=data\input"
call "tools\run_python.cmd" "src\pipeline\流水线.py" --from-dir "%SOURCE_DIR%" --excel "data\readings\图片读数.xlsx" --debug-dir "data\debug\图片读数_标注图"
set "RESULT=%errorlevel%"
pause
exit /b %RESULT%
