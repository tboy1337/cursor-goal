@echo off
REM Source PATH template for wake_loop.cmd (marketplace / non-baked installs).
REM Classic Windows install-goal.ps1 overwrites this with an absolute-Python bake.
REM Do not expect this file to match the installed classic launcher.
setlocal EnableExtensions
set PYTHONUNBUFFERED=1
set "RUN_GOAL=%~dp0run_goal.py"
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
echo %CGP%| findstr /R "[&|<>^]" >nul 2>&1
if not errorlevel 1 (
  echo [cursor-goal] CURSOR_GOAL_PYTHON contains unsafe cmd metacharacters >&2
  exit /b 1
)
"%CGP%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)" >nul 2>&1
if errorlevel 1 (
  echo [cursor-goal] CURSOR_GOAL_PYTHON is not Python 3.12+ >&2
  exit /b 1
)
"%CGP%" -u "%RUN_GOAL%" wake loop %*
exit /b %ERRORLEVEL%

:use_path
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
