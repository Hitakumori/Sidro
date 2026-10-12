@echo off
rem Historical Windows OCR launcher; the current Settings selector and portable release support RapidOCR and optional PaddleOCR.
cd /d "%~dp0"
set TTB_OCR_ENGINE=windowsocr
set WFM_OCR_GPU=0
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
