@echo off
setlocal
set PYTHONUNBUFFERED=1
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -u "%~dp0stop_hook.py"
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -u "%~dp0stop_hook.py"
  exit /b %ERRORLEVEL%
)
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
  python3 -u "%~dp0stop_hook.py"
  exit /b %ERRORLEVEL%
)
echo [cursor-goal] Python 3.12+ not found on PATH >&2
exit /b 1
