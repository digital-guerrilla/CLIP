# Discovery, gossip, replication and evidence profiles

## 1. DID service discovery

Resolve a `did:web` organisation using that method's HTTPS document location:
domain-only DIDs use `/.well-known/did.json`; path DIDs use the method's
path-based `did.json`. Production resolution MUST enforce the HTTPS and egress
rules in [security.md](security.md).

Service IDs MUST equal the DID plus the registered fragment below. `type` may
be a string or array containing the registered type. `serviceEndpoint` MUST
be a clean absolute HTTPS URL without user-info, query or fragment.

| Fragment | Service type | Operation advertised |
|---|---|---|
| `clip-gossip` | `ClipGossipService` | Peer digest exchange |
| `clip-replication` | `ClipReplicationService` | Receive proof bundle |
| `clip-evidence` | `ClipEvidenceService` | Store fragment; `/read` appended for repair reads |
| `clip-assets` | `ClipAssetService` | Authenticated project read |
| `clip-project-invites` | `ClipProjectInviteService` | Receive join request |
| `clip-supply-chain` | `ClipSupplyChainService` | Supply-chain action exchange |

Fragments and type strings above are CLIP v1 conventions, not claimed IANA or
DID-wide registrations. Services MAY be on a different public host from the
DID document; trust derives from the resolved controller document.
Clients MUST NOT guess a peer's service URL from its gossip URL.

Discovery locates services; it does not certify application capabilities.
Graph proposal endpoints are not separately advertised in the prototype.
Their base URL currently requires deployment configuration.

## 2. Gossip membership

A peer digest has `@context`, `fromDid`, `generation`, `knownDids`, `created`,
and an `authentication` proof. `knownDids` contains at most 512 distinct
`did:web` identifiers. It is not a list of assets or trust endorsements.

A peer MUST durably increase its generation for each new digest. Restart MUST
NOT reset it. A receiver MUST verify the DID/proof and reject its own DID as
sender. Only a generation strictly greater than that sender's stored generation
may refresh direct liveness or introduce learned peers.

For this draft, the digest timestamp MUST be within the service freshness window
`[-30,300]` seconds. It has no audience and its proof timestamp need not be
identical to the digest timestamp. The prototype lacks this freshness check;
generation alone does not prove an observation is recent.

Direct fresh authenticated contact marks the sender `alive`. Indirectly learned
DIDs start `unknown`, never `alive`. Local failed probes may mark a peer
`suspect`; timeout may mark it `dead`. Fresh direct higher-generation contact
can restore a dead peer. Probe intervals/timeouts are local operational policy,
not globally synchronised facts.

Gossip response senders MUST match the peer contacted. Replayed/old generations
MUST NOT revive a peer. Peer status MUST NOT modify construction facts,
permissions, accepted lineage or publisher trust.

## 3. Proof-bundle replication

Replication transmits:

```text
{
  "publication": secured IFCX publication for one dataset,
  "transactions": [
    {"transaction": acceptedWrapper, "receipt": securedAuthorityReceipt},
    ...
  ]
}
```

The outer service message is `kind: replication`; its actor MUST be the
publication's publisher. Replication to the same authority MUST fail.
The receiver MUST independently trust that publisher.

### 3.1 Scope and verification

Because sequence is authority-wide, `transactions` is the **entire authority's
accepted history**, including transactions for other datasets. It MUST be
contiguous from sequence 1; at most 10,000 entries are allowed in this profile.
The selected publication is one current dataset snapshot, not a snapshot of
all datasets.

For each entry at sequence `n`, a replica MUST verify:

- receipt authority, signature, `accepted: true`, sequence and wrapper digest;
- supported acceptance wrapper kind;
- proposal signature and target authority;
- owner capability decision and `decision: accept`;
- proposal/decision ID and secured digest linkage;
- both expected sequences equal `n-1`;
- receipt and wrapper transaction IDs equal the decision ID.

The receiver MUST also verify the publication proof. It MUST NOT apply an
unsigned wrapper or elevate a replica to the publisher's authority.

The current bundle cannot independently prove that the publication is the
result of replaying the history: no initial snapshot or signed publication
sequence binding is included. This profile is proof retention, not a complete
event-sourced reconstruction or consensus protocol.

### 3.2 Monotonic storage and acknowledgement

For the same publisher/dataset:

- lower sequence MUST fail;
- same sequence and different bundle digest MUST fail;
- an extension MUST preserve the exact old history prefix;
- same sequence and same digest is an idempotent retry.

After durable storage, return `replicationAcknowledgement` containing the
bundle digest, publishing `authorityDid`, `datasetId`, and full-history sequence.
The publisher MUST verify response signer/audience/freshness and every field
before recording a successful placement.

Full history can expose other private projects. The sending authority MUST
authorise the peer to read every managed project covered by the history; the
prototype applies this check across all managed projects. Dataset-only access
is insufficient. This is a significant privacy cost, not an implicit grant.

## 4. Encrypted evidence manifest

An assertion-signed evidence object has:
`@context`, `evidenceId`, `publisherDid`, `target`, `manifest`, `proof`.
The target authority MUST equal publisher. In the construction profile its
component schema is `urn:clip:construction:evidence-reference:v1`.

| Manifest field | Meaning |
|---|---|
| `name`, `mediaType` | Display name and content type |
| `expiresAt` | Timezone-bearing retention deadline |
| `contentSha256` | SHA-256 plaintext digest |
| `encryptedContentSha256` | SHA-256 of ordered ciphertexts excluding nonces |
| `chunkSize` | Plaintext chunk size, 1024..16777216 bytes |
| `fragmentCount` | Positive number of fragments |
| `recipients` | `{did, verificationMethod, wrappedKey}` objects |
| `fragments` | `{index, digest, sha256, size}` objects |

`evidenceId` MUST equal `encryptedContentSha256`. Fragment indices MUST be
distinct, contiguous from zero, and agree with `fragmentCount`.
`size` is full stored nonce+ciphertext size. The manifest is immutable for its ID.

Managed-project evidence recipients are the authority plus current project
members, snapshotted at upload. Arbitrary nonmember recipient additions MUST
fail. Later membership changes do not edit the signed recipient list.

Upload returns an evidence reference containing `evidenceId`, `name`,
`mediaType`, `uri`, `integrity`; its integrity is the secured manifest's JCS
digest, not SRI. Upload alone MUST NOT modify an accepted graph component;
attaching the reference uses the normal proposal/decision process.

## 5. Evidence cryptographic format

Generate a random 32-byte content key. Split nonempty plaintext into ordered
chunks. For each chunk, generate a fresh random 24-byte nonce and encrypt using
XSalsa20-Poly1305 in the NaCl/libsodium **secretbox** format.

```text
ciphertext_i = 16-byte Poly1305 authenticator || encrypted chunk_i
stored_i     = 24-byte nonce_i || ciphertext_i
fragment.sha256 = hex(SHA-256(ciphertext_i))
fragment.digest = hex(SHA-256(stored_i))
evidenceId = hex(SHA-256(ciphertext_0 || ciphertext_1 || ...))
```

Do not use an AES-GCM or XChaCha20 format under the same profile.
Receivers MUST check signed size and full stored digest before retaining or
using a fragment.

Recipient keys are X25519 `Multikey` values: `ec 01` followed by the 32-byte
public key, controlled by the recipient and authorised in `keyAgreement`.
`wrappedKey` is standard Base64 of a libsodium-compatible sealed box of the
32-byte content key:

```text
ephemeral X25519 public key (32 bytes) ||
crypto_box ciphertext (16-byte authenticator + 32 encrypted key bytes)
```

The sealed-box nonce is the 24-byte BLAKE2b digest of ephemeral public key
concatenated with recipient public key; encryption is the compatible
X25519/XSalsa20-Poly1305 box construction. Total wrapped-key size is 80 bytes.
Sealed boxes do not authenticate the encrypting sender on their own; the
authority-signed manifest supplies attribution.

After unwrap, decrypt in index order, validate every ciphertext digest and the
combined encrypted/plaintext digests, and only then return plaintext as verified.
Key splitting helpers in the prototype are not an implemented threshold/key
quorum protocol and are outside this profile.

## 6. Placement, receipts, repair and expiry

A `fragment` service payload contains the original secured `manifest`, `index`
and standard Base64 `content` of `stored_i`. Storage peers MUST opt in to storage,
trust the publisher, enforce capacity, verify both proofs/digests and reject
expired manifests or conflicting reuse of an evidence ID.

After durable storage a peer signs `fragmentReceipt` binding `evidenceId`,
`index`, full stored `digest` and `expiresAt`. The publisher MUST verify signer,
audience and exact commitments before marking that fragment placed.
Partial placement MUST remain distinguishable from complete placement.

The publisher can send `fragmentRead` for repair. The replica authorises only
the original publisher in this v1 profile and responds with `fragment` containing
the same manifest and index. Repair MUST reject mismatched manifests and bytes;
it MUST NOT replace an original digest with a newly computed one.

Placement is permission-gated at the publisher. A storage peer can receive the
manifest without receiving a wrapped content key for itself; however, manifests
still expose metadata and recipient relationships.

At `expiresAt`, reads MUST fail even if files remain on disk. Sweeping may
physically remove expired ciphertext. Expiry/receipts do not prove erasure of
every copy or guarantee future availability until expiry. No erasure coding,
quorum durability, economic incentive or automatic replica-count guarantee is
specified.
