@echo off
rem Graphics diagnostic source launcher; requests Qt software rendering and preserves the shared OCR dependency baseline.
cd /d "%~dp0"

rem Dev test launcher for HDR / fullscreen flicker.
rem Forces Qt away from GPU-backed render paths where possible.
set QT_OPENGL=software
set QT_QUICK_BACKEND=software
set QT_ANGLE_PLATFORM=warp
set QT_SCALE_FACTOR_ROUNDING_POLICY=PassThrough

call "%~dp0tools\run_source.bat" "main.py"
if errorlevel 1 pause
