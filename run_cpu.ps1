$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:WFM_OCR_GPU = "0"
& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
