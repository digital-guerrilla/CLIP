# CLIP: Construction Lineage Information Protocol

CLIP 3.0 is a federated protocol for identifying physical assets and resolving
their independently governed lifecycle evidence. Each record is signed by its
issuer, each identifier binds to an authority key fingerprint, and an owner-held
instance record links manufacturer, supplier, contractor, inspector, and other
stakeholder assertions without copying their source data into one database.

This repository is a presentation-ready research implementation. It demonstrates
the protocol and failure model; it is not yet a production trust service. See
[the roadmap](docs/roadmap.md) for the remaining security and operational work.

## What Is Implemented

- Self-certifying identifiers: `clip://{routing-host}/{key-fingerprint}/{uuid4}`
- RFC 8785 JSON canonicalization and Ed25519 structured proofs
- Signed `3.0` authority descriptors and purpose-scoped verification methods
- `type`, `instance`, `assertion`, and `collection` records
- Stakeholder-signed relationship proposals and owner-signed acceptance
- Exact manufacturer baseline snapshots for `defines_type` relationships
- Bounded, cycle-safe graph resolution with verified cache fallback
- Signed public/restricted visibility and explicit per-node replication grants
- COBie/CSV/JSON owner asset import with CLIP extraction, deduplication, and idempotent replay
- Authorization-aware asset queries and contractor batch relationship proposals
- Authenticated encrypted evidence fragments with content-integrity manifests
- Six role-based demo services, gossip membership, Python SDK, and web console

The normative wire and resolution rules are in the
[CLIP Instance-Based Dependency Network](docs/instance-dependency-network.md).
The implementation view and governance diagrams are in
[Architecture](docs/architecture.md).

## Quick Start

Prerequisites:

- Windows PowerShell 7
- Python 3.11 or later

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\examples\run-network.ps1
```

The script starts and seeds six services:

| Port | Authority role | Demonstrated responsibility |
|---:|---|---|
| 8101 | Manufacturer | Product type and accepted baseline |
| 8102 | Supplier | Delivery and custody assertion |
| 8103 | Main contractor | Procurement assertion |
| 8104 | Owner | Physical instance root and acceptance decisions |
| 8105 | Inspector | Commissioning inspection assertion |
| 8106 | Relay | Independent federated graph resolution and cache |

Open the operations console at <http://127.0.0.1:8104/ui>. Verify the complete
federated graph independently through the relay:

```powershell
.\examples\verify-network.ps1
```

The seeded manufacturer type is public. The installed instance, delivery,
procurement, and inspection records are restricted to the Owner (`8104`) and
Relay client (`8106`). The verification script also proves that the Contractor
node cannot enumerate the Owner instance or Inspector assertion.

### Data Visibility

Visibility is part of the signed record envelope:

```json
{
  "availability": {
    "visibility": "restricted",
    "allowed_nodes": ["127.0.0.1:8104", "127.0.0.1:8106"]
  }
}
```

- `public` records may be fetched anonymously and replicated across live peers.
- `restricted` records are replicated only to the exact routing hosts in
  `allowed_nodes` and require that receiving node's API key for catalog, direct,
  graph, and document reads.
- Catalogs default to records authoritative on that node. `scope=network`
  exposes the node's permitted cache only to its authenticated local operator.
- `public` graph requests never disclose a restricted root. `partner` and
  `confidential` views require the local node API key.

The web console does not contain API keys. Keys entered through **Private
access** or write dialogs are retained only in browser session storage. The
example keys in the PowerShell scripts are demo credentials and must not be
used for a production deployment.

This policy provides API authorization and minimizes distribution. It does not
encrypt SQLite databases or document files at rest; production deployments
still require encrypted storage, secret management, authenticated service
identity, key rotation, and transport-layer access controls.

The encrypted fragment primitive in `node/app/core/content_crypto.py` is the
foundation for confidential evidence. It encrypts each chunk with an
authenticated nonce and produces a manifest committing to every ciphertext and
the reconstructed plaintext. The existing document endpoint still stores
public documents as complete files; quorum placement, recipient key wrapping,
and authenticated cross-node fragment transfer remain to be integrated.

To retain demo keys and databases between starts:

```powershell
.\examples\run-network.ps1 -KeepData
```

## Docker Demo

```powershell
docker compose -f docker-compose.demo-6node.yml up --build -d
.\examples\seed-network.ps1
.\examples\verify-network.ps1
```

The Docker network uses service names as routing hosts. Browser traffic enters
through the published ports; authorities resolve one another inside the Compose
network. The demo explicitly enables HTTP discovery for those private service
names; production deployments keep `ALLOW_INSECURE_HTTP_DISCOVERY=false` and use
HTTPS authority endpoints.

## Single Node

```powershell
$env:NODE_DOMAIN = "localhost:8000"
$env:NODE_API_BASE = "http://localhost:8000"
$env:API_KEY = "demo-key"
$env:DID_WEB_ID = "did:web:localhost%3A8000"
.\.venv\Scripts\uvicorn.exe node.app.main:app --port 8000
```

- Operations console: <http://localhost:8000/ui>
- OpenAPI: <http://localhost:8000/docs>
- Signed descriptor: <http://localhost:8000/.well-known/clip/server>

Publish a manufacturer type:

```powershell
$body = @{
  record_kind = "type"
  subject = @{
    name = "Fire Door FD60"
    manufacturer = "Example Manufacturing"
    model_number = "FD60-01"
  }
} | ConvertTo-Json -Depth 8

Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v3/records" `
  -Headers @{ "x-api-key" = "demo-key" } `
  -ContentType "application/json" -Body $body
```

Resolve a graph:

```powershell
$request = @{ root = "clip://..."; depth = 2; max_nodes = 50 } | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri "http://localhost:8000/v3/resolve-graph" `
  -ContentType "application/json" -Body $request
```

## API Surface

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/.well-known/clip/server` | Signed authority descriptor |
| `GET` | `/v3/node/info` | Node role and authority identity |
| `GET` | `/v3/node/access` | Validate local private-data access |
| `GET` | `/v3/node/storage` | Inspect encrypted storage opt-in and capacity |
| `GET` | `/v3/records` | List locally held records |
| `GET` | `/v3/records/query` | Query authorized installed assets by identity and location |
| `POST` | `/v3/records` | Publish an authoritative record |
| `POST` | `/v3/imports/assets` | Import COBie/CSV/JSON asset rows |
| `GET` | `/v3/imports/{import_id}` | Read import status and result |
| `GET` | `/v3/records/{authority}/{uuid}` | Fetch a local record |
| `PUT` | `/v3/records/{authority}/{uuid}` | Append a signed record version |
| `GET` | `/v3/records/{authority}/{uuid}/history` | Read immutable prior versions |
| `POST` | `/v3/relationships/proposals` | Stakeholder-sign a relationship |
| `POST` | `/v3/relationships/bulk-proposals` | Submit bounded contractor/supplier proposal batches |
| `POST` | `/v3/relationships/accept` | Verify and owner-accept a proposal |
| `POST` | `/v3/resolve-graph` | Resolve a bounded verified graph |
| `POST` | `/v3/federation/sync` | Replicate a verified signed record |
| `POST` | `/v3/documents/upload/{authority}/{uuid}` | Attach and distribute a document |
| `POST` | `/v3/documents/encrypted-upload/{authority}/{uuid}` | Attach an encrypted fragment document |
| `GET` | `/v3/documents/{sha256}` | Read an authorized document replica |
| `GET` | `/v3/documents/encrypted/{sha256}` | Reconstruct an authorized encrypted document |
| `POST` | `/v3/documents/encrypted-cleanup` | Remove expired encrypted fragments |
| `GET` | `/v3/gossip/peers` | Inspect peer membership |
| `GET` | `/v3/replication/status/{authority}/{uuid}` | Inspect persisted federation delivery jobs |
| `POST` | `/v3/replication/retry/{authority}/{uuid}` | Requeue authoritative record replication |

Writes require the node's `x-api-key`. Proof verification never treats that API
key as an identity credential; authority derives from Ed25519 keys and signed
descriptors.

## Test

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The regression test creates a fresh node, publishes a record, validates its
proof against discovery, retrieves it, resolves its graph, and verifies clean
database shutdown.

## Repository Layout

```text
client/                 Python SDK
docs/                   Protocol, architecture, and roadmap
examples/               Six-service launch, seed, and verification scripts
node/app/api/            HTTP routes
node/app/core/           Identifiers, models, canonicalization, and signing
node/app/db/             Persistence
node/app/federation/     Discovery, remote verification, and gossip
node/app/static/         Operations console
tests/                   Executable regression tests
```