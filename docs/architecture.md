# CLIP 3.0 Architecture

## Trust Model

A CLIP identifies a logical record and binds it to an Ed25519 genesis-key
fingerprint. DNS and HTTP locate an authority for the current request; they do
not establish authority. A resolver accepts a record only when the signed
descriptor fingerprint matches the CLIP, the named verification method has the
required purpose, the record identity matches the request, and its RFC 8785
canonical proof verifies.

```mermaid
flowchart LR
    C[Client or CAFM] --> G[Graph resolver]
    G --> L[Local authoritative records]
    G --> K[Verified cache]
    G --> D[Signed authority discovery]
    D --> A[Independent authorities]
    A --> V[Schema and proof verification]
    V --> K
    K --> G
    G --> R[Partial graph response]
```

No node is a global registry. Every authority controls only its own records;
the owner controls the instance root and which external assertions it accepts.

## Asset Lifecycle

The lifecycle is an append-only evidence graph. Product facts, custody,
procurement, installation, inspection, maintenance, and decommissioning remain
signed by the organizations responsible for those claims.

```mermaid
sequenceDiagram
    participant M as Manufacturer
    participant S as Supplier
    participant C as Main contractor
    participant O as Asset owner
    participant I as Inspector
    participant R as Resolver

    M->>M: Publish type record
    O->>O: Publish physical instance root
    M->>O: Propose defines_type with pinned baseline
    O->>O: Verify manufacturer proof and accept
    S->>S: Publish custody assertion
    S->>O: Propose custody_event
    C->>O: Propose procured_under assertion
    I->>O: Propose inspected_by assertion
    O->>O: Append accepted links and sign new root versions
    R->>O: Resolve instance root
    par Independent child resolution
        R->>M: Verify type and baseline
        R->>S: Verify custody evidence
        R->>C: Verify procurement evidence
        R->>I: Verify inspection evidence
    end
    R-->>R: Return complete or explicit partial graph
```

A later manufacturer update cannot rewrite the installed baseline. The
`defines_type` edge pins the accepted type version and canonical SHA-256 digest.
Corrections publish a new record version or superseding assertion.

## Data Governance

CLIP separates control of evidence from aggregation of evidence. This avoids a
single asset database becoming an accidental owner of every participant's data.

```mermaid
flowchart TB
    subgraph Private systems
        ERP[Manufacturer ERP]
        SCM[Supplier SCM]
        CDE[Contractor CDE]
        FM[Owner CAFM]
    end
    ERP -->|minimal signed type projection| MT[Manufacturer CLIP]
    SCM -->|minimal signed custody projection| SA[Supplier CLIP]
    CDE -->|minimal signed project projection| CA[Contractor CLIP]
    FM -->|owner-controlled identity| IR[Instance root]
    MT -->|stakeholder proof + owner acceptance| IR
    SA -->|stakeholder proof + owner acceptance| IR
    CA -->|stakeholder proof + owner acceptance| IR
    IR --> RES[Verified graph response]
    MT -. no commercial source data copied .-> RES
    SA -. no invoice or margin copied .-> RES
    CA -. no private project file copied .-> RES
```

This addresses common governance failures:

| Governance problem | CLIP control |
|---|---|
| One party silently edits another's facts | Every authority signs only its own record or assertion |
| DNS or hosting takeover impersonates an issuer | The identifier binds to a key fingerprint |
| Aggregator becomes the source of truth | The owner stores signed pointers and acceptance, not copied claims |
| Product data changes after installation | Accepted type relationships pin version and digest |
| Supplier disappears | Verified cached evidence remains usable and is marked stale |
| Missing evidence is hidden | Graph responses preserve failed edges and explicit reason codes |
| Sensitive commercial data spreads | Authorities publish minimal projections from private source systems |
| Audit history is overwritten | Updates append immutable signed history versions |

## Record And Relationship Writes

Record publication builds the complete envelope, normalizes it through the
Pydantic wire model, canonicalizes it with RFC 8785, and signs every field except
`proof`. Storage retains the complete JSON document and received payload.

Cross-authority relationships use two proofs:

1. The target stakeholder signs a proposal whose `target` is its authoritative
   record.
2. The root owner resolves the target descriptor, verifies the assertion proof,
   appends an acceptance proof, and publishes a new signed root version.

Ordinary record updates cannot change the relationship list. This prevents a
write client from bypassing the consent workflow.

## Resolution And Failure

`POST /v3/resolve-graph` performs bounded breadth-first traversal with a visited
set, depth limit, and node limit. Links are followed only from verified records.
A missing child is returned as a failure entry and does not turn a verified root
into an HTTP error. A verified cached child can be returned as
`verified_stale` when its authority is unavailable.

The current implementation uses SQLite per node and local cache records. The
production controls still required are tracked in [Roadmap](roadmap.md).

## Backend Integration Flows

External systems enter through adapters rather than writing a second record
format. An owner can import COBie/CSV/JSON component rows through
`POST /v3/imports/assets`; the importer signs normal owner instance records,
extracts valid CLIPs from source fields into `subject.linked_clips`, preserves
source values and row provenance, and supports idempotent replay. Imported
links are resolved as `import_reference` graph links and remain distinct from
authority-signed relationships.

Contractor systems can query an authorized installed-asset projection through
`GET /v3/records/query` and submit bounded relationship proposal batches through
`POST /v3/relationships/bulk-proposals`. Installation proposals may carry
additional signed CLIP references to manufacturer, supplier, inspection, or
evidence records. The owner acceptance workflow remains the authority boundary;
graph resolution exposes the linked records and their provenance without
copying another authority's facts into the owner record.

Confidential evidence uses the authenticated fragment primitive in
`node/app/core/content_crypto.py`. A random SecretBox key encrypts each chunk
with its own nonce, while the manifest commits to every ciphertext and to the
reconstructed plaintext. A node holding a fragment can verify its digest but
cannot read it without the document key. This primitive is deliberately
separate from placement: threshold/quorum coding, recipient key wrapping,
storage receipts, repair, and authenticated peer transfer must be added before
it becomes the network's confidential document service.