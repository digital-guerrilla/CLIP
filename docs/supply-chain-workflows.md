# Supply-chain workflows

CLIP's supply-chain console and API preserve separate organisation-owned records.
A manufacturer definition, supplier offering, project delivery, contractor
installation and recipient asset are different records with explicit lineage.
Acceptance creates a recipient-owned record; it does not transfer control of the
original or certify technical completion.

## Console

Open `/ui` on your organisation's node. Use the key button to enter that node's
operator API key. The key stays in browser memory and is sent only to the local
node. Organisation signing and remote DID authentication happen on the server.
The current key grants whole-node operator access, not an individual-user role.

The console includes:

- **My products:** product data, technical properties, component specifications,
  private version freezing, existing local type adoption, document replacement
  and deliberate public publication.
- **My supplied products:** reusable offerings and separate private project
  deliveries with quantities, units, batches and serials.
- **Discover products:** public catalogue discovery using an explicit organisation
  DID, filters, read-only product details and source-version selection.
- **Projects:** local workspaces, invitations, remote project connections and
  separately approved submission senders.
- **Installations:** occurrences allocated from accepted supply, locations,
  installation/commissioning dates and technical status.
- **Submissions:** private draft preparation, immutable issue, incoming review,
  accept/reject/request-changes decisions, corrections and onward handover.
- **Documents:** document versions, issuer, visibility, digest, retrieval and
  source-controlled recipient grants.

Network, replication and raw transaction controls remain available separately.
An organisation can perform multiple business roles; labels do not confer rights.

### Manufacturer

1. Create a product with an IFC type class, model/SKU and technical properties.
2. Add components using either **Direct from manufacturer** or **Via supplier
   offering**. Select an exact immutable source revision, quantity and unit.
   Review the original manufacturer and all intermediate suppliers.
3. Upload documentation. Mark a document public only if its bytes may be publicly
   distributed; private uploads do not enter the public publication.
   Choose an existing owned attachment to replace it, or retire an attachment
   from the next working revision. Earlier issued revisions retain their original
   documents. Retirement is not deletion and does not revoke existing read grants.
4. Review and publish the product revision. Publishing captures the selected
   dependency closure and explicitly public document bytes.
5. Later edits create new working revisions. Existing downstream associations
   remain pinned; publication does not update them automatically.

### Supplier

1. Discover a manufacturer's catalogue or another supplier's public offerings.
2. Create an offering referencing one exact source revision. Add supplier-owned
   SKU, warranty, service information and documents.
3. Publish a reusable offering, or keep it private. Public catalogue records
   cannot be project-scoped or contain the reserved delivery/commercial fields.
   The disclosure preview is still essential for arbitrary additional properties.
4. Create project supply in a private local project, using a frozen offering.
   Record quantity/unit, batch, serials and delivery evidence.
5. Connect to the contractor's recipient project. The recipient can approve your
   DID as a scoped sender without making you a project Contributor.
6. Prepare a submission, select records and explicitly select documents, review
   disclosure, save a draft and issue it. A draft is not delivered.

### Contractor and client

1. Review an incoming supply issue, its signed source revisions, evidence and
   technical status. Accept, reject or request changes with a reason.
2. Acceptance produces local supply records. It does not record installation.
3. Create a compatible occurrence from the accepted supply. Allocate quantity,
   units and serials; duplicate serials and excess quantity are refused.
4. Record location, installer, dates, status and commissioning information.
   Upload installation evidence.
5. Submit to a main contractor or client project. A receiving contractor can
   accept a linked local asset and submit it onward again.
6. Corrections create a new issue referencing the earlier issue. Old signed
   snapshots and decisions remain available.

### Shared manufacturer types and published updates

The resolved IFC project graph has one canonical manufacturer definition for each
**manufacturer DID + original product record ID**, independently of supplier,
recipient, dataset, placement or component nesting. The resolver follows verified
dependencies and acceptance provenance, not product names, SKUs or unverified
lineage summaries. A product assembly retains its own identity; its component-type
references resolve independently. BOM references are not physical containment.

For example, a directly delivered motor and the same motor nested in an assembly
through three supplier offerings point to the same `manufacturer-types/...` node.
Occurrences inherit manufacturer fields through that node. Supplier warranties,
delivery quantities, serial allocations, locations and installation evidence stay
on their separate workflow/occurrence records. Identical product types do not
merge two physical pumps.

The eight-authority demo includes two independent manufacturers: Northstar on port
8101 publishes the door and P-100 pump, and Aster on port 8106 publishes the M-5
electric motor used by that pump. There is no dedicated demo relay; the inspector
demonstrates optional encrypted evidence storage and replica placement.
Three suppliers run on 8102 (wholesale), 8107 (regional) and 8108 (specialist).
The client on 8104 receives contractor handovers, accepts supplier deliveries
for self-installation and purchases directly from both manufacturers.
Six additional facilities contain 108 serialized installations and 324 signed
installation/inspection events, including failed inspections followed by remedial
reinspection. Each facility has its own renewal project but shares the one owner
spatial model and the original manufacturer product identities.

After these installations, three products receive new published catalogue
revisions. Each supplier has a delivered superseding issue awaiting review in
**Incoming**. The contractor has a pending updated regional pump offering, and
the client has a pending South Hospital handover supplement: five undecided
corrections in total. Each has an accepted baseline for comparison. These updates
add catalogue information/warranty terms rather than replacing serialized assets.
Shared type metadata refresh and explicit acceptance of a submission are separate:
new published data may be visible while the old signed handover remains accepted.

In **Facilities & assets**, drag graph nodes to rearrange them. Right-click an
asset (keyboard: **L** or **Shift+F10**, or use **Show lineage**) to filter to its
upstream accepted delivery, issued source snapshots, supplier offerings,
manufacturer definitions and component products. Only its location ancestors
are included, not other assets in the same space or using the same product type.
For a type, lineage also shows its using installations. Source snapshots are
read-only provenance, not editable recipient products. **Show full graph** or
**Escape** restores the complete graph; **Reset layout** resets the current
view. Dragging and keyboard arrow movement change the page layout only.

Arrows point in contribution-flow direction: component product to assembled
product type, product type to installed asset, and delivery/work event to asset.
Containment arrows remain campus to building to space to asset. The demo authors
that spatial structure only once; the Renewal project's installations reference
its exact owner/dataset/entity addresses rather than creating duplicate facilities.

The default graph view uses the newest verified **published** manufacturer revision
observed by the node. An authorised console refresh also fetches the manufacturers'
signed catalogues, so every reference resolves the update together, including
nested components. No supplier republication is required. Private working edits
never advance the shared definition. This is pull-on-refresh, not a background
push subscription. Initial page loading uses verified revisions already known to
the node; only an explicit **Refresh** requests new manufacturer publications.
The console serializes update-enabled graph requests because catalogue caching
can write to the local database.

Select an installed asset or its manufacturer type in **Facilities & assets**.
The inspector's **Manufacturer documents** section downloads public documents
from that resolved revision, including component-product documentation, directly
from the client's cached signed publication. These downloads do not require a
new private-document grant or rewrite the original accepted handover attachments.
Private evidence remains available through its existing record/submission controls.
The operator-authorized `GET /clip/v1/supply-chain/documents/{documentId}` route
accepts `authorityDid`, `recordId` and `revision` to read an exact cached public
document; it does not fall back to a different revision or private evidence.

- `GET /ifc/v1/datasets/{id}/graph?refresh_products=true` verifies and caches
  newly published revisions; it requires the local operator key.
- `GET /ifc/v1/datasets/{id}/graph?product_view=pinned` resolves each original
  pinned revision separately, even when two versions occur in the same project.
- Graph/component reads default to `product_view=current`; the component endpoint
  also supports `product_view=pinned`.
- Graph responses include `productResolution` with canonical definitions,
  publisher IDs, revisions, digests, component references and per-entity associations.
- The SDK exposes `resolve_graph(id, refresh_products=True)` and
  `resolve_graph(id, product_view="pinned")`.
- `GET /clip/v1/supply-chain/revisions` returns immutable revisions for locally
  owned records in one operator-authorized read, ordered by record ID and revision.
  Cached foreign catalogue revisions are excluded. Individual record revision
  endpoints remain available for focused inspection.

Resolved nodes are a read-only derived view, not recipient-authored manufacturer
changes or modifications to the stored signed dataset. Original issue snapshots,
acceptance proofs, pinned source records and accepted history are never rewritten.
Copies embedded in signed audit envelopes are deliberate cryptographic evidence;
the current graph shares the product definition rather than treating those copies
as independent editable manufacturer products.

Publisher trust, signatures, dependency pins and revision equivocation are checked.
Unavailable publishers fail refresh explicitly; a normal non-refresh read can use
previously observed verified revisions but does not claim to have fetched the latest.
Changing an existing manufacturer's IFC class is refused in the current projection.
There is no inference that matching serial text from unrelated authorities represents
the same physical item.

The console reviews whole submissions. The API additionally allows an explicit
record subset for acceptance; the signed decision binds that exact subset.

## API

The workflow base is `/clip/v1/supply-chain`. Local management endpoints require
`x-api-key`. Public catalogue reads do not. Cross-authority requests use
audience-bound, fresh, signed messages through the DID-advertised
`ClipSupplyChainService`; never send another organisation your operator key.
Operators must approve external catalogue/source publishers in
`CLIP_TRUSTED_PUBLISHERS`. A valid signature alone does not establish business
trust. The UI reports trust denial rather than automatically approving a publisher.

| Operation | Route |
|---|---|
| Supported record/schema metadata | `GET /schema` |
| List/create local records | `GET/POST /records` |
| Adopt an existing local private product type | `GET /records/adoption-candidates`, `POST /records/adopt` |
| Read/edit local record | `GET/PUT /records/{id}` |
| List/freeze immutable revisions | `GET/POST /records/{id}/revisions` |
| Publish public product/offering revision | `POST /records/{id}/publish` |
| Preview exact source pins | `POST /dependencies/preview` |
| Local public catalogue | `GET /catalogue` |
| Discover a remote public catalogue | `POST /catalogue/discover` |
| Retrieve an immutable public revision | `GET /catalogue/{id}/revisions/{revision}` |
| List/upload record documents | `GET/POST /records/{id}/documents` |
| Retire a current attachment | `POST /records/{id}/documents/{documentId}/detach` |
| Read document | `GET /records/{id}/documents/{documentId}` |
| Inspect/update source-controlled grants | `GET/POST /records/{id}/documents/{documentId}/grants` |
| Local/joined project destinations | `GET /projects` |
| Connect/refresh remote project | `POST /projects/connect`, `POST /projects/refresh` |
| Scoped submission sender policy | `GET/PUT /projects/{projectId}/senders` |
| Inbox/outbox and drafts | `GET/POST /submissions` |
| Read submission | `GET /submissions/{id}` |
| Issue/retry exact immutable submission | `POST /submissions/{id}/issue` |
| Accept/reject/request changes | `POST /submissions/{id}/decision` |
| Read selected issue document | `GET /submissions/{id}/documents/{documentId}` |
| Authenticated cross-authority exchange | `POST /receive` |

Dataset/project graph reads and accepted history remain under the existing
`/ifc/v1/datasets` and `/clip/v1/projects` routes. Workflow authoring records
signed graph proposals, authority decisions and receipts rather than bypassing
the IFCX authority history.

### Record creation

```json
{
  "kind": "offering",
  "name": "Supplier pump offering",
  "ifcClass": "IfcPumpType",
  "data": {"sku": "SUP-100", "warranty": "24 months"},
  "sources": [
    {
      "authorityDid": "did:web:manufacturer.example",
      "recordId": "manufacturer-record-id",
      "revision": 3
    }
  ],
  "idempotencyKey": "create-offering-unique-intent"
}
```

Record kinds are `product`, `offering`, `supply`, `installation`, and `asset`.
Product component sources add quantity/unit; installation sources add allocated
quantity/unit/serials. Supply data requires positive quantity and unit.
Concrete classes are checked against IFC4X3_ADD2. Type and occurrence classes
are not interchangeable.

Existing native types can be adopted through the candidate picker when they
belong locally to a private managed project/library. Adoption preserves the native
dataset/entity address and unrelated IFCX components. Imported paths are not
owned locally and cannot be adopted. Public native libraries are excluded to
avoid exposing private working records; new public catalogue revisions are
deliberately issued from private working records instead.

Edit requests supply the complete editable record fields plus `expectedRevision`.
Publication, freezing, upload, issue and decision also check revisions.
Conflicts return 409; refresh and explicitly review before creating another
request. Idempotency keys identify one exact intent and cannot be reused with a
different payload.

Document uploads optionally accept `replacesDocumentId`, and the returned new
document metadata records `supersedesDocumentId`. Detachment checks the current
record revision and removes only its working attachment reference. Neither
operation edits historical signed snapshots or physically destroys document
bytes.

An API source pin may also include a digest, dataset ID and entity path from
dependency preview. The server checks them against the signed immutable source,
preserves its dependencies and rejects cycles or incompatible sourcing.

### Draft, issue and decision

```json
{
  "recipientDid": "did:web:contractor.example",
  "projectId": "recipient-project-id",
  "recordIds": ["owned-supply-record-id"],
  "documentIds": ["selected-delivery-document-id"],
  "supersedes": null,
  "idempotencyKey": "create-submission-unique-intent"
}
```

Issue with `{ "expectedRevision": 1, "idempotencyKey": "issue-unique-intent" }`.
The issue binds selected record revisions, dependencies, document digests,
sender, recipient and destination. Delivery and recipient acceptance are
separate persisted states. Changed records invalidate a stale draft rather than
silently changing its disclosure scope.

The recipient decides with:

```json
{
  "decision": "accept",
  "reason": "Reviewed delivery and documentation",
  "expectedRevision": 1,
  "idempotencyKey": "accept-unique-intent"
}
```

`reject` and `request-changes` are also supported. Optional `recordIds` scopes
the decision. Acceptance preserves original upstream signed material and creates
local records atomically with graph history. Exact retries must not duplicate
assets or allocations.

The Python SDK exposes project/invitation methods and `*_workflow_*` record,
catalogue, document, source-preview and sender-policy methods, plus submission
creation, issue, reads and decisions. See [CLIPClient](../client/clip_client.py).

## Document policy and operational boundaries

Workflow documents use encrypted-at-rest local storage and brokered,
DID-authenticated reads. Public document bytes are deliberately captured inside
the signed public revision. Private bytes are not put in issue snapshots;
explicit issue selection or issuer-controlled grants authorise retrieval.
Downstream recipients cannot grant someone else access to an upstream document.

These workflow documents are separate from the original `/clip/v1/evidence`
encrypted-fragment/recipient-sealed-key replication API. They do not promise
automatic fragment replication, recipient-side offline key delivery, or
long-term archival retention. Grant expiry controls future brokered reads; it
cannot recall downloaded copies.

Source associations and submitted records are capped at 32 per operation.
Evidence selection is capped at 100 documents per submission, recursive source
chains are bounded, and transport/upload limits apply. Split large packages into
independently reviewable issues rather than assuming unbounded handover bundles.

Catalogue discovery is explicit-DID, not a global federated search engine.
Automatic dependency upgrade, arbitrary BIM geometry, OAuth/OIDC individual-user
management, KMS/HSM, production quotas and independent production qualification
are outside this workflow implementation. Keep the operator endpoint protected.

## Validation

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests
node --test .\tests\dashboard.test.cjs .\tests\supply-chain-ui.test.cjs
$env:CLIP_INTEGRATION = '1'
.\.venv\Scripts\python.exe -m unittest tests.test_supply_chain_network
```

The isolated real-HTTP regression starts its own temporary authorities and
verifies recursive component sourcing, supplier-to-supplier offerings, private
delivery documents, allocations, contractor/client handover, immutable revision
pins, decisions, corrections and IFCX graph history. It terminates only its own
processes and removes its own temporary databases.
