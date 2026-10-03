param([switch]$KeepData)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dataDirectory = Join-Path $PSScriptRoot "data"
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

$nodes = @(
    @{ Name="Northstar Manufacturer"; Port=8101; Role="manufacturer"; Storage="manufacturer" },
    @{ Name="Wholesale Supplier"; Port=8102; Role="supplier"; Storage="supplier" },
    @{ Name="Contractor"; Port=8103; Role="main_contractor"; Storage="main_contractor" },
    @{ Name="Client / Portfolio Owner"; Port=8104; Role="owner"; Storage="owner" },
    @{ Name="Inspector"; Port=8105; Role="inspector"; Storage="inspector" },
    @{ Name="Aster Components Manufacturer"; Port=8106; Role="manufacturer"; Storage="component_manufacturer" },
    @{ Name="Regional Supplier"; Port=8107; Role="supplier"; Storage="supplier_2" },
    @{ Name="Specialist Supplier"; Port=8108; Role="supplier"; Storage="supplier_3" }
)
$seedDids = ($nodes | ForEach-Object { "did:web:127.0.0.1%3A$($_.Port)" }) -join ","

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
`$env:CLIP_GOSSIP_INTERVAL = '15'
`$env:NODE_ROLE = '$($node.Role)'
`$env:DID_WEB_ID = '$nodeDid'
`$env:DID_VERIFICATION_METHOD = '${nodeDid}#authority-key'
& '.\.venv\Scripts\python.exe' -m uvicorn node.app.main:app --host 127.0.0.1 --port $($node.Port) --log-level warning
"@
    Start-Process pwsh -ArgumentList "-NoProfile", "-NoExit", "-Command", $command
}

Write-Host "Launching eight CLIP authorities on ports 8101-8108..." -ForegroundColor Cyan
if ($KeepData) {
    Write-Host "Existing data preserved; skipping seeding." -ForegroundColor Cyan
    if (-not (Test-Path (Join-Path $dataDirectory "clip-demo-state.json"))) {
        Write-Warning "No completed demo seed state was found. The preserved data may contain an incomplete seed."
    }
} else {
    Write-Host "Large portfolio seeding may take several minutes..." -ForegroundColor Cyan
    & "$PSScriptRoot\seed-network.ps1"
}
Write-Host "CLIP network / IFC graph API docs: http://127.0.0.1:8104/docs" -ForegroundColor Green