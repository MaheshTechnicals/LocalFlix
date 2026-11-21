@echo off
rem LocalFlix launcher for Windows — double-click to start.
setlocal
cd /d "%~dp0"
chcp 65001 >nul
set PY=
where py >nul 2>nul && set PY=py -3
if not defined PY (
  where python >nul 2>nul && set PY=python
)
if not defined PY (
  echo Python 3.10+ is not installed.
  echo Install it with:  winget install Python.Python.3.13
  echo or download it from https://www.python.org/downloads/  ^(tick "Add python.exe to PATH"^)
  pause
  exit /b 1
)
%PY% "%~dp0localflix.py" %*
if errorlevel 1 (
  echo.
  echo LocalFlix stopped with an error. Run "%PY% localflix.py doctor" for diagnostics.
  pause
)
endlocal
