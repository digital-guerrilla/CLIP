# CLIP: Construction Lineage Information Protocol

CLIP uses native IFCX entities, schema-typed components, hierarchy and type
inheritance. Organizations identify themselves with `did:web`; contractors
propose signed component changes and owners separately accept or reject them.
No v3 API, identifier, SDK or signature compatibility is provided. Existing
legacy database tables are not converted or deleted.

The IFCX alpha vocabulary is pinned to buildingSMART/IFC5-development commit
`1a63082ada967c683cfacee2005f8f749c8e1b79`. This is a research implementation,
not a certified IFC5 or production trust service.

## Quick Start

Windows PowerShell 7 and Python 3.11 or later:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\examples\run-network.ps1
.\examples\verify-network.ps1
```

The six authorities run on ports 8101-8106: manufacturer, supplier, main
contractor, owner, inspector and relay. Open <http://127.0.0.1:8104/ui> or
<http://127.0.0.1:8104/docs>. The seed includes a hierarchical North Wing IFC
model with inherited door/pump product data, signed installation and
commissioning history, manufacturer-to-contractor sourcing, private delivery
documents, owner acceptance, inspector handover, a Viewer invitation, encrypted
evidence fragments, signed replication receipts and five DID gossip peers. The
local demo grants unauthenticated local operator access to all six nodes; keep
it on loopback and never deploy the demo configuration. See the walkthrough in
[Supply-chain workflows](docs/supply-chain-workflows.md).

Business-role consoles open on Projects; the authority network map remains in
Overview. Verified imports, accepted
contributions, replica placements and gossip membership have distinct connections.
The facility portfolio drills into a searchable containment tree and asset details,
including inherited product data, installation status and publisher origins. Use the
key icon to authorize peer membership and replication controls. The diagram is a
dependency/receipt view, not live packet telemetry. D3 and Lucide are bundled locally.

Demo node operator access is automatic. On secured nodes, use the key icon to
authorize before creating a private project, adding organisation DIDs as Viewers
or Contributors, or creating supported IFC entities without raw JSON.
Entities inherit their project's access. Manufacturers can create a separate
public product library. The entity diagram shows containment and type inheritance;
selecting a node opens its properties, inherited access and signed accepted history.
Local edits still produce a signed proposal, authority decision and receipt.

The supply-chain workspaces provide guided product data and documentation
editing, direct/manufacturer or supplier component sourcing, public product and
offering revisions, private project supplies, installation allocations and
versioned contractor/client submissions. Each organisation controls its own
records; downstream acceptance creates linked local records and preserves the
original signed revisions. See [Supply-chain workflows](docs/supply-chain-workflows.md)
for the console walkthrough, API contracts and document-access boundaries.

Project graph resolution shares manufacturer product definitions by original
authority DID and product ID across direct supply, supplier chains and nested
components. Authorised console refreshes resolve verified published updates once
for every reference; pinned graph views and signed issue history remain unchanged.

To invite another organisation, open **Projects > Invite**, select Viewer or
Contributor, and generate a code. On their own node, the invited organisation
chooses **Enter invite**, pastes the code and selects **Propose connection**.
The owner reviews **Join requests** and explicitly accepts or rejects it.
Redeeming a code never grants access by itself. Each organisation authorizes its
own node; no operator API key is sent to the other organisation.

Codes are single-use, expire after seven days by default (configurable from one
hour to thirty days), and can be revoked in **Issued invites**. The first signed
join request reserves the code; retries by that organisation reuse its pending
request. Only token hashes are stored, so the code is shown only when generated.
Signed requests and owner decisions are retained; acceptance rechecks the
request's DID key authorization and project revision before adding membership.
Expired or revoked pending invites cannot be accepted. Invite acceptance grants
project membership, not ownership, automatic asset acceptance, linked-project
sharing, or retroactive document-key envelopes.

Guided mappings currently cover site, building, storey, space, zone/group,
door/pump types and occurrences, and physical assemblies. Concrete classes and
type pairings are checked against `IFC4X3_ADD2`; this is semantic authoring into
the pinned IFCX profile, not complete IFC4.3 STEP generation or IFC5 certification.
Zones/groups are not physical containment parents.

`run-network.ps1 -KeepData` preserves keys and databases, but seeding an already
registered dataset returns 409. To revisit persisted state, start services
without reseeding or use the verification script. A fresh demo start removes
the demo databases, SQLite sidecars, document storage and generated seed-state
file, while retaining authority keys.

## Implemented

- IFCX dataset registration, composition, effective components and accepted history.
- DID-authorized `eddsa-jcs-2022` proposals, owner decisions and signed receipts.
- Default DID gossip with persisted generations and offline/rejoin probes.
- DID-service proof-bundle replication with signed, verified acknowledgements.
- Signed encrypted evidence manifests, X25519 recipient key envelopes, fragment
  receipts, retention sweeps, replication and digest-checked repair.
- IFC4.3 STEP conversion through IfcOpenShell; COBie Component/Type CSV mapping.
- Explicit versioned construction event, source, reference and evidence schemas.
- Publisher allowlists, key validity/revocation policy and DNS-pinned outbound HTTP.
- Numbered checksum-verified database migrations and byte-pinned import caching.
- CLIP network / IFC graph Python SDK and visual network/facility/asset console.
- Private-by-default projects, revision-checked Viewer/Contributor membership,
  separate public product libraries and signed graph-operation authoring.
- Audience-bound DID project reads including pending proposal status; project
  membership is checked at retrieval and applies to newly created records.
- Project-derived evidence recipients at upload and permission-gated placement.
- Organisation-owned product/offering/supply/installation/asset records, signed
  immutable catalogue revisions and recursive source lineage.
- Scoped project submission senders, persisted joined projects, signed directed
  issues and decisions, correction references and idempotent acceptance.
- Guided record/property/source editors, public disclosure previews, inbox/outbox,
  source-controlled document grants and quantity/serial allocation validation.
- Expiring, revocable project invite codes with DID-signed pending join requests
  and owner-only acceptance/rejection into Viewer or Contributor membership.

## API

Network infrastructure and business exchange use **`/clip/v1`**; graph data,
relationships, transactions and IFC imports use **`/ifc/v1`**. Discovery is
`/.well-known/did.json`. There are no old `/ifcx/v1` or `IFCX_*` aliases.
Actual IFCX serialization fields and semantics remain unchanged.

| Surface | Base | Routes |
|---|---|---|
| Datasets | `/ifc/v1` | `GET/POST /datasets`, `GET /datasets/{id}/publication`, `/graph`, `/components`, `/history` |
| Projects/access | `/clip/v1` | `GET/POST /projects`, `PUT /projects/{id}/permissions` |
| Graph authoring | `/ifc/v1` | `GET /projects/templates`, `POST /projects/{id}/entities` |
| Partner project reads | `/clip/v1` | `POST /projects/read` using signed `projectRead` / `projectView` messages |
| Supply-chain authoring | `/clip/v1/supply-chain` | `/records`, `/dependencies/preview`, `/catalogue`, `/catalogue/discover` |
| Project destinations | `/clip/v1/supply-chain` | `/projects`, `/projects/connect`, `/projects/refresh`, `/projects/{id}/senders` |
| Directed submissions | `/clip/v1/supply-chain` | `/submissions`, `/submissions/{id}/issue`, `/submissions/{id}/decision`, `/receive` |
| Workflow documents | `/clip/v1/supply-chain` | `/records/{id}/documents`, `/records/{id}/documents/{documentId}/grants` |
| Invite codes | `/clip/v1` | `GET/POST /projects/{id}/invites`, `POST /projects/invites/{inviteId}/revoke` |
| Invite redemption | `/clip/v1` | Local `POST /projects/invites/redeem`; signed peer `POST /projects/invites/receive` via `ClipProjectInviteService` |
| Join review | `/clip/v1` | `GET /projects/{id}/join-requests`, `POST /projects/join-requests/{requestId}/decision` |
| Agreements | `/ifc/v1` | `POST /proposals`, `POST /decisions`, `PUT /datasets/{id}/trusted-proposers` |
| Membership | `/clip/v1` | `POST /network/gossip/sync`, `GET /network/gossip/peers` |
| Replication | `/clip/v1` | `POST /replication/push`, `/receive`, `GET /replication/status` |
| Evidence | `/clip/v1` | `POST /evidence/upload`, `GET /evidence/{id}`, `/fragments/{index}` |
| Evidence durability | `/clip/v1` | `POST /evidence/{id}/replicate`, `/repair`, `POST /evidence/retention/sweep` |
| Conversion | `/ifc/v1` | `POST /imports/cobie`, `/imports/ifc43`, `GET /imports/schemas` |
| Operations | `/clip/v1` | `/node/info`, `/node/storage`, `/node/storage/opt-in`, `/node/offline` |

Unmanaged existing datasets retain legacy-public access. Public projects and
libraries allow anonymous reads; private project datasets are hidden from
unauthorised listing, graph, component, publication and history requests. Local
operator writes and evidence reads require the node API key. Partner project
reads, proposals, decisions and cross-node traffic require DID proofs, not shared
API keys. Contributors may propose changes; Viewer access does not grant write
or acceptance authority. The operator key is a whole-node permission, not
individual-user role management. Full-authority replication is denied unless the
destination can read every project represented by the authority.

The supply-chain service supports explicit remote project destinations, supplier
catalogues and scoped immutable issues through manufacturer-to-client handover.
Global federated asset search and automatic sealed evidence-key grants for
members added after an original fragment upload remain unimplemented. Workflow
document grants are brokered by their issuer, not anonymous sealed-key delivery.
Revoking access blocks subsequent authorised reads but cannot recall previously
downloaded records or document keys. Individual-user OAuth/OIDC and production
access-control qualification remain separate work.

Evidence upload returns an `evidenceReference`; attaching it to an entity still
requires the normal signed proposal and owner acceptance. Upload alone does not
mutate the construction graph. The SDK decrypts evidence locally for an intended
recipient, so replica storage never needs the document key.

## Deployments

```powershell
docker compose -f docker-compose.demo-6node.yml up --build -d
.\examples\seed-network.ps1
.\examples\verify-network.ps1
```

The Docker demo shares a loopback network namespace and mounts `examples/data`
for the seeder's signing keys. Stop local demo processes first; both demos use
ports 8101-8106. Docker writes Linux-accessible files into that demo directory.

The main Compose file is a single production-configured authority and requires
explicit DID, verification method, HTTPS base URL and operator key environment
variables. Terminate TLS and restrict inbound/outbound traffic at the deployment
boundary. See [Operations](docs/operations.md) for the full policy.

## Verification

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
.\examples\demo-offline.ps1
pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_ifc_interop.py
```

The opt-in integration regression starts six isolated authorities on 8301-8306,
then tests restart, offline/rejoin, transaction replication, corrupt-cache
recovery, evidence placement and fragment repair. It cleans up its own processes
and temporary storage. Interoperability fixtures use separate JCS and Ed25519
implementations; they are not an external IFCX certification.

See [Architecture](docs/architecture.md), [Protocol](docs/instance-dependency-network.md)
and [Roadmap](docs/roadmap.md) for semantics and remaining qualification work.