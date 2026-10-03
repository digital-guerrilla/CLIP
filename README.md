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

The eight authorities run on ports 8101-8108: Northstar manufacturer (8101),
wholesale supplier (8102), main contractor (8103), client/owner (8104), inspector
(8105), Aster component manufacturer (8106), regional supplier (8107) and specialist
supplier (8108). Open <http://127.0.0.1:8104/ui> or
<http://127.0.0.1:8104/docs>. The seed includes a hierarchical North Wing IFC
model with inherited door/pump product data, signed installation and
commissioning history, manufacturer-to-contractor sourcing, private delivery
documents, owner acceptance, inspector handover, a Viewer invitation, encrypted
evidence fragments (stored by the inspector), signed replication receipts and seven DID gossip peers.
It also seeds a large six-facility portfolio and post-install update reviews described below;
allow several minutes for the actual signed workflows to complete.
The seeder displays a terminal progress bar with completed work steps, elapsed
time and the current facility/procurement route. Percentages measure completed
work, not estimated time; verification must finish before it reaches 100%.
Redirected output uses periodic progress lines instead of terminal redraws.
Seeding waits for each authority's API, not random gossip contact. Peer discovery
converges in the background and is checked during final verification with a
bounded two-minute wait. The eight-node demo gossips every
15 seconds to avoid flooding its local SQLite databases during bulk authoring.
The local demo grants unauthenticated local operator access to all eight nodes; keep
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
Initial page loading uses the already verified manufacturer revision cache rather
than refreshing every remote catalogue. Use **Refresh** to fetch current published
manufacturer updates explicitly. Update-enabled graph requests are serialized to
avoid competing cache writes; ordinary graph loads have bounded concurrency.
Supply-chain revision histories are loaded with one authorized bulk read, not one
request per record.

The demo uses one Northstar door product identity and one P-100 pump product
identity across its imported IFC example and North Wing Renewal. Renewal has two
distinct door installations in the lobby/corridor and two pump installations:
one supplied through the supplier/contractor chain and one directly from the
manufacturer. Northstar's pump references the independently published Aster M-5
motor from the second manufacturer on port 8106. Both manufacturers' signed
product revisions federate into the same project; the motor is a separate
component type, not another P-100. The inspector also demonstrates optional
evidence storage and replica placement, so a dedicated relay is no longer needed.
The entity network shares type nodes by manufacturer identity (never by name),
keeps physical assets and delivery records separate, and labels **Contains**,
**Type**, **Direct supply / Supply via**, **Allocated from** and **Component type**
relationships separately. Catalogue membership is **Lists type**, not a physical
installation. Rebuild existing demo data with a fresh `run-network.ps1` start
(stop all demo services first); `-KeepData` does not migrate old seed data.
North Wing Campus, its building, storey, spaces, groups and pump assembly are
authored once in the owner's spatial dataset. Renewal installations reference
those exact locations by authority DID, dataset ID and entity path through
`source.properties.locationReference`; they do not recreate the campus.
Selecting the Renewal dataset also includes its referenced locations and their
containment ancestors, but not unrelated assets from the spatial dataset.
Explicit `urn:clip:construction:entity-identity:v1` component addresses can identify
contributions to the same entity across datasets. Their diagram node retains the
contributing record addresses and provenance; matching names alone never merge.

Diagram arrows show **contribution flow**: component type to assembled product,
product type to installed asset, upstream source to offering/delivery, and
delivery or work event to the asset. **Contains** remains parent to child and is
visually distinct. IFC dependency and inheritance data are not rewritten merely
to reverse their presentation.
Both manufacturers use the manufacturer business role, with separate databases
and signing keys (`manufacturer` and `component_manufacturer`).

### Large portfolio and updates

Alongside North Wing, the seed creates **South Hospital, East Logistics Centre,
West Research Labs, Central Library, Riverside Leisure Centre and Hilltop School**.
Each has a campus, building, two floors and six spaces, authored once in the
owner's spatial dataset. Its private renewal project references these locations.
The portfolio adds **108 serialized installations and 324 signed work events**,
giving the client an entity network of **more than 500 unique nodes**.

Eight shared products cover doors, pumps, motors, valves, fans, filters, sensors
and controllers. Northstar pumps and fans incorporate Aster motors; Aster
controllers incorporate Aster sensors. Product identities stay shared across
facilities, while individual installations retain their own serials and records.
Each new facility demonstrates six routes:

| Route | Installation / acceptance |
| --- | --- |
| Manufacturer → wholesale supplier → contractor | Contractor accepts supply, installs and hands over to client |
| Manufacturer → regional supplier → contractor | Separate supplier and contractor installation workflow |
| Manufacturer → specialist supplier → client | Client accepts supply and self-installs |
| Manufacturer → wholesale supplier → regional supplier → client | Two-tier distribution, client self-installation |
| Northstar → client | Factory-direct purchase and client self-installation |
| Aster → client | Direct component purchase and client self-installation |

Every new installation has an installation event and two inspector-authored
inspection events. Each delivery's first asset fails its initial inspection and
passes a remedial reinspection; the others pass both initial and periodic checks.
These are real signed proposals accepted by the client, not decorative graph nodes.

After installation, Northstar publishes additional pump service guidance and an
updated door certificate; Aster publishes controller integration guidance.
Open **Incoming** on each supplier to review its superseding catalogue issue.
The regional supplier also publishes an updated pump offer (six-year warranty)
and leaves its correction pending at the contractor. A contractor handover
supplement remains pending at the client (South Hospital). There are **five
delivered, undecided updates**, each linked to an already accepted baseline.
Use the comparison/review controls to accept, reject or request changes.

Published manufacturer metadata can advance the shared type on graph refresh;
this does **not** accept a commercial handover correction or rewrite the original
signed issue, supply allocation or installation history. Other suppliers keep
older source pins intentionally, so different adoption stages remain visible.
Select a facility's renewal project or right-click an asset for a readable lineage
within the large graph. No running databases are migrated automatically.

In **Assets**, drag a node to rearrange it; its connecting arrows follow.
Every node displays a small **Data owner** label. Shared product types identify
their manufacturer authority; facilities, assets and events identify their record
authority, and issued snapshots identify the upstream issuer. Hover for the full
owner DID. Record authority is distinct from an installer or event contributor.
Drag the background to pan, use the wheel to zoom, or choose **Fit entity network**.
Right-click any node, press **L** / **Shift+F10** on a focused node, or select it
and choose **Show lineage** to see just its upstream deliveries, offerings,
manufacturer types, component products and physical location ancestors.
Lineage does not pull in sibling assets merely because they share a type or room.
For a product type, it also includes the installations using that type.
Upstream issue snapshots are read-only provenance, not additional local assets.
**Show full graph** or **Escape** exits lineage. **Reset layout** restores the
automatic layout for the current view. Arrow keys move a focused node (Shift
moves further). Positions and zoom survive refreshes and selection changes in
the current page, separately for each dataset scope and lineage view; they do
not modify product/asset data or persist after a page reload.

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

`run-network.ps1 -KeepData` preserves keys, databases and documents, and skips
seeding. Stop the existing services first, then use this mode to apply code or
database connection-setting changes without rebuilding the demo. If no completed
seed state exists, the launcher warns that the preserved seed may be incomplete.
Use the verification script to check persisted state. A fresh demo start removes
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
ports 8101-8108. The Compose filename is retained for compatibility, but now starts
eight authorities. Docker writes Linux-accessible files into that demo directory.

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

The large-demo regression starts **eight isolated authorities on dynamically
allocated ports**, seeds the complete portfolio, checks its actual UI network
size and identities, and verifies that update issues remain undecided:

```powershell
$env:CLIP_INTEGRATION = "1"
.\.venv\Scripts\python.exe -m unittest tests.test_demo_graph -v
```

See [Architecture](docs/architecture.md), [Protocol](docs/instance-dependency-network.md)
and [Roadmap](docs/roadmap.md) for semantics and remaining qualification work.