param([string]$ReleaseVenv = ".\.venv-sidro")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

if (-not (Test-Path -LiteralPath "$releaseVenv\Scripts\python.exe")) {
    throw "Run setup_environment.ps1, then test Sidro from source before building."
}
$pythonExe = "$releaseVenv\Scripts\python.exe"
function Invoke-ReleasePython {
    & $pythonExe @args
    if ($LASTEXITCODE -ne 0) { throw "Release command failed: $args" }
}

Write-Host "Building Sidro 1.0 portable CPU release (RapidOCR only)..."
Invoke-ReleasePython -B tools\check_dependencies.py
Invoke-ReleasePython tools\make_windows_icon.py
Invoke-ReleasePython tools\prepare_rapidocr_models.py

if (Test-Path -LiteralPath ".\build") {
    $releaseBuildPath = (Resolve-Path -LiteralPath ".\build").Path
    if ($releaseBuildPath -ne (Join-Path $PSScriptRoot "build")) { throw "Unsafe build cleanup target." }
    Remove-Item -LiteralPath $releaseBuildPath -Recurse -Force
}
if (Test-Path -LiteralPath ".\dist") {
    $releaseDistPath = (Resolve-Path -LiteralPath ".\dist").Path
    if ($releaseDistPath -ne (Join-Path $PSScriptRoot "dist")) { throw "Unsafe dist cleanup target." }
    Remove-Item -LiteralPath $releaseDistPath -Recurse -Force
}
Invoke-ReleasePython -m PyInstaller Sidro.spec --noconfirm --clean
Invoke-ReleasePython -m PyInstaller --onefile --windowed --name SidroUpdater --icon "$PSScriptRoot\Sidro.ico" --version-file "$PSScriptRoot\tools\updater_version.txt" --distpath "dist\Sidro" --workpath "build\updater" --specpath "build" updater_helper.py --noconfirm --clean

Copy-Item -LiteralPath ".\README.md" -Destination ".\dist\Sidro\README.md" -Force
Copy-Item -LiteralPath ".\.env.example" -Destination ".\dist\Sidro\.env.example" -Force
Copy-Item -LiteralPath ".\app_settings.example.json" -Destination ".\dist\Sidro\app_settings.example.json" -Force
Copy-Item -LiteralPath ".\requirements-rapidocr-test.txt" -Destination ".\dist\Sidro\requirements-rapidocr-test.txt" -Force
Copy-Item -LiteralPath ".\requirements-paddleocr-test.txt" -Destination ".\dist\Sidro\requirements-paddleocr-test.txt" -Force
Copy-Item -LiteralPath ".\requirements-windowsocr.txt" -Destination ".\dist\Sidro\requirements-windowsocr.txt" -Force
Invoke-ReleasePython tools\bundle_release_materials.py
Invoke-ReleasePython tools\make_release_zip.py

Write-Host ""
Write-Host "Built portable app:"
Write-Host "  dist\Sidro\Sidro.exe"
Write-Host ""
Write-Host "User-specific files are created at:"
Write-Host "  %LOCALAPPDATA%\Sidro\"
Write-Host "Release upload: dist\Sidro-windows-v1.0.zip"
