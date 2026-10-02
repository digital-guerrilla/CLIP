# CLIP Network And IFC Graph Protocol Profile

Status: implementation profile for the pinned IFCX alpha; no legacy wire support.

Network/authentication/discovery services use `/clip/v1`; graph datasets,
relationships, authoring and transactions use `/ifc/v1`. The graph wire format
remains IFCX. No former `/ifcx/v1` API aliases are retained.

## Identity And Addressing

Only organizations and network services receive DIDs. An entity/component is
addressed by `authorityDid`, `datasetId`, `entityPath`, `componentSchemaId`.
Native IFCX `children`, `inherits` and `Reference` values carry graph structure.
No parallel asset-record or signed-relationship envelope is defined.

## Composition

Dataset headers must match registered dataset IDs. Layer traversal is root
first, imports afterward, with later contributions overriding earlier ones.
Duplicate path contributions merge attributes, children and named inheritance.
Inheritance nulls remove a named inheritance edge. Component removal uses
`urn:clip:ifcx:component-deletions:v1`, not null attribute values.

Imports are signed IFCX publication envelopes, not unsigned JSON files. The
import `integrity` value pins exact response bytes with SRI SHA-256. Each
publisher must be operator-trusted, the DID must authorize the publication key,
and composition must pass schema validation. Limits apply to depth, layers and
bytes. Exact pinned bytes are cached; DID authorization is rechecked on use.

## Transactions

An `assertionMethod` proposal binds the component address, change, schema digest,
transaction UUID and expected authority sequence. An owner `capabilityInvocation`
decision binds the proposal UUID and its full signed JCS digest. Acceptance is
atomic with the layer update, local sequence increment, transaction and receipt.
Rejection records a signed receipt without incrementing the accepted sequence.
API keys authorize local management only; they cannot replace either DID proof.

Serialize typed models in JSON/alias mode before signing. Timestamps use UTC `Z`.
Proofs use W3C `eddsa-jcs-2022`, RFC 8785 and controlled Ed25519 Multikeys.

## Service Messages

Replication and fragment messages bind message UUID, actor DID, audience DID,
operation kind, complete payload and UTC creation time. Authentication proofs
must use a DID `authentication` key. Endpoints resolve from explicit
`ClipGossipService`, `ClipReplicationService` or `ClipEvidenceService` entries.
Freshness permits at most five minutes of age and thirty seconds of clock skew.
Persisted sequence/digest state prevents rollback; exact retries are idempotent.

Replication preserves the complete accepted authority history, publication and
receipts. Replicas do not become authorities and cannot issue owner decisions.
Verified receiver acknowledgements bind dataset, authority, sequence and digest.

## Evidence

The evidence reference schema is `urn:clip:construction:evidence-reference:v1`.
It carries evidence ID, publisher DID, retrieval URI and signed-manifest JCS
digest. An evidence manifest binds its target component, ciphertext fragments,
plaintext digest, retention deadline and recipient-wrapped keys. X25519 sealed
boxes deliver random SecretBox keys; no plaintext keys are returned or replicated.
Storage receipts cover complete nonce+ciphertext bytes. Repair must match the
original signed manifest. Expired fragments cannot be read and may be swept.

## Trust And Revocation

An owner's `trustedProposers` list authorizes component proposals independently
of `CLIP_TRUSTED_PUBLISHERS`, which authorizes imported publications and replica
placement. Neither DID possession nor a byte pin establishes business trust.
Revoked verification methods fail all verification, including historical proofs.
See [Operations](operations.md) for rotation and evidence-key retention policy.