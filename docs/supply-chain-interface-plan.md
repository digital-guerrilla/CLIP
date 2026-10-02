# CLIP supply-chain interface plan

Status: original design proposal, with implementation update below.
Original baseline: the IFCX working tree, including project invitation routes.

Implementation update: the organisation-owned authoring, catalogue, sourcing,
submission, installation and brokered document workflows are now implemented.
See [Supply-chain workflows](supply-chain-workflows.md) for the implemented API,
usage and remaining operational boundaries. The sections below preserve the
original design proposal; their original capability/gap descriptions are not a
current implementation inventory.

## 1. Agreed workflow and ownership

Each organisation retains authority over its own records. A downstream party
receives a versioned submission, reviews it, and accepts selected information
into its own project. Acceptance does not transfer upstream authority or allow
the recipient to edit the source record.

```text
Manufacturer product type and component specification
  -> Supplier offering and supply record
    -> Contractor installation record
      -> Main contractor / client accepted asset and handover
```

A manufacturer can source components directly from another manufacturer or
through one or more suppliers. A supplier can source another supplier's public
offering. A contractor can submit to another contractor before the client.
These are repeatable organisation-to-organisation steps, not a fixed four-node
chain. One organisation may manufacture, supply and install different items.

Keep the following concepts distinct:

| Record | Authority | Meaning |
|---|---|---|
| Product type | Manufacturer | Reusable technical definition, model and documentation |
| Component specification | Assembling manufacturer | Required component type, quantity, units and selected source |
| Supplied offering | Supplier | Supplier's reusable catalogue entry linked to an upstream product or offering |
| Supply record | Supplier | Particular project delivery, quantities, batch/serial details and delivery evidence |
| Installation | Installing contractor | Actual occurrence, location, dates and installation/commissioning evidence |
| Accepted asset | Receiving contractor/client | Recipient-owned project occurrence linked to accepted submissions |
| Submission | Sender, with recipient-signed decision | Immutable issue of selected records and dependencies to a named recipient/project |

An offering is not a physical installed instance. A product revision is not a
new serial-numbered asset. A project's access membership is not a submission.
An acceptance receipt proves a recorded decision, not technical certification.

## 2. Interface structure

Extend the existing console rather than make the network map the main workflow.
Keep network health, replication and raw signed JSON under Administration.

```text
Organisation switcher / active organisation and authority
  My products              manufacturer-owned definitions
  My supplied products     supplier-owned offerings and deliveries
  Discover products        public manufacturer and supplier catalogues
  Projects                 local projects and projects joined elsewhere
  Submissions              incoming, outgoing, decisions and corrections
  Documents                documents, versions, recipients and expiry
  Administration           participants, trust, signing and network operations
```

Show the relevant workspaces for an organisation's capabilities, without treating
a displayed business role as permission. Every detail view shows who controls
the record, which version is selected, its visibility and its lineage.

### Manufacturer: product editor

Product list: model/SKU, name, IFC class, revision, publication state, completeness
and upstream dependency warnings. Actions: Create product, Edit, Manage
components, Add documentation, Publish revision and View downstream usage where
authorised.

Product detail tabs:

- **Product data:** identity, descriptions, supported property sets, units and
  technical values. Use schema-driven forms; do not require raw JSON.
- **Components:** bill of materials with component type, quantity/unit,
  originating manufacturer, immediate supplying organisation and pinned version.
- **Documentation:** datasheets, declarations, certificates, manuals and document
  versions; distinguish public documentation from restricted evidence.
- **Publication:** preview exactly what will be exposed, issue a revision and
  compare it with the previous revision.
- **History and lineage:** accepted edits, source provenance and supersession.

The Add component wizard first asks whether the component comes directly from
a manufacturer or via a supplier. The picker then filters eligible public
product types or supplied offerings. Display both the originating manufacturer
and the immediate supplier, including intermediate suppliers where present.
Choose a version, quantity and unit; preview the dependency chain before saving.

Adding a component does not grant permission to edit it. Upstream updates appear
as an available revision requiring explicit review, not a silent replacement.
Reject dependency cycles and incompatible component classes. A bill of materials
is not the same relationship as occurrence containment or type inheritance.

### Supplier: offering, delivery and submission

The Create supplied product wizard:

1. Select a public manufacturer product or an upstream supplier offering.
2. Inspect its origin, revision, documentation and chain of supply.
3. Add the supplier's own SKU, description, service/warranty information and
   supplier-owned documents without changing manufacturer-authored properties.
4. Save privately, then optionally publish a catalogue revision.

Use distinct **Create project supply** and **Publish offering** actions. A public
offering must not expose project prices, client details, delivery addresses,
serial allocations or private project evidence.

Create project supply selects a joined recipient project, offering revision,
quantity/unit and optional batch/serial allocations. Attach delivery records and
required documentation. The Submit to contractor wizard selects records and
documents, names the recipient/project, previews the disclosed scope, checks
document access and issues a signed, versioned submission.

Public offerings are discoverable by other suppliers and manufacturers. Reuse
creates a new local offering/component reference while preserving every
upstream organisation and the original manufacturer.

### Contractor: review, install and hand over

Project navigation: Products received, Installations, Incoming submissions,
Outgoing submissions, Documents and Participants.

Incoming supply review shows the sender, manufacturer, supply chain, version,
quantities, documents, changes since a previous issue and verification results.
Actions: Accept, Reject with reason or Request changes. Accepting material
supply does not mark it installed.

The Record installation wizard:

1. Select accepted supply lines and allocate quantities/serials.
2. Choose a compatible occurrence class and project location.
3. Record installation date, installer, status and relevant commissioning data.
4. Attach installation/inspection evidence and save the contractor-owned record.
5. Select a receiving contractor or client project and issue a submission.

Separate installation completion from recipient acceptance. A subcontractor
submits to the main contractor; the main contractor can associate that accepted
installation with its own record and submit onward. Preserve the original
installer and every intermediate decision rather than reattribute the work.

### Client / receiving contractor: acceptance and asset view

Review incoming installations with location, quantities, evidence completeness
and the complete authorised lineage. Accept into a recipient-owned asset/project
record, reject or request corrections. Later corrections are new submissions
that reference the earlier issue; they do not rewrite its contents or receipts.

Asset detail presents:

```text
Installed asset
  Installed by: subcontractor
  Accepted/submitted onward by: main contractor
  Supplied by: immediate supplier -> upstream supplier, if any
  Manufactured by: original manufacturer
  Contains: component definitions and their sourcing chains
  Evidence: product -> supply -> installation -> recipient decisions
```

An accepted installation may legitimately be failed, planned or incomplete if
the receiving workflow permits it. Do not equate acceptance with an event's
technical completion status.

## 3. Shared interaction patterns

- **Source picker:** search/filter by manufacturer, supplier, class, model and
  publication revision; separate direct manufacture from supplied offerings.
  Initially allow explicit organisation DID/catalogue links. Federated search
  requires a defined catalogue registry/index; gossip alone is not discovery.
- **Lineage panel:** show origin, immediate source, versions, publication pins
  and acceptance decisions. Clearly label restricted or unavailable dependencies.
- **Review screen:** compare before/after values and document revisions; show
  local versus upstream-authored fields and the exact disclosure scope.
- **Submission inbox/outbox:** filter by project, sender/recipient, workflow state,
  issue number and date. Distinguish delivery acknowledgement from acceptance.
- **Document panel:** show issuer, document category, version, integrity,
  retention deadline and whether the current recipient can actually decrypt it.
- **Action permissions:** editable local records offer Edit; upstream records
  offer Reuse/View source; unaccepted submissions offer Review, not direct editing.
- **Conflict handling:** a stale sequence/revision triggers refresh and comparison
  before re-signing. Verification failure, missing keys and unavailable sources
  block the affected action with an explicit error.

Recommended submission states: Draft -> Issued -> Delivered -> Accepted or
Rejected. Request changes is a recipient response to an immutable issue; a new
issue supersedes it. If partial line acceptance is needed, specify it explicitly
before implementing the decision contract. Start with whole-submission decisions
and let senders split independently decidable records into separate submissions.

Publication state and project visibility are different. A private working
library may contain draft revisions; a public catalogue should expose only
deliberately issued product/offering revisions.

## 4. Mapping to the current API

Graph/dataset paths below are under `/ifc/v1`; project permissions, invitations,
networking and supply-chain paths are under `/clip/v1` unless stated otherwise.

| Interface capability | Existing API / contract | Remaining work |
|---|---|---|
| Create a project or product library | `POST /projects`, with `kind`, `name`, `visibility` | Separate working libraries from issued catalogue publications |
| List local projects/catalogues | `GET /projects` for local operator; `GET /datasets` for visible datasets | Joined-project persistence and public catalogue indexing/search |
| Create guided types/occurrences | `GET /projects/templates`; `POST /projects/{id}/entities` | Broader classes, offering/supply/event forms; only door/pump types are currently supported in libraries |
| Read data, origins and history | `GET /datasets/{id}/graph`, `/components`, `/history`, `/publication` | Per-record provenance presentation and immutable revision retrieval |
| Edit owned component data | Signed `POST /proposals`, then owner-signed `POST /decisions` | Guided local edit/signing service and schema-driven UI |
| Change entity graph relationships | Graph proposals support `create` and `contribute` | Validated sourcing/BOM relationships and transactional dependency attachment |
| Invite an organisation | `POST /projects/{id}/invites`; `/projects/invites/redeem`; join-request listing and decision routes | Persist recipient-side joined projects and explicit directed project links |
| Read a private partner project | `POST /projects/read`, DID-authenticated `projectRead` message and signed `projectView` response | Browser-facing broker, joined-project navigation and scoped handover reads |
| Manage Viewer/Contributor access | `PUT /projects/{id}/permissions`, with `expectedRevision` | Scoped business actions and individual-user authorisation |
| Upload evidence | `POST /evidence/upload`, then attach returned `evidenceReference` through signed change/decision | Document UI, public-document distribution and downstream recipient grants |
| Select/import an upstream publication | Signed publications and byte-pinned IFCX imports exist; dataset registration accepts imports | No guided add/update dependency operation on an existing project |
| Publish supplied offerings | No dedicated offering or catalogue workflow | Validated offering schema, catalogue publication and source selection |
| Submit supply/installation/handover | Generic signed proposals and construction events provide primitives | Directed immutable submissions, inbox/outbox, scope, delivery and decisions |

Project invitation acceptance grants project access, not acceptance of products
or installations. Contributors can propose changes; only the receiving dataset
authority can accept them. Keep these decisions separate in the interface.

Do not make supplier submission eligibility require broad Contributor access:
the proposed submission channel should permit scoped issues from approved
senders, while the recipient authority controls importing/accepting them.

The existing construction event schema supports manufacture, delivery,
installation, inspection, maintenance and decommission events. It is not a
complete commercial offering, supply-allocation or handover contract.

## 5. Required API/model extensions

The route families in this section are proposals, not existing endpoints.
Final request/response schemas should precede UI implementation.

1. **Guided local authoring:** extend project/entity authoring with typed,
   revision-checked product edits, documents and relationship operations. The
   local service signs proposals and authority decisions using existing contracts.
   Keep external changes pending for explicit recipient review.
2. **Dependencies:** add preview and atomic attach/update operations for selected
   publication revisions. Validate signatures, publisher trust, exact-byte pins,
   schemas, path collisions, class compatibility and cycles before committing.
   Current graph operations do not edit imports or install new schema definitions.
3. **Catalogue revisions:** introduce `/catalogues` discovery/publication and
   immutable version retrieval. Preserve exact signed publication bytes at a
   versioned URI; a pin to a mutable latest-publication URI is insufficient after
   upstream edits or cache loss. Draft and latest views must remain distinct.
4. **Business components:** define versioned CLIP schemas for product component
   specifications, supplied offerings, supply allocations and submission lineage.
   Use native IFCX References/children/inherits where semantically appropriate;
   organisation DIDs identify actors, not individual products.
5. **Submissions:** introduce `/submissions` create/issue/list/detail and
   DID-authenticated receive/acknowledgement/decision operations, with persistence
   on both sender and recipient. Bind sender/recipient DIDs, source and destination
   project IDs, issue ID/revision, included records, dependencies, evidence
   manifests, immutable bundle digest and superseded issue. Verify intended
   recipient, project policy and sender authority before showing an issue.
6. **Recipient acceptance:** validate the immutable issue and create linked local
   records plus a signed decision/receipt atomically. Preserve upstream signed
   material as upstream evidence; a recipient must not re-sign edited upstream
   facts as if the original publisher issued them. Acceptance retries must not
   create duplicate assets. Do not treat whole-authority replication as handover.
7. **Joined projects and links:** persist accepted invitations on the joining
   organisation and establish directed contractor/client project destinations.
   Authenticate remote reads through a local broker; never share operator keys.
8. **Evidence grants:** implement explicit source-authorised recipient key grants
   and authenticated retrieval for downstream documents. A public product record
   does not currently make its encrypted evidence public. New project members do
   not automatically receive keys for previously uploaded documents.
9. **User authentication/signing:** add scoped individual-user permissions and a
   backend signing broker. Keep organisation private signing keys out of browser
   code. The present operator API key controls the whole node, not a person's role.

Cross-dataset association must include authority, dataset, entity path and
immutable publication revision/digest, plus component schema where relevant.
Native inheritance uses a path within a resolved composition, not an arbitrary
remote DID or URL. Preview materialisation into a project before accepting a
source selection.

The pinned composition profile processes root layers first and imports later.
Do not assume that local supplier attributes automatically override imported
manufacturer attributes. Keep supplier-owned data separate and test effective
values and provenance under the actual layer order.

Scoped bundles must include the dependency closure needed to interpret selected
records, without disclosing unrelated private project records. Preserve original
signatures for included upstream publications, and introduce a source-authorised
signed projection contract where a full publication cannot safely be disclosed.
A digest does not confer read permission or publisher trust.

## 6. Permissions and documentation policy

- Manufacturer: edit own definitions and component choices; reference upstream
  definitions; issue its own catalogue revisions.
- Supplier: edit own offerings/deliveries; reference upstream definitions; issue
  public offerings or project-specific submissions.
- Contractor: edit own installation records; review received supply; submit onward.
- Client/receiving contractor: decide submissions and edit its own accepted assets.
- Viewer: read authorised records only. Contributor: propose, not accept on behalf
  of the dataset authority. Individual-user roles must be enforced server-side.

Start all working records and project submissions private. Use separate public
catalogue publications, with a disclosure preview. For MVP, handle deliberately
public datasheets separately from encrypted project evidence; do not grant
anonymous document keys by making a project public.

Before issuing a submission, verify intended recipients can retrieve and decrypt
all required evidence for the agreed retention period. Reuse of an upstream
document requires its issuer's distribution permission; otherwise request a
grant or exclude it and report the gap. The current upload limit is one year of
retention, so long-term handover needs an explicit retention/renewal design.

Revoking membership blocks future authorised reads but cannot recall previously
downloaded data or document keys. State this in access and publication dialogs.

## 7. Delivery sequence and verification

1. **Contracts and ownership:** agree product/offering/supply/installation schemas,
   revision identity, disclosure rules and whole-submission decisions. Define
   quantity/unit validation, serial uniqueness and allocation correction rules.
2. **Manufacturer vertical slice:** create/edit a supported product type, manage
   document versions and issue an immutable public revision.
3. **Supplier vertical slice:** discover/select that revision, create an offering,
   publish it or create a private project supply, then issue a contractor submission.
4. **Contractor vertical slice:** receive/review/accept supply, allocate it into
   installations and issue an installation submission to a client.
5. **Recursive sourcing/handover:** manufacturer BOM selection, supplier-to-supplier
   offerings and subcontractor-to-main-contractor-to-client submissions.
6. **Qualification:** broader IFC classes, scoped user authentication, durable
   document grants/retention, concurrent decisions and representative projects.

MVP acceptance scenarios:

- A manufacturer edit creates a new revision; an already accepted downstream
  record remains pinned to the previous revision until explicitly updated.
- A component selected via a supplier retains both the immediate supplier and
  original manufacturer; direct sourcing has no invented supplier.
- A public offering exposes only its intended catalogue scope, not project supply.
- A supplier submission reaches the correct private contractor project without
  granting the supplier editing rights to unrelated recipient records.
- A contractor accepts supply, records an installation and submits it onward;
  client acceptance preserves the complete upstream chain and evidence versions.
- Corrections, rejected issues and requests for changes remain auditable.
- Duplicate delivery/acceptance produces no duplicate occurrences or allocations.
- Installed quantity cannot exceed accepted, unallocated supply in compatible
  units; duplicate serial allocation is rejected.
- An unauthorised user/organisation cannot edit upstream records or read private
  bundles. Spoofed actor/recipient/project identifiers fail verification.
- Missing grants, stale revisions, revoked keys, invalid signatures, dependency
  cycles and expired evidence cause explicit errors, not successful fallbacks.
- Scoped handover omits unrelated private records; retained publication versions
  can be resolved after upstream changes without depending solely on a warm cache.

## Source context

- [Protocol profile](instance-dependency-network.md)
- [Architecture](architecture.md)
- [Current API overview and known gaps](../README.md)
- [Project and invitation routes](../node/app/api/clip_projects.py)
- [Dataset/proposal/decision routes](../node/app/api/ifc_transactions.py)
- [Evidence routes](../node/app/api/clip_evidence.py)
- [Transaction contracts](../node/app/core/ifc_protocol.py)
- [Guided IFC templates](../node/app/core/ifc_authoring.py)
- [Current construction schemas](../node/app/imports/ifcx.py)
- [Existing console](../node/app/static/dashboard.html)
