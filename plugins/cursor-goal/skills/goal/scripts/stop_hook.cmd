@echo off
setlocal
set PYTHONUNBUFFERED=1
set "STOP_PY=%~dp0stop_hook.py"
if not "%CURSOR_GOAL_PYTHON%"=="" (
  "%CURSOR_GOAL_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    "%CURSOR_GOAL_PYTHON%" -u "%STOP_PY%"
    exit /b %ERRORLEVEL%
  )
  echo [cursor-goal] CURSOR_GOAL_PYTHON is not Python 3.12+ >&2
  exit /b 1
)
where py >nul 2>&1
if %ERRORLEVEL%==0 (
  py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    py -3 -u "%STOP_PY%"
    exit /b %ERRORLEVEL%
  )
)
where python >nul 2>&1
if %ERRORLEVEL%==0 (
  python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python -u "%STOP_PY%"
    exit /b %ERRORLEVEL%
  )
)
where python3 >nul 2>&1
if %ERRORLEVEL%==0 (
  python3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
  if %ERRORLEVEL%==0 (
    python3 -u "%STOP_PY%"
    exit /b %ERRORLEVEL%
  )
)
echo [cursor-goal] Python 3.12+ not found on PATH >&2
exit /b 1
