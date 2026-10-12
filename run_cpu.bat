@echo off
rem Launch main.py after the shared dependency check. GPU-named entry points retain the supported CPU OCR baseline.
cd /d "%~dp0"
set WFM_OCR_GPU=0
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
