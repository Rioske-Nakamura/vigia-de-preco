@echo off
setlocal
cd /d "%~dp0"
where py >nul 2>&1
if not errorlevel 1 (
  py -3 -c "import sys; print(sys.version); sys.exit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 (
    py -3 app.py
    pause
    exit /b
  )
)
where python >nul 2>&1
if not errorlevel 1 (
  python -c "import sys; sys.exit(sys.version_info < (3, 10))" >nul 2>&1
  if not errorlevel 1 (
    python app.py
    pause
    exit /b
  )
)
echo Instale Python 3.10 ou mais recente em https://www.python.org/downloads/
pause
