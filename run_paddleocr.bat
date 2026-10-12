@echo off
rem Request paddleocr for a source run through the shared locked environment; no packages are installed by this launcher.
cd /d "%~dp0"
set TTB_OCR_ENGINE=paddleocr
set WFM_OCR_GPU=0
set TTB_INVENTORY_OCR_DEBUG=1
set FLAGS_use_onednn=0
set FLAGS_use_mkldnn=0
set FLAGS_enable_pir_api=0
set FLAGS_enable_new_ir_in_executor=0
set GLOG_minloglevel=2
set FLAGS_minloglevel=2
call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
