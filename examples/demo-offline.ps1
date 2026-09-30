param([switch]$KeepData)

# Self-contained demo of the cache/canonical-data story: an independent two-node
# harness (not the shared 6-node demo) so it never interferes with
# run-network.ps1 / seed-network.ps1 / verify-network.ps1 state.
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
$dataDirectory = Join-Path $PSScriptRoot "data/offline-demo"
if (-not $KeepData) { Remove-Item $dataDirectory -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Path $dataDirectory -Force | Out-Null

$authority = @{ Port=8901; Key="authority-key"; Base="http://127.0.0.1:8901" }
$resolver  = @{ Port=8902; Key="resolver-key";  Base="http://127.0.0.1:8902" }

foreach ($port in @($authority.Port, $resolver.Port)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "Port $port is already in use. Stop that process and run the demo again."
    }
}

function Start-DemoNode($name, $port, $key, $role, $cacheTtl) {
    $command = @"
`$host.UI.RawUI.WindowTitle = 'CLIP offline-demo $name :$port'
Set-Location '$root'
`$env:NODE_DOMAIN = '127.0.0.1:$port'
`$env:NODE_API_BASE = 'http://127.0.0.1:$port'
`$env:API_KEY = '$key'
`$env:DATABASE_URL = 'sqlite+aiosqlite:///./examples/data/offline-demo/$name.db'
`$env:PRIVATE_KEY_FILE = './examples/data/offline-demo/$name.key'
`$env:DOCUMENT_STORAGE_DIR = './examples/data/offline-demo/documents/$name'
`$env:GOSSIP_SEEDS = ''
`$env:NODE_ROLE = '$role'
`$env:CACHE_TTL = '$cacheTtl'
`$env:DID_WEB_ID = 'did:web:127.0.0.1%3A$port'
& '.\.venv\Scripts\python.exe' -m uvicorn node.app.main:app --port $port --log-level warning
"@
    Start-Process pwsh -ArgumentList "-NoProfile", "-NoExit", "-Command", $command
}

Write-Host "Starting a throwaway authority + resolver node pair..." -ForegroundColor Cyan
Start-DemoNode "authority" $authority.Port $authority.Key "manufacturer" 3600
Start-DemoNode "resolver" $resolver.Port $resolver.Key "resolver" 2

try {
    foreach ($node in @($authority, $resolver)) {
        $deadline = (Get-Date).AddSeconds(30)
        while ($true) {
            try { Invoke-RestMethod -Uri "$($node.Base)/v3/node/info" -TimeoutSec 2 | Out-Null; break }
            catch {
                if ((Get-Date) -gt $deadline) { throw "Node on port $($node.Port) did not become ready" }
                Start-Sleep -Milliseconds 500
            }
        }
    }

    Write-Host "Publishing a public record on the authority node..." -ForegroundColor Cyan
    $record = Invoke-RestMethod -Method Post -Uri "$($authority.Base)/v3/records" `
        -Headers @{ "x-api-key"=$authority.Key } -ContentType "application/json" `
        -Body (@{ record_kind="type"; subject=@{ name="Offline demo pump"; model_number="PMP-OFFLINE-1" } } | ConvertTo-Json -Depth 8)

    Write-Host "Resolving it through the resolver node to populate its cache..." -ForegroundColor Cyan
    $graphBody = @{ root=$record.id; depth=0; max_nodes=1; view="public" } | ConvertTo-Json
    $first = Invoke-RestMethod -Method Post -Uri "$($resolver.Base)/v3/resolve-graph" -ContentType "application/json" -Body $graphBody
    $firstNode = $first.nodes.($record.id)
    if ($firstNode.source -ne "remote_authoritative") { throw "Expected the first resolution to fetch live from the authority, got '$($firstNode.source)'" }
    Write-Host "Confirmed: resolver fetched the record live (source: remote_authoritative)." -ForegroundColor Green

    Write-Host "Taking the authority node offline (simulated)..." -ForegroundColor Cyan
    Invoke-RestMethod -Method Post -Uri "$($authority.Base)/v3/node/offline" `
        -Headers @{ "x-api-key"=$authority.Key } -ContentType "application/json" `
        -Body (@{ offline=$true } | ConvertTo-Json) | Out-Null

    # Wait past the resolver's 2s CACHE_TTL so it re-checks the authority instead of serving its fresh cache.
    Start-Sleep -Seconds 3
    $second = Invoke-RestMethod -Method Post -Uri "$($resolver.Base)/v3/resolve-graph" -ContentType "application/json" -Body $graphBody
    $secondNode = $second.nodes.($record.id)
    if ($secondNode.status -ne "verified_stale") { throw "Expected the record to be served as verified_stale, got '$($secondNode.status)'" }
    if ($secondNode.source -ne "local_cache") { throw "Expected the record to come from local_cache, got '$($secondNode.source)'" }
    Write-Host "Confirmed: resolver served its cached copy as verified_stale while the authority was offline." -ForegroundColor Green

    Invoke-RestMethod -Method Post -Uri "$($authority.Base)/v3/node/offline" `
        -Headers @{ "x-api-key"=$authority.Key } -ContentType "application/json" `
        -Body (@{ offline=$false } | ConvertTo-Json) | Out-Null
    Write-Host "Authority node brought back online." -ForegroundColor Green
} finally {
    Write-Host "Stopping the offline-demo node pair..." -ForegroundColor Cyan
    Get-NetTCPConnection -LocalPort $authority.Port, $resolver.Port -State Listen -ErrorAction SilentlyContinue |
        ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
    Start-Sleep -Milliseconds 500
    if (-not $KeepData) { Remove-Item $dataDirectory -Recurse -Force -ErrorAction SilentlyContinue }
}

Write-Host "Offline/cache fallback demo complete." -ForegroundColor Green
