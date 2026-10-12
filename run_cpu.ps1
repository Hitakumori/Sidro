# Launch main.py after the shared dependency check. GPU-named entry points retain the supported CPU OCR baseline.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:WFM_OCR_GPU = "0"
& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
