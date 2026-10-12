# Run the standalone inventory scanner diagnostic and save local debug images. GPU-named aliases use the same CPU baseline.
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:WFM_OCR_GPU = "0"
& "$PSScriptRoot\tools\run_source.ps1" -Script "inventory_scanner.py"
