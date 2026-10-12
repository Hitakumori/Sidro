@echo off
rem Request rapidocr for a source run through the shared locked environment; no packages are installed by this launcher.
cd /d "%~dp0"
set TTB_OCR_ENGINE=rapidocr
set WFM_OCR_GPU=0
set TTB_INVENTORY_OCR_DEBUG=1
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
