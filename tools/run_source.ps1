param([Parameter(Mandatory)][string]$Script, [string[]]$Arguments = @())
$ErrorActionPreference = "Stop"
$sourceRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $sourceRoot
$sourcePython = Join-Path $sourceRoot ".venv-sidro\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $sourcePython)) {
    throw "Run setup_environment.ps1 before starting Sidro from source."
}
& $sourcePython -B "$PSScriptRoot\check_dependencies.py"
if ($LASTEXITCODE -ne 0) { throw "Sidro dependency baseline differs from the lock." }
& $sourcePython (Join-Path $sourceRoot $Script) @Arguments
if ($LASTEXITCODE -ne 0) { throw "Sidro exited with code $LASTEXITCODE." }
