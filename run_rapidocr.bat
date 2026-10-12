@echo off
cd /d "%~dp0"
set TTB_OCR_ENGINE=rapidocr
set WFM_OCR_GPU=0
set TTB_INVENTORY_OCR_DEBUG=1
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
