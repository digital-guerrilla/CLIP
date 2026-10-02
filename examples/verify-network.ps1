$ErrorActionPreference = "Stop"
Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    & ".\.venv\Scripts\python.exe" "$PSScriptRoot\clip_network_demo.py" verify
    if ($LASTEXITCODE -ne 0) { throw "CLIP network verification failed with exit code $LASTEXITCODE" }
} finally {
    Pop-Location
}