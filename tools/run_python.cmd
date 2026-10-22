@echo off
setlocal
for %%I in ("%~dp0..") do set "PROJECT_DIR=%%~fI"
set "PROJECT_PY="
if exist "%PROJECT_DIR%\config\python_path.txt" set /p PROJECT_PY=<"%PROJECT_DIR%\config\python_path.txt"
if not defined PROJECT_PY set "PROJECT_PY=python"
cd /d "%PROJECT_DIR%"
"%PROJECT_PY%" %*
exit /b %errorlevel%
