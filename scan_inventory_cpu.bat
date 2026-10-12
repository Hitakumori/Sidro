@echo off
rem Run the standalone inventory scanner diagnostic and save local debug images. GPU-named aliases use the same CPU baseline.
cd /d "%~dp0"
set WFM_OCR_GPU=0
call "%~dp0tools\run_source.bat" "inventory_scanner.py"
if errorlevel 1 pause
