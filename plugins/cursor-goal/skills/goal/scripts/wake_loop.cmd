@echo off
REM Source PATH template for wake_loop.cmd (marketplace / non-baked installs).
REM Classic Windows install-goal.ps1 overwrites this with an absolute-Python bake.
REM Do not expect this file to match the installed classic launcher.
setlocal
set PYTHONUNBUFFERED=1
set "RUN_GOAL=%~dp0run_goal.py"
if not "%CURSOR_GOAL_PYTHON%"=="" (
  "%CURSOR_GOAL_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    "%CURSOR_GOAL_PYTHON%" -u "%RUN_GOAL%" wake loop %*
    exit /b %ERRORLEVEL%
  )
  echo [cursor-goal] CURSOR_GOAL_PYTHON is not Python 3.12+ >&2
  exit /b 1
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 -u "%RUN_GOAL%" wake loop %*
    exit /b %ERRORLEVEL%
  )
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python -u "%RUN_GOAL%" wake loop %*
    exit /b %ERRORLEVEL%
  )
)
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python3 -u "%RUN_GOAL%" wake loop %*
    exit /b %ERRORLEVEL%
  )
)
echo [cursor-goal] Python 3.12+ not found for wake_loop.cmd >&2
exit /b 1
