param([switch]$KeepData)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dataDirectory = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

$seedList = "http://127.0.0.1:8101,http://127.0.0.1:8102,http://127.0.0.1:8103,http://127.0.0.1:8104,http://127.0.0.1:8105,http://127.0.0.1:8106"
$nodes = @(
    @{ Name="Manufacturer"; Port=8101; Role="manufacturer"; Key="manufacturer-key" },
    @{ Name="Supplier"; Port=8102; Role="supplier"; Key="supplier-key" },
    @{ Name="Contractor"; Port=8103; Role="main_contractor"; Key="contractor-key" },
    @{ Name="Owner"; Port=8104; Role="owner"; Key="owner-key" },
    @{ Name="Inspector"; Port=8105; Role="inspector"; Key="inspector-key" },
    @{ Name="Relay"; Port=8106; Role="relay"; Key="relay-key" }
)

foreach ($node in $nodes) {
    if (Get-NetTCPConnection -LocalPort $node.Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $($node.Port) is already in use. Stop that process and run the demo again."
    }
    if (-not $KeepData) {
        Remove-Item (Join-Path $dataDirectory "$($node.Role).db") -Force -ErrorAction SilentlyContinue
        Remove-Item (Join-Path $dataDirectory "documents/$($node.Role)") -Recurse -Force -ErrorAction SilentlyContinue
    }
}

foreach ($node in $nodes) {
    $command = @"
`$host.UI.RawUI.WindowTitle = 'CLIP $($node.Name) :$($node.Port)'
Set-Location '$root'
`$env:NODE_DOMAIN = '127.0.0.1:$($node.Port)'
`$env:NODE_API_BASE = 'http://127.0.0.1:$($node.Port)'
`$env:API_KEY = '$($node.Key)'
`$env:DATABASE_URL = 'sqlite+aiosqlite:///./examples/data/$($node.Role).db'
`$env:PRIVATE_KEY_FILE = './examples/data/$($node.Role).key'
`$env:DOCUMENT_STORAGE_DIR = './examples/data/documents/$($node.Role)'
`$env:GOSSIP_SEEDS = '$seedList'
`$env:NODE_ROLE = '$($node.Role)'
`$env:DID_WEB_ID = 'did:web:127.0.0.1%3A$($node.Port)'
& '.\.venv\Scripts\python.exe' -m uvicorn node.app.main:app --port $($node.Port) --log-level warning
"@
    Start-Process pwsh -ArgumentList "-NoProfile", "-NoExit", "-Command", $command
}

Write-Host "Launching six CLIP services on ports 8101-8106..." -ForegroundColor Cyan
& "$PSScriptRoot\seed-network.ps1"
Write-Host "Dashboard: http://127.0.0.1:8104/ui" -ForegroundColor Green