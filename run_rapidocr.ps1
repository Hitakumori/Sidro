# Request rapidocr for a source run through the shared locked environment; no packages are installed by this launcher.
Set-Location -LiteralPath $PSScriptRoot
$env:TTB_OCR_ENGINE = "rapidocr"
$env:WFM_OCR_GPU = "0"
$env:TTB_INVENTORY_OCR_DEBUG = "1"

& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
