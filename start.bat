@echo off
chcp 65001 >nul
cd /d "%~dp0"

set PY=
where python >nul 2>nul && set PY=python
where py >nul 2>nul && if not defined PY set PY=py -3

if not defined PY (
  echo.
  echo   [X] This computer does not have Python installed.
  echo       Download: https://www.python.org/downloads/
  echo       IMPORTANT: check "Add python.exe to PATH" during install.
  echo.
  pause
  exit /b 1
)

echo.
echo   Jianyi - starting local server...
echo   Keep this window open. Close it to stop.
echo.
start "" http://localhost:8000
%PY% server.py 8000
pause
