# Prepare the shared Python 3.12 source/build environment from exact pins. Run source tests before packaging it.
param([string]$PythonExecutable = "py")
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$environmentPath = Join-Path $PSScriptRoot ".venv-sidro"
$environmentPython = Join-Path $environmentPath "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $environmentPython)) {
    if ($PythonExecutable -eq "py") {
        & $PythonExecutable -3.12 -m venv $environmentPath
    } else {
        & $PythonExecutable -m venv $environmentPath
    }
    if ($LASTEXITCODE -ne 0) { throw "Creating the Python 3.12 environment failed." }
}
& $environmentPython -c "import sys; assert sys.version_info[:2] == (3, 12), 'Python 3.12 required'"
if ($LASTEXITCODE -ne 0) { throw "The shared environment must use Python 3.12." }
# The full lock supplies dependencies explicitly, including the single cv2
# provider. Ordinary resolution would add a second, overlapping OpenCV wheel.
$bootstrapPins = @(Get-Content -LiteralPath requirements-release.lock | Where-Object { $_ -match '^(pip|setuptools)==' })
if ($bootstrapPins.Count -ne 2) { throw "The lock must pin pip and setuptools for environment setup." }
& $environmentPython -m pip install --no-deps @bootstrapPins
if ($LASTEXITCODE -ne 0) { throw "Installing the pinned packaging tools failed." }
& $environmentPython -m pip install --no-deps --no-build-isolation -r requirements-release.lock
if ($LASTEXITCODE -ne 0) { throw "Installing the locked dependencies failed." }
& $environmentPython -B tools\check_dependencies.py
if ($LASTEXITCODE -ne 0) { throw "Environment verification failed; see the baseline document." }
Write-Host "Source launchers and release builders now share .venv-sidro."
