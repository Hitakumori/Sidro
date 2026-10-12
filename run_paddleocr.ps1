# Request paddleocr for a source run through the shared locked environment; no packages are installed by this launcher.
Set-Location -LiteralPath $PSScriptRoot
$env:TTB_OCR_ENGINE = "paddleocr"
$env:WFM_OCR_GPU = "0"
$env:TTB_INVENTORY_OCR_DEBUG = "1"
$env:FLAGS_use_onednn = "0"
$env:FLAGS_use_mkldnn = "0"
$env:FLAGS_enable_pir_api = "0"
$env:FLAGS_enable_new_ir_in_executor = "0"
$env:GLOG_minloglevel = "2"
$env:FLAGS_minloglevel = "2"

& "$PSScriptRoot\tools\run_source.ps1" -Script "main.py"
