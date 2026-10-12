Set-Location -LiteralPath $PSScriptRoot
$env:TTB_OCR_ENGINE = "windowsocr"
$env:WFM_OCR_GPU = "0"

& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
