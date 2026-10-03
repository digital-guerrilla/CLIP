param([switch]$KeepData)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dataDirectory = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

$seedDids = "did:web:127.0.0.1%3A8101,did:web:127.0.0.1%3A8102,did:web:127.0.0.1%3A8103,did:web:127.0.0.1%3A8104,did:web:127.0.0.1%3A8105,did:web:127.0.0.1%3A8106"
$nodes = @(
    @{ Name="Northstar Manufacturer"; Port=8101; Role="manufacturer"; Storage="manufacturer" },
    @{ Name="Supplier"; Port=8102; Role="supplier"; Storage="supplier" },
    @{ Name="Contractor"; Port=8103; Role="main_contractor"; Storage="main_contractor" },
    @{ Name="Owner"; Port=8104; Role="owner"; Storage="owner" },
    @{ Name="Inspector"; Port=8105; Role="inspector"; Storage="inspector" },
    @{ Name="Aster Motor Manufacturer"; Port=8106; Role="manufacturer"; Storage="component_manufacturer" }
)

foreach ($node in $nodes) {
    if (Get-NetTCPConnection -LocalPort $node.Port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $($node.Port) is already in use. Stop that process and run the demo again."
    }
    if (-not $KeepData) {
        $databasePath = Join-Path $dataDirectory "$($node.Storage).db"
        foreach ($databaseFile in @(
            $databasePath,
            "$databasePath-wal",
            "$databasePath-shm",
            "$databasePath-journal"
        )) {
            Remove-Item $databaseFile -Force -ErrorAction SilentlyContinue
        }
        Remove-Item (Join-Path $dataDirectory "documents\$($node.Storage)") -Recurse -Force -ErrorAction SilentlyContinue
    }
}

if (-not $KeepData) {
    Remove-Item (Join-Path $dataDirectory "clip-demo-state.json") -Force -ErrorAction SilentlyContinue
}

foreach ($node in $nodes) {
    $nodeDid = "did:web:127.0.0.1%3A$($node.Port)"
    $command = @"
`$host.UI.RawUI.WindowTitle = 'CLIP $($node.Name) :$($node.Port)'
Set-Location '$root'
`$env:NODE_DOMAIN = '127.0.0.1:$($node.Port)'
`$env:NODE_API_BASE = 'http://127.0.0.1:$($node.Port)'
`$env:CLIP_DEMO_OPEN_ACCESS = 'true'
`$env:DATABASE_URL = 'sqlite+aiosqlite:///./examples/data/$($node.Storage).db'
`$env:PRIVATE_KEY_FILE = './examples/data/$($node.Storage).key'
`$env:DOCUMENT_STORAGE_DIR = './examples/data/documents/$($node.Storage)'
`$env:CLIP_GOSSIP_ENABLED = 'true'
`$env:CLIP_GOSSIP_SEEDS = '$seedDids'
`$env:CLIP_TRUSTED_PUBLISHERS = '$seedDids'
`$env:CLIP_ALLOW_HTTP_LOOPBACK = 'true'
`$env:CLIP_GOSSIP_INTERVAL = '2'
`$env:NODE_ROLE = '$($node.Role)'
`$env:DID_WEB_ID = '$nodeDid'
`$env:DID_VERIFICATION_METHOD = '${nodeDid}#authority-key'
& '.\.venv\Scripts\python.exe' -m uvicorn node.app.main:app --host 127.0.0.1 --port $($node.Port) --log-level warning
"@
    Start-Process pwsh -ArgumentList "-NoProfile", "-NoExit", "-Command", $command
}

Write-Host "Launching six CLIP authorities with DID gossip on ports 8101-8106..." -ForegroundColor Cyan
& "$PSScriptRoot\seed-network.ps1"
Write-Host "CLIP network / IFC graph API docs: http://127.0.0.1:8104/docs" -ForegroundColor Green