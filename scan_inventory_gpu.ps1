$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:WFM_OCR_GPU = "auto"
& "$PSScriptRoot\tools\run_source.ps1" -Script "inventory_scanner.py"
