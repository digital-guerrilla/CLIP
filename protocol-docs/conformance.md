# Conformance, examples and validation plan

## 1. How to claim conformance

A report MUST identify `CLIP 1.0-draft.1`, implemented roles/profiles, binding,
test results, supported limits and deviations. Passing structural schemas alone
is not conformance. At least two independent implementations should exchange
messages before adopting a stable interoperable release.

Use isolated synthetic DIDs/keys/data for qualification. Never reuse a published
test private key in deployment. Tests involving freshness use a controlled
clock; historical proof fixtures are not fresh service requests.

## 2. Core acceptance tests

| ID | Input/action | Required result |
|---|---|---|
| C01 | Valid core assertion and authorised DID relationship | Proof verification succeeds |
| C02 | Change one signed value, nested proof, or digest | Verification fails without mutation |
| C03 | Valid signature with wrong purpose/controller/actor | Fail |
| C04 | Unknown/revoked key or time outside validity interval | Fail |
| C05 | Duplicate JSON members or non-finite/JCS-incompatible numbers | Fail |
| C06 | Omit/add a signed null/default or rewrite timestamp text | Signature does not silently become valid |
| C07 | Unknown operation/profile/outer field | Explicit unsupported/validation failure |
| C08 | Valid external proposal without proposer permission | Fail, graph unchanged |
| C09 | Valid authorised proposal | Pending; graph and authority sequence unchanged |
| C10 | Owner accepts exact proposal/digest at current sequence | Atomic graph/history/receipt commit; sequence +1 |
| C11 | Owner rejects | Negative receipt; graph/accepted sequence unchanged |
| C12 | Stale expected sequence or schema digest | Conflict; no partial commit |
| C13 | Two concurrent accepts at the same authority sequence | At most one succeeds |
| C14 | Accept proposals in two different datasets | Shared authority counter, not two per-dataset counters |
| C15 | Contributor removed/key revoked between proposal and acceptance | Acceptance denied |
| C16 | Duplicate/reused transaction ID | No double application; conflicting reuse denied |
| C17 | Crash before/after commit boundary | State/history/receipt remain consistent after restart |

## 3. Service and profile tests

| ID | Input/action | Required result |
|---|---|---|
| S01 | Wrong audience or response signer | Fail |
| S02 | Envelope age -30 or +300 seconds | Eligible for acceptance if all other checks pass |
| S03 | Age less than -30 or greater than +300 | Fail |
| S04 | Service proof/message timestamps denote different instants | Fail |
| S05 | Wrong response request ID/action or acknowledgement content | Fail; no delivery/placement success |
| G01 | Root/import collision | Later imported contribution wins |
| G02 | Named child/inheritance null | Named edge removed |
| G03 | Remove then restore component | Mask hides inherited/local value; later set restores |
| G04 | Missing target, cycle, invalid schema or mask definition | Fail |
| G05 | Create collision or contribute to imported-only node | Fail atomically |
| G06 | Conflicting multiple inherited branches | Draft ambiguity rejection, not insertion-order choice |
| P01 | Private read by nonmember | Hidden/denied, including list endpoints |
| P02 | Viewer submits graph proposal | Denied |
| P03 | Redemption of active invite | Pending only; no membership |
| P04 | Same-actor retry versus another actor's reservation attempt | Reuse pending request versus conflict |
| P05 | Expired/revoked invite, stale project revision or revoked join key | No acceptance/membership |
| W01 | Alter source revision/digest/dependency proof | Fail |
| W02 | Reuse record revision or submission ID with different digest | Conflict |
| W03 | Issue/manifest addressing or record/document map mismatch | Fail |
| W04 | Valid issue from nonmember without scoped sender approval | Denied despite valid signature |
| W05 | Lost issue/decision response; retry exact intent | No duplicate assets/allocations |
| W06 | Accept selected subset | Only selected linked records; terminal scoped decision |
| W07 | Public snapshot contains private dependency/attachment | Publication denied |
| W08 | Upstream document access without grant/selection; revoked grant | Denied |
| F01 | Import whitespace changes with unchanged JCS content | SRI byte pin fails even if proof could remain valid |
| F02 | Cached import whose DID key was revoked | Fail despite cache hit |
| F03 | URI/dataset cycle or depth/size limit exceeded | Fail |
| N01 | Indirectly learned peer | Unknown, never alive solely from hearsay |
| N02 | Equal/lower peer generation or stale higher-generation digest | Does not refresh liveness |
| N03 | Peer restart and generation increase | Fresh direct contact restores liveness |
| R01 | Missing/reordered accepted history entry | Contiguity verification fails |
| R02 | Old replica sequence/different digest/same-history rewrite | Conflict |
| R03 | Exact bundle retry | Idempotent storage; verified acknowledgement |
| E01 | Wrong stored fragment size/digest/nonce or unwrap key | Fail |
| E02 | Repair response with a different manifest | Fail; do not replace original commitment |
| E03 | Expired evidence before/after sweep | Unavailable in both cases |
| E04 | Membership change after upload | Existing recipient envelopes unchanged |
| E05 | Ciphertext-only storage peer | Can verify/store/receipt without plaintext key |

## 4. Existing known-answer vectors

### 4.1 Ed25519/JCS

The public fixture [ifcx-proof.json](../tests/vectors/ifcx-proof.json) was
generated with an independent Ed25519 implementation and JCS tooling.
Its expected values are:

```text
document SHA-256:
f0cb55f068523d1a506bdd66265d863b0c70e3f88ed1b3e8dd2f3e95590b8caa

proof-options SHA-256:
63d66070b0ab881c5f8102988e5cab2e05de38e5aa630d8f5aa271f5fdcdb185

Ed25519 public key:
d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a
```

Combine the two hashes in **proof-options then document** order. Compare the
signature bytes and multibase proof value to the fixture.

The fixture exercises cryptography, not complete replication-acknowledgement
payload validation: its payload omits the `authorityDid` required for a real
placement acknowledgement. Its old `created` also makes it unsuitable as a live
fresh service message. A conforming implementation MUST distinguish those layers.

### 4.2 Composition

[ifcx-composition.json](../tests/vectors/ifcx-composition.json) independently
states expectations for import ordering, type inheritance, named-edge removal
and component masking. The fixture's simplified schemas/nodes are composition
inputs, not necessarily complete prototype-normalised signed wire files.

### 4.3 Further vectors needed

Stable release qualification SHOULD add independently generated known-answer
vectors for complete proposal/decision/wrapper/receipt digests, serialised schema
digests including null/default fields, byte-pinned nested publications,
X25519 sealed keys/secretbox fragments, invitations and accepted supply lineage.
The existing fixture set does not prove those features across implementations.

## 5. Illustrative transaction trace

The following is a semantic example, **not signed JSON to submit**:

```text
owner authority sequence = 7
proposer signs component proposal:
  target = (owner DID, building dataset, door-01, ifc::name)
  change = set "North entrance"
  expectedSequence = 7
  schemaDigest = current root schema digest

owner stores proposal as pending; sequence stays 7
owner signs accept:
  proposalId = proposal transactionId
  proposalDigest = SHA-256(JCS(complete secured proposal))
  expectedSequence = 7

owner commits acceptance wrapper and graph change
owner signs receipt:
  transactionId = decision transactionId
  accepted = true
  sequence = 8
  transactionDigest = SHA-256(JCS(acceptance wrapper))
```

If another accepted transaction advances the authority to 8 first, this accept
MUST fail; the proposer reviews and signs a new proposal at 8. Rejection instead
uses current sequence 8 without applying the stale change.

## 6. Reference implementation evidence

Existing tests are useful evidence, but are not declared to pass every draft
requirement:

- [Data Integrity tests](../tests/test_clip_data_integrity.py).
- [DID tests](../tests/test_clip_did.py).
- [Graph/store/API tests](../tests/test_ifc_store.py) and
  [operations tests](../tests/test_ifc_operations.py).
- [Federation tests](../tests/test_clip_federation.py).
- [Gossip tests](../tests/test_clip_gossip.py).
- [Encryption primitive tests](../tests/test_content_crypto.py).
- [Supply-chain API tests](../tests/test_supply_chain_api.py) and
  [network tests](../tests/test_supply_chain_network.py).
- [Interoperability vectors/tests](../tests/test_ifc_interop.py).
- [Opt-in network regression](../tests/test_clip_network_integration.py).

The [compatibility register](decisions.md) identifies tests that would currently
expose draft gaps rather than demonstrate existing conformance.
