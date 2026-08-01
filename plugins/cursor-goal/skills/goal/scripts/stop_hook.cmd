@echo off
setlocal EnableExtensions
set PYTHONUNBUFFERED=1
set "STOP_PY=%~dp0stop_hook.py"
if not "%CURSOR_GOAL_PYTHON%"=="" goto :use_cgp
echo [cursor-goal] WARNING: CURSOR_GOAL_PYTHON unset; using PATH Python. Set CURSOR_GOAL_PYTHON to an absolute 3.12+ interpreter. >&2
goto :use_path

:use_cgp
set "CGP=%CURSOR_GOAL_PYTHON:"=%"
set "CGP_ABS="
if "%CGP:~1,1%"==":" set "CGP_ABS=1"
if "%CGP:~0,2%"=="\\" set "CGP_ABS=1"
if not defined CGP_ABS (
  echo [cursor-goal] CURSOR_GOAL_PYTHON must be an absolute path >&2
  exit /b 1
)
"%CURSOR_GOAL_PYTHON%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [cursor-goal] CURSOR_GOAL_PYTHON is not Python 3.12+ >&2
  exit /b 1
)
"%CURSOR_GOAL_PYTHON%" -u "%STOP_PY%"
exit /b %ERRORLEVEL%

:use_path
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
