# CLIP 3.0: The Evidence Graph for Physical Assets

## Elevator Pitch

CLIP is a federated protocol for identifying physical assets and verifying the
evidence accumulated across their lifecycles. Instead of copying every
manufacturer, supplier, contractor, and inspector record into one central
database, CLIP gives each authority control of its own signed records. The
asset owner maintains the graph root and accepts the relationships that belong
in the asset's history.

When someone needs to inspect an asset, a resolver follows the graph, verifies
each authority and signature, and returns the evidence that caller is allowed
to see. The result is a durable, auditable view of an asset that can survive
organizational boundaries, changing hosting, and temporary outages without
pretending that one system owns everybody else's data.

In one sentence: **CLIP turns an asset's lifecycle evidence into a verifiable,
owner-controlled graph without creating a central registry.**

## 20-Minute Presentation

### 1. The problem: lifecycle evidence is fragmented (2 minutes)

Introduce a physical asset such as an air-handling unit in a hospital.

Its useful history is distributed across several organizations:

- The manufacturer knows what product type was produced.
- The supplier knows what was shipped and delivered.
- The contractor knows what was procured and installed under a project.
- The inspector knows whether commissioning passed.
- The owner knows which physical unit is installed, where it is, and what has
  been accepted into the asset record.

Those organizations do not share one database, and they should not necessarily
share all of their commercial or operational data. A central aggregator can
make lookup convenient, but it creates difficult questions: Who is allowed to
change another party's facts? What happens when the aggregator is unavailable?
Which copy is authoritative? How do we prove that a product specification was
not changed after installation?

### 2. The idea: a federated evidence graph (2 minutes)

CLIP models the asset as a graph rather than a single expanded record.

The owner's **instance record** is the root. It points to independently issued
records such as:

```text
Owner instance
  |-- defines_type   -> Manufacturer product type
  |-- custody_event  -> Supplier delivery assertion
  |-- procured_under -> Contractor project assertion
  `-- inspected_by   -> Inspector commissioning assertion
```

The root stores signed relationships and acceptance decisions, not a copied
version of every participant's source system. A resolver can retrieve the
current graph on demand, subject to visibility and traversal limits.

There is no global registry, shared database, mandatory relay, or ledger in the
protocol. A relay may improve availability, but it is not the source of truth.

### 3. Identity and trust: location is not authority (3 minutes)

A CLIP has this shape:

```text
clip://{routing-host}/{authority-key-fingerprint}/{uuid4}
```

The routing host is a starting location. It is not the trust anchor. The
authority key fingerprint binds the identifier to the authority's genesis
key, so a DNS or hosting change does not by itself allow somebody else to
forge the record.

When a resolver retrieves a record, it checks the trust chain:

1. Discover the authority through its signed descriptor.
2. Confirm that the descriptor's genesis key matches the fingerprint in the
   CLIP.
3. Confirm that the descriptor is valid for the requested protocol and proof
   purpose.
4. Canonicalize the record using RFC 8785 JSON Canonicalization Scheme.
5. Verify the Ed25519 signature over every field except the proof itself.
6. Confirm that the record identity and authority match the request.

This separates stable identity from replaceable infrastructure. Endpoints,
mirrors, and caches can change while the asset identity remains stable.

### 4. What a CLIP record contains (2 minutes)

CLIP 3.0 uses four record kinds:

- **Type:** a reusable manufacturer definition, such as an AHU-100 product
  type.
- **Instance:** the owner's identity for one physical or installed asset.
- **Assertion:** a stakeholder-owned lifecycle event or evidence package,
  such as delivery or inspection.
- **Collection:** an authority-owned assembly, system, space, or package.

Each record has a signed envelope containing its identity, authority,
controller, schema version, subject projection, relationships, availability
policy, timestamps, version, and proof. Updates append a new signed version;
history is not overwritten.

The important design choice is minimal projection. A supplier can publish the
facts needed to verify custody without publishing its invoices or margins. A
contractor can publish a procurement assertion without copying its entire
project file.

### 5. The consent process: proposal, verification, acceptance (3 minutes)

Cross-authority links require two parties to participate.

1. The stakeholder creates and signs a relationship proposal. The proposal
   names the owner's source record, the stakeholder's target record, the role,
   the relationship meaning, and any evidence or claims.
2. The owner resolves the target authority and verifies the stakeholder's
   assertion.
3. The owner accepts the proposal and signs the accepted relationship into a
   new root version.

This produces two proofs: the stakeholder's assertion and the owner's
acceptance. The owner cannot impersonate the supplier, and a write client
cannot silently add a relationship by editing the root directly.

For a manufacturer relationship, CLIP can pin the exact accepted baseline by
version and canonical SHA-256 digest. A later manufacturer update can be
published, but it cannot rewrite what was accepted at installation.

### 6. Resolution: verification before traversal (2 minutes)

`POST /v3/resolve-graph` starts at the owner-controlled root and performs a
bounded breadth-first traversal.

The resolver:

- follows links only from verified records;
- prevents cycles with a visited set;
- limits depth and node count;
- respects public, partner, and confidential views;
- records unavailable or restricted branches explicitly; and
- can return a verified stale cache entry when an authority is temporarily
  offline.

An incomplete child does not make a verified root disappear. The response
distinguishes a current record, a verified stale record, a restricted record,
and an unavailable or invalid branch. Missing evidence remains visible as a
diagnostic instead of being mistaken for evidence that no problem exists.

### 7. Live demonstration: six-node lifecycle (3 minutes)

Use the repository demo with six services:

| Service | Role | Responsibility |
|---|---|---|
| 8101 | Manufacturer | Product type and accepted baseline |
| 8102 | Supplier | Delivery and custody assertion |
| 8103 | Main contractor | Procurement assertion |
| 8104 | Owner | Physical instance root and acceptance decisions |
| 8105 | Inspector | Commissioning inspection assertion |
| 8106 | Relay | Independent resolution and verified cache |

Suggested narration:

1. Start the network and open the owner console at `http://127.0.0.1:8104/ui`.
2. Seed the manufacturer type, supplier delivery, contractor procurement,
   inspector result, and owner instance.
3. Point out that these are separate authoritative records, not five rows in
   one central database.
4. Show the relationship exchange: each stakeholder proposes a link and the
   owner accepts it.
5. Resolve the owner root through the relay. The expected result is five
   verified nodes and four accepted relationships.
6. Run the verification script and show that every relationship has two
   proofs and that the contractor cannot enumerate the owner's restricted
   instance or the inspector's restricted assertion.

The key moment is not the number of services. It is that the relay can verify
the graph without becoming the authority for any participant's claim.

### 8. Governance and failure behavior (2 minutes)

CLIP makes governance rules visible in the protocol:

- each authority signs only its own claims;
- owners accept external claims into their asset graph;
- restricted records replicate only to explicitly permitted nodes;
- public graph resolution never discloses a restricted root;
- caches preserve verified evidence but mark it stale when freshness expires;
- immutable versions preserve audit history; and
- failed edges carry status and reason codes.

This is useful in the real conditions of asset management: suppliers change,
systems are retired, networks fail, and evidence arrives at different times.
The graph can remain useful without hiding the parts that could not be
verified.

### 9. What CLIP is, and is not (1 minute)

CLIP is a protocol and a research implementation for self-certifying identity,
signed records, governed relationships, federated discovery, and bounded graph
resolution.

It is not a blockchain, a universal asset database, or a replacement for an
ERP, SCM, CDE, CAFM, or document-management system. Those systems remain the
systems of record. CLIP publishes the minimal signed projections and links
needed for independent verification.

The current repository is presentation-ready, not a production trust service.
Production deployment still needs HTTPS-only discovery, stronger service
authorization, key rotation and revocation, encrypted storage, migrations,
replica acknowledgements, size and SSRF controls, and independent
cross-language conformance testing.

### 10. Closing: the value proposition (2 minutes)

CLIP gives an owner a trustworthy answer to a practical question:

> “What do we know about this asset, who said it, who accepted it, can we
> verify it, and what is currently unavailable?”

It does so without requiring every organization to surrender control of its
records or place its trust in one central database. Identity remains stable,
evidence remains attributable, relationships are explicit, and resolution is
honest about uncertainty.

The next step is to connect those signed projections to real lifecycle systems
and validate the protocol with independent implementations. The architectural
promise is simple: **one physical asset, many authorities, one verifiable
graph.**

## Demo Preparation

From the repository root in PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
.\examples\run-network.ps1
.\examples\seed-network.ps1
.\examples\verify-network.ps1
```

The demo credentials are for demonstration only. The web console does not
contain API keys; private access is entered through the browser session and is
not a production authorization model.