$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
$previousIntegration = $env:CLIP_INTEGRATION
try {
    $env:CLIP_INTEGRATION = "1"
    & ".\.venv\Scripts\python.exe" -m unittest discover -s tests -p test_clip_network_integration.py -v
    if ($LASTEXITCODE -ne 0) { throw "CLIP network recovery regression failed with exit code $LASTEXITCODE" }
} finally {
    $env:CLIP_INTEGRATION = $previousIntegration
    Pop-Location
}