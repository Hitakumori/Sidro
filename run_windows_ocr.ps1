# Historical Windows OCR launcher; the current Settings selector and portable release support RapidOCR and optional PaddleOCR.
Set-Location -LiteralPath $PSScriptRoot
$env:TTB_OCR_ENGINE = "windowsocr"
$env:WFM_OCR_GPU = "0"

& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
