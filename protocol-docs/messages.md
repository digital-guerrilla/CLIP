# Message catalogue and cryptographic representation

## 1. Common representation

Wire field names are case-sensitive. Internal names or snake_case aliases are
not wire names. Objects use UTF-8 JSON, with no duplicate members, non-finite
numbers or values outside the RFC 8785 data model. JSON booleans are not integers.
Protocol counters are nonnegative integers within the interoperable exact range
`0..9007199254740991`. Revisions start at one unless explicitly stated otherwise.

Timestamps MUST include a timezone; senders SHOULD emit UTC RFC 3339 text with
`Z`. A verifier MUST verify the original text, not a re-rendered timestamp.
Freshness calculations compare instants, not strings.

Core secured objects MUST explicitly carry this context:

```json
[
  "https://w3id.org/security/data-integrity/v2",
  {"@vocab": "urn:clip:protocol:"}
]
```

No JSON-LD expansion or remote context fetch is needed for JCS signing.
This context supplies a vocabulary convention, not an ontology registry or
another network trust root. This draft requires exact context equality in the
document and proof; the prototype's more permissive verification is noted in
[decisions.md](decisions.md).

### 1.1 Default-field interoperability

The prototype's typed core verifier often serialises a parsed model before
verification. Omitted defaults may therefore reappear and invalidate a proof.
For prototype interoperability, senders MUST materialise defaults **before**
signing, including graph-node `children`, `inherits`, `attributes` maps, and
nullable IFCX schema-description fields used by its serializer.

A conforming independent verifier instead verifies the supplied logical JSON
without mutation. It then validates semantics. Removing a signed null and
omitting an unsigned default are not equivalent operations.

## 2. Data Integrity proof

Every secured object in these profiles contains one `proof` object:

| Field | Required value/type |
|---|---|
| `@context` | Same context as the unsecured document |
| `type` | `DataIntegrityProof` |
| `cryptosuite` | `eddsa-jcs-2022` |
| `created` | Timezone-bearing RFC 3339 timestamp |
| `verificationMethod` | Actor-controlled DID URL, e.g. `did:web:owner.example#key-1` |
| `proofPurpose` | Purpose required for that object |
| `proofValue` | `z`-prefixed base58btc encoding of a 64-byte Ed25519 signature |

Signing procedure:

```text
unsecured := document without the top-level proof member
options   := proof without proofValue, including @context
dataHash  := SHA-256(JCS(unsecured))
proofHash := SHA-256(JCS(options))
signature := Ed25519.Sign(secretKey, proofHash || dataHash)
proofValue := multibase-base58btc(signature)
```

`||` means byte concatenation. This is Ed25519 over the 64 concatenated hash
bytes, **not** Ed25519ph, a signature over JSON text, or a signature over the
hexadecimal hash strings. Verification reconstructs the same bytes.

The DID method is an Ed25519 `Multikey`: decoded `publicKeyMultibase` is the
two bytes `ed 01` followed by a 32-byte public key. It MUST be listed in the DID
relationship matching the required proof purpose, with matching `controller`.

| Object | Purpose |
|---|---|
| Proposal, publication, authority receipt, evidence manifest, frozen record, issued submission/manifest, document grant | `assertionMethod` |
| Graph decision, supply-chain recipient decision | `capabilityInvocation` |
| Peer digest, request/response service envelope, join decision notification | `authentication` |

Different purposes MUST NOT be interchangeable even if one key is authorised
for all three.

## 3. Digest registry

All hexadecimal SHA-256 fields contain exactly 64 lowercase hexadecimal digits.
Digest definitions differ by field; callers MUST NOT substitute one for another.

| Field/context | Exact input |
|---|---|
| `proposalDigest` | JCS of complete secured proposal, including `proof` |
| Authority receipt `transactionDigest` | JCS of transaction wrapper in section 6, including nested proofs |
| Replication payload/ack `digest` | JCS of complete bundle `{publication, transactions}` |
| Supply-chain source/revision digest | JCS of complete secured revision |
| `issueDigest` | JCS of complete secured issue |
| `issueManifestDigest` | JCS of complete secured issue manifest |
| Manifest `recordDigests` | JCS of each complete secured record revision |
| Manifest `documentDigests` | JCS of selected document metadata objects, NOT document bytes |
| Evidence-reference `integrity` | JCS of complete secured evidence manifest |
| Invitation `tokenDigest` | ASCII bytes of the raw token |
| Workflow document `digest`, evidence `contentSha256` | Plaintext document bytes |
| Fragment `sha256` | Ciphertext bytes including authenticator, excluding nonce |
| Fragment `digest` | Full stored bytes: nonce followed by ciphertext |
| Evidence `encryptedContentSha256` / `evidenceId` | Ordered concatenation of ciphertexts, excluding nonces |
| Import `integrity` | Exact fetched publication response bytes, encoded as `sha256-` plus standard Base64 |

`schemaDigest` is SHA-256 of JCS of:

```text
{
  "ifcxVersion": file.header.ifcxVersion,
  "imports": file.imports,
  "schemas": file.schemas
}
```

For compatibility this uses the **local root's serialised** imports/schemas,
including inserted CLIP deletion schema and serializer-emitted null fields.
It is not a digest of entity data, a fully composed graph, or imported schemas.
Import byte pins transitively bind remote dependencies.

## 4. Proposals

### 4.1 Component proposal

| Field | Meaning |
|---|---|
| `@context`, `proof` | Common context and assertion proof |
| `transactionId` | Unique proposal UUID |
| `actorDid` | Proposer DID |
| `target` | Component address from the core specification |
| `change` | `{"action":"set","value":...}` or `{"action":"remove"}` |
| `expectedSequence` | Authority-wide accepted-change precondition |
| `schemaDigest` | Root schema-set digest |
| `created` | Proposal creation time |

`set` requires a present non-null value valid for the target schema. `remove`
MUST omit `value`; even `value: null` is invalid. Component deletion uses the
IFCX deletion extension, not a null attribute.

### 4.2 Graph proposal

Same outer fields, except `target` contains only `authorityDid` and `datasetId`,
and `operations` replaces `change`.

`operations` is an ordered array of 1..32 objects:

```json
{
  "action": "create",
  "node": {
    "path": "door-01",
    "children": {},
    "inherits": {},
    "attributes": {"ifc::name": "North entrance"}
  }
}
```

`action` is `create` or `contribute`. The IFCX profile defines their semantics.
Component and graph proposals are distinct variants; an object MUST NOT contain
both `change` and `operations`.

## 5. Decisions and receipts

### 5.1 Graph decision

| Field | Meaning |
|---|---|
| `@context`, `proof` | Context and capability-invocation proof |
| `transactionId` | Unique decision UUID, distinct from proposal ID |
| `actorDid` | Dataset authority |
| `decision` | `accept` or `reject` |
| `proposalId` | Proposal `transactionId` |
| `proposalDigest` | Digest of the exact secured proposal |
| `expectedSequence` | Current authority-wide sequence |
| `created` | Decision creation time |

### 5.2 Authority receipt

| Field | Meaning |
|---|---|
| `@context`, `proof` | Context and authority assertion proof |
| `receiptId` | Receipt UUID |
| `authorityDid` | Authority committing the outcome |
| `transactionId` | Decision UUID |
| `accepted` | Boolean outcome |
| `sequence` | Resulting accepted-change sequence |
| `transactionDigest` | Digest of the stored wrapper |
| `created` | Receipt creation time |

`accepted: true` means the change was committed at the indicated authority
sequence. `accepted: false` means an authenticated rejection was recorded at the
unchanged sequence. Neither receipt transfers legal ownership.

## 6. Transaction wrappers

Accepted history entries contain an unsigned wrapper whose exact digest is
secured by the receipt:

```text
{
  "@context": decision["@context"],
  "transactionId": decision.transactionId,
  "kind": "componentProposalAcceptance" | "graphProposalAcceptance",
  "proposal": securedProposal,
  "decision": securedDecision
}
```

The wrapper MUST NOT receive a synthetic top-level `actorDid`,
`expectedSequence` or proof when calculating `transactionDigest`.
The nested documents already bind those values.

The prototype rejection wrapper uses `componentProposalRejection`, including
when rejecting a graph proposal. That spelling is preserved in this v1 draft
for compatibility; the proposal shape disambiguates it. Rejected wrappers are
not accepted history and MUST NOT increment sequence.

A verifier MUST verify both nested proofs, proposal/decision linkage,
decision authority, receipt linkage and wrapper digest. Verifying only the
receipt signature is insufficient.

## 7. Publication

An IFCX publication is:

```text
{
  "@context": context,
  "publisherDid": authorityDID,
  "file": IFCXFile,
  "proof": assertionProof
}
```

There is no required top-level `created`, sequence or publication ID in this
envelope. The proof timestamp is not an authority sequence. The publisher's
authorised assertion key MUST sign the whole file.

## 8. General service envelope

| Field | Meaning |
|---|---|
| `@context`, `proof` | Context and authentication proof |
| `messageId` | UUID for this service message |
| `actorDid` | Sender DID |
| `audienceDid` | Exact receiver DID |
| `kind` | One of the kinds below |
| `payload` | Kind-specific object |
| `created` | Creation time, same instant as `proof.created` |

The receiver MUST accept only an age in `[-30, 300]` seconds, calculated as
receiver current time minus `created`. Long-lived assertions, publications,
revisions and graph decisions do **not** inherit this service freshness window.

| Kind | Required payload |
|---|---|
| `replication` | `publication`, `transactions` |
| `replicationAcknowledgement` | `digest`, `authorityDid`, `datasetId`, `sequence` |
| `fragment` | `manifest`, `index`, `content` (standard Base64 stored bytes) |
| `fragmentReceipt` | `evidenceId`, `index`, `digest`, `expiresAt` |
| `fragmentRead` | `evidenceId`, `index` |
| `projectRead` | `projectId` |
| `projectView` | `requestId`, `project`, `publication`, `graph`, `history`, `assertions` |
| `projectJoin` | `inviteId`, `tokenDigest` |
| `projectJoinAcknowledgement` | `requestMessageId`, `joinRequestId`, `projectId`, `projectName`, `role`, `status` |
| `projectJoinDecision` | `joinRequestId`, `projectId`, `role`, `decision`, `requestDigest`, `projectRevision` |

`projectView.requestId` and join acknowledgement `requestMessageId` reference the
request envelope's `messageId`. Other responses correlate by their exact content
commitment (bundle digest or fragment address/digest). A receiver MUST check the
expected responder DID, audience and matching content, not just a valid signature.

Responses get new message IDs and fresh authentication proofs. A valid old
business object can be redelivered inside a new fresh envelope.

## 9. Other message families

Peer digests do not use `messageId`, `audienceDid` or `kind`; their exact shape is
specified in [federation.md](federation.md).

Supply-chain services use a separate `action` envelope, not `kind`; its
request/response fields and immutable business documents are specified in
[workflows.md](workflows.md). A verifier MUST select this profile explicitly.

The [core JSON Schema](schemas/core.schema.json) covers the core proposal,
decision, receipt and general-service outer structures. Schema success alone
does not prove signature validity, payload conformance or authorisation.
