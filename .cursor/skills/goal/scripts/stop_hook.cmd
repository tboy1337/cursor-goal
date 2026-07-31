@echo off
REM Cursor stop-hook launcher for Windows.
REM Installer rewrites PYTHON_EXE and STOP_PY with absolute paths.
REM Fallback: sibling stop_hook.py + py/python on PATH.
setlocal
set PYTHONUNBUFFERED=1
set "STOP_PY=%~dp0stop_hook.py"
if not "%CURSOR_GOAL_PYTHON%"=="" (
  "%CURSOR_GOAL_PYTHON%" -u "%STOP_PY%"
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -u "%STOP_PY%"
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -u "%STOP_PY%"
  exit /b %ERRORLEVEL%
)
echo [cursor-goal] No Python found for stop_hook.cmd 1>&2
exit /b 1
