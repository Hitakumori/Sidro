@echo off
cd /d "%~dp0"
set WFM_OCR_GPU=auto
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
