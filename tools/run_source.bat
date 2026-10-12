@echo off
setlocal
cd /d "%~dp0.."
set "SIDRO_PYTHON=.venv-sidro\Scripts\python.exe"
if not exist "%SIDRO_PYTHON%" (
  echo Run setup_environment.ps1 before starting Sidro from source.
  exit /b 1
)
"%SIDRO_PYTHON%" -B tools\check_dependencies.py
if errorlevel 1 exit /b 1
"%SIDRO_PYTHON%" %*
exit /b %errorlevel%
