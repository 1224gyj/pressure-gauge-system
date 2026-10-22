@echo off
chcp 65001 >nul
cd /d "%~dp0"
call "tools\run_python.cmd" "tests\check_project.py"
pause
