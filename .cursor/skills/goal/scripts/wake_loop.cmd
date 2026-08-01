@echo off
REM Goal wake watchdog launcher for Windows (optional convenience wrapper).
REM Prefer: py -3 -u "%~dp0run_goal.py" wake loop
REM Installer may rewrite CURSOR_GOAL_PYTHON; fallback uses py/python on PATH.
setlocal
set PYTHONUNBUFFERED=1
set "RUN_GOAL=%~dp0run_goal.py"
if not "%CURSOR_GOAL_PYTHON%"=="" (
  "%CURSOR_GOAL_PYTHON%" -u "%RUN_GOAL%" wake loop %*
  exit /b %ERRORLEVEL%
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -u "%RUN_GOAL%" wake loop %*
  exit /b %ERRORLEVEL%
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -u "%RUN_GOAL%" wake loop %*
  exit /b %ERRORLEVEL%
)
echo [cursor-goal] No Python found for wake_loop.cmd 1>&2
exit /b 1
