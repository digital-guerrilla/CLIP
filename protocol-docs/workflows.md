# Project and supply-chain application profiles

## 1. Project access

A managed project is an authority-owned dataset with:
`projectId`, `authorityDid`, `name`, `visibility`, `members`, `revision`.
`projectId` is its dataset ID. Visibility is `private` or `public`; new projects
default to private. Member values are `viewer` or `contributor`.

| Actor | Read managed project | Propose | Decide/administer |
|---|---|---|---|
| Authority/local authorised operator | Yes | Yes | Yes |
| Viewer | Yes | No | No |
| Contributor | Yes | Yes, subject to validation | No |
| Nonmember | Only if public | No | No |

Member maps are limited to 100 external organisational DIDs. The authority
is not an entry in its own member map. Changing the whole policy requires
`expectedRevision`; success increments the policy revision atomically.
Contributors populate the project's proposer policy.

Membership MUST be checked at retrieval and acceptance, not only when a record
is created. Newly created project entities inherit the same access policy.
Membership grants neither upstream-document access nor acceptance of records.

Legacy unmanaged datasets are public in the prototype; this MUST NOT be used
as a silent fallback for a managed private project whose policy is missing.

### 1.1 Partner reads

`projectRead` carries `{projectId}` in a fresh audience-bound service envelope.
The authority returns `projectView` with:

- `requestId`: request `messageId`;
- `project`: `projectId`, `authorityDid`, `name`, `visibility`, `revision`;
- `publication`: signed IFCX publication;
- `graph`: derived resolved view;
- `history`: this dataset's accepted transaction/receipt entries;
- `assertions`: proposal/status entries, up to 200 in the prototype.

The service proof binds the view, but does not replace nested publication and
history verification. Dataset-filtered history may contain gaps in the
authority-wide sequence and is not a complete replication bundle.
The assertions list is bounded, not guaranteed exhaustive.

## 2. Invitation and join protocol

The owner issues a single-use invitation for a fixed project and fixed role.
Lifetime defaults to seven days and may be 1..720 hours.

The invitation code is:

```text
"CLIP1." || base64url-no-padding(UTF-8 JSON {
  "authorityDid": ownerDID,
  "inviteId": UUID,
  "token": base64url-no-padding(32 random bytes)
})
```

The token text is 43 Base64url characters. Only its SHA-256 ASCII-text digest
MUST be retained in the invitation record. The full code is a bearer secret
and MUST NOT appear in logs or durable join history.

Redemption resolves the owner's `ClipProjectInviteService` and sends:

```text
{
  "token": rawToken,
  "message": authentication-signed service envelope,
             kind = "projectJoin",
             payload = {"inviteId": UUID, "tokenDigest": SHA256(tokenASCII)}
}
```

The owner MUST authenticate the actor/audience/freshness, match the raw token
digest to both signed payload and stored invitation, then atomically reserve
the invite for that actor. Redemption MUST NOT itself grant membership.

```text
invite: active -> pending -> accepted | rejected
invite: active | pending -> revoked
expiry: prevents redemption and acceptance even if stored status is unchanged
join request: pending -> accepted | rejected
```

Retries by the same actor against a pending invite reuse its pending join
request; another actor MUST be refused. The signed acknowledgement references
the current request message and durable join-request ID.

Owner acceptance MUST recheck invite expiry/status, actor key authorisation,
membership capacity, absence of existing membership, and project permission
revision. Membership insertion, revision increment and signed join decision
MUST commit atomically. A rejection MUST NOT insert membership.

The `projectJoinDecision` payload binds `requestDigest` to the originally stored
secured join request, not necessarily the most recent retry envelope.
The notification is an authentication proof, unlike graph capability decisions.

The requester may retrieve its own join status through the supply-chain
`projectJoinStatus` action. No general push-delivery/acknowledgement protocol for
join decisions is promised. Acceptance does not retroactively add evidence-key
envelopes or approve scoped submission senders.

## 3. Supply-chain service envelope

This profile preserves a distinct v1 envelope:

```text
{
  "@context": context,
  "messageId": nonemptyString,
  "actorDid": senderDID,
  "audienceDid": recipientDID,
  "action": actionName,
  "payload": object,
  "created": timestamp,
  "proof": authenticationProof,
  "requestId": requestMessageId  // responses only
}
```

The `//` annotation is explanatory, not JSON wire content.
Requests MUST omit `requestId`. Responses use `<action>Response`, include a new
`messageId`, and bind `requestId` to the request. The general `kind` envelope
MUST NOT be substituted. The same `[-30,300]` second freshness window and
proof/message timestamp equality apply.

| Action | Request payload | Response payload |
|---|---|---|
| `catalogue` | `{}` | `authorityDid`, `items` array of signed published revisions |
| `projectConnect` | `projectId` | Authority/project identity, name, visibility, revision |
| `projectJoinStatus` | `projectId`, `joinRequestId` | Identity, name, role, status, signed decision or null |
| `issue` | `issue` secured object | `id`, `status`, `issueDigest` |
| `decision` | `decision` secured object | `id`, `status` |
| `documentRead` | `documentId` | Document metadata plus standard Base64 `content` |

Clients MUST check response actor, audience, action, request ID, freshness and
proof. A catalogue response is not authority to disclose private projects.

## 4. Owned records and revisions

Profile: `urn:clip:supply-chain:record:v1`.

Record fields are `profile`, `id`, `authorityDid`, `kind`, `name`, `projectId`
(nullable), `ifcClass`, `data`, `sources`, `documents`, `revision`, `status`,
`datasetId`, `graphPath`. Frozen snapshots additionally carry `dependencies`,
`@context`, `proof`. Accepted records may carry `lineage`, `acceptedFrom`,
`originalKind`.

| Kind | Intended meaning | Sources |
|---|---|---|
| `product` | Manufacturer type/BOM | Products or offerings; BOM quantity/unit required |
| `offering` | Supplier offering of a type | Exactly one product/offering of matching type |
| `supply` | Project delivery | Exactly one offering of matching type; positive quantity/unit |
| `installation` | Installed occurrence/assembly | Accepted supplies, installations or assets; allocations validated |
| `asset` | Recipient-owned occurrence | Installation, asset or supply lineage |

Types and occurrences MUST not be treated as interchangeable IFC classes.
Detailed application validation includes concrete IFC4X3_ADD2 class checks,
quantity/unit consistency, distinct serial allocation and source-kind checks.
The supported class list is application metadata, not the full IFC standard.

A source identifies `authorityDid`, `recordId`, `revision`; it may also include
`digest`, `datasetId`, `entityPath`, `componentSchema`, `ifcClass`, `quantity`,
`unit`, `serials`. Identity/digest/address pins MUST agree with the supplied
signed dependency. Sources and dependencies correspond positionally.

Records are working state; frozen revisions are immutable assertions.
Editing requires `expectedRevision`, increments revision, and MUST NOT mutate
past signed snapshots. Public publication is restricted to unscoped products
and offerings and MUST exclude private dependencies and delivery/commercial data.
Private issued revisions cannot become public at the same revision number.

Public attachments may embed Base64 bytes in the signed public revision.
Restricted attachment metadata MUST NOT be included implicitly in frozen
snapshots; directed issues explicitly select it.

Dependencies MUST be verified recursively, including publisher trust, proof,
exact pins, accepted provenance and document-byte digests. Cycles MUST fail.
The prototype bounds dependency ancestry around 64 and sources to 32; a release
needs unambiguous total-node/byte limits as well as ancestry limits.

## 5. Directed issues

Issue profile: `urn:clip:supply-chain:submission:v1`. Required fields:
`submissionId`, `senderDid`, `recipientDid`, `projectId`, `supersedes` (nullable),
`records`, `documents`, `manifest`, `created`, `@context`, `proof`.

The independently assertion-signed manifest uses
`urn:clip:supply-chain:issue-manifest:v1` and carries the same addressing fields,
plus `recordDigests`, `documentDigests`, `created`, `@context`, `proof`.

Records MUST be 1..32 distinct sender-owned signed revisions. Documents MUST
be 0..100 distinct selected attachments belonging to those records and sender.
Both digest maps MUST exactly match their objects; extra/missing entries fail.
All issue/manifest addressing and correction fields MUST match.

Project submission-sender approval is a separate scoped policy. In this v1
profile an actor may exchange a submission with a project if it is the authority,
a project member (viewer or contributor), **or** an explicitly approved scoped
sender. A scoped sender can submit without gaining general project graph reads
or contributor rights. A member's ability to submit business records does not
permit graph acceptance or administrative changes.

```mermaid
sequenceDiagram
    participant S as Sender authority
    participant R as Recipient authority
    S->>S: Freeze revisions and sign issue + manifest
    S->>S: Persist exact issue; delivery pending
    S->>R: Fresh authenticated issue envelope
    R->>R: Verify scope, dependencies, digests and sender policy
    R-->>S: Signed issueResponse (received, not accepted)
    R->>R: Explicit capability-signed decision
    R->>R: Atomically create linked local records on acceptance
    R->>S: Fresh authenticated decision envelope
    S-->>R: Signed decisionResponse
```

Issuing MUST persist the immutable issue before network egress. Delivery retries
send that same issue inside fresh envelopes. A stale draft whose selected
working revisions changed MUST fail rather than silently disclose newer data.

The same submission ID with the same issue digest is a retry; another digest
is a conflict. `supersedes` references an existing issue from the same sender
and receiving project. It MUST NOT erase or alter that prior issue.

## 6. Recipient decision and acceptance lineage

The capability-signed decision has:
`submissionId`, `issueDigest`, `issueManifestDigest`, `actorDid`, `recipientDid`,
`projectId`, `decision`, `recordIds`, `reason`, `created`, `@context`, `proof`.
`recipientDid` here is the original sender; the actor is the original issue
recipient. `decision` is `accept`, `reject` or `request-changes`.

Selected `recordIds` MUST be a nonempty distinct subset of the issue records.
The decision is terminal for this issue, including partial acceptance; omitted
records are not automatically accepted and no second decision is implied.
A correction uses a new issue.

On acceptance the recipient MUST atomically create local linked records,
preserve source signatures/provenance, commit graph history and store the
idempotent outcome. Supply/product/offering kinds remain those kinds;
installation/asset issues become recipient `asset` records in the prototype.
Acceptance MUST NOT grant control over the sender's original records.

`acceptedFrom` contains `snapshot` (original secured revision), `issue`
(secured **manifest**, not full issue), and `decision`. Therefore provenance
verification compares `issueManifestDigest` with `acceptedFrom.issue` and its
`recordDigests` with the original snapshot. Comparing it to the full issue
digest would be incorrect.

Decision state and network delivery state are separate. Failed delivery MUST
retain the local decision for retry without creating duplicate assets.

## 7. Workflow documents

Workflow document metadata includes `id`, `name`, `mediaType`, `visibility`,
`digest`, `size`, `authorityDid`, `recordId`; replacements may add
`supersedesDocumentId`. Its `digest` is SHA-256 of plaintext bytes.

Private bytes are brokered from the original document authority after a
DID-authenticated read. Access requires explicit issue selection for that
recipient or an active source-authority grant. An explicit revoked/expired grant
blocks retrieval in the prototype even if an old issue selected the document.
Downstream recipients MUST NOT grant access on the upstream issuer's behalf.

Signed grants bind `documentId`, `publisherDid`, `recipientDid`, `active`,
`expiresAt` (nullable), `revision`, `created`, `@context`, `proof`.
The first grant uses `expectedRevision: 0`; updates require the current revision.
Detaching an attachment edits working references, not historical signatures
or already-disclosed copies.

This brokered-document profile is distinct from encrypted fragment evidence.
Encryption-at-rest key derivation/storage is an implementation concern; it is
not a cross-authority sealed-key delivery protocol.
