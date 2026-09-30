$ErrorActionPreference = "Stop"
$state = Get-Content "$PSScriptRoot/data/demo-state.json" | ConvertFrom-Json
$graph = Invoke-RestMethod -Method Post -Uri "http://127.0.0.1:8106/v3/resolve-graph" `
    -Headers @{ "x-api-key"="relay-key" } `
    -ContentType "application/json" `
    -Body (@{ root=$state.root; depth=2; max_nodes=50; view="confidential" } | ConvertTo-Json)

if (-not $graph.complete) { throw "Graph is incomplete: $($graph.failures | ConvertTo-Json -Depth 8)" }
if (@($graph.nodes.PSObject.Properties.Name).Count -ne 5) { throw "Expected 5 verified records" }
if ($graph.edges.Count -ne 4) { throw "Expected 4 accepted relationships" }
if (@($graph.edges | Where-Object { $_.proofs.Count -ne 2 }).Count -ne 0) {
    throw "Every cross-authority relationship must have assertion and acceptance proofs"
}
if (@($graph.nodes.PSObject.Properties.Value | Where-Object { $_.status -ne "verified_current" }).Count -ne 0) {
    throw "Every record must be currently verified"
}

$contractorCatalog = Invoke-RestMethod -Uri "http://127.0.0.1:8103/v3/records?scope=network&limit=500" `
    -Headers @{ "x-api-key"="contractor-key" }
$inspectorHost = (Invoke-RestMethod -Uri "http://127.0.0.1:8105/v3/node/info").routing_host
$inspectorId = @($state.records | Where-Object { $_ -like "clip://$inspectorHost/*" })[0]
if (@($contractorCatalog.items | Where-Object { $_.id -eq $state.root -or $_.id -eq $inspectorId }).Count -ne 0) {
    throw "Contractor received restricted owner or inspection data without a grant"
}

try {
    Invoke-RestMethod -Uri "http://127.0.0.1:8104/v3/records/$($state.root.Split('/')[3])/$($state.root.Split('/')[4])" | Out-Null
    throw "Restricted owner record was returned without authorization"
} catch {
    if ([int]$_.Exception.Response.StatusCode -ne 403) { throw }
}

Write-Host "CLIP network verified: complete confidential graph and contractor isolation enforced." -ForegroundColor Green