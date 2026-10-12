@echo off
rem Batch release entry point; delegates the tested-baseline build and license bundling to build_exe.ps1.
setlocal
cd /d "%~dp0"
rem Share environment verification and packaging with the PowerShell builder.
powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%~dp0build_exe.ps1" %*
if errorlevel 1 exit /b 1
pause
