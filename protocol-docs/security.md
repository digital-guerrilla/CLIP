# Security and privacy requirements

This is a protocol threat model, not a security audit or production certification.
The requirements apply to the selected conformance roles.

## 1. Trust boundaries

Treat peer input, DID documents, service URLs, imports, snapshots, fragment
manifests and attachments as untrusted. Signature validity does not eliminate
resource-exhaustion or business-authorisation risks.

The security model assumes uncompromised signing/decryption keys, correct
cryptographic implementations, trustworthy HTTPS resolution at verification
time, secure local administration and durable atomic storage.

Compromise of a DID's domain/TLS/controller document can redirect discovery and
authorise new keys. `did:web` is not a transparency log or proof of historic
domain ownership.

## 2. Verification and key lifecycle

- MUST reject mismatched DID document ID, method controller, proof purpose,
  unsupported cryptosuite, invalid signature and revoked verification methods.
- MUST enforce `validFrom <= proof.created < validUntil` when the controller's
  method supplies those timezone-bearing bounds.
- MUST honour the profile's `revoked` method flag and
  `revokedVerificationMethods` list as controller-policy extensions.
- MUST separate historic proof time from current authorisation: a signature
  claiming an old timestamp does not prove it was actually made before compromise.
- MUST preserve old authorised methods when historic verification is desired;
  removing/revoking them may make old proofs fail under current resolution.
- SHOULD use protected key storage and audited rotation. The protocol does not
  mandate a KMS, HSM or a key derived from the prototype's local key files.

The prototype publishes one Ed25519 method for multiple proof purposes; independent
deployments MAY use separate keys. X25519 evidence-key publication does not
require deriving it from an Ed25519 key as the demo does.

No historical DID-document pin, trusted timestamp service, revocation
transparency or long-term legal-signature validation is defined in v1.

## 3. Threat handling

| Threat | Required control | Residual limitation |
|---|---|---|
| Forged/modified construction assertion | Verify exact secured JSON, DID relationship and proof | Does not establish real-world truth |
| Unauthorised acceptance | Owner capability proof plus current policy and atomic precondition | Owner may knowingly accept false data |
| Replay | Fresh audience-bound service envelopes; immutable IDs/digests; durable generations | Core service profiles have no universal nonce ledger |
| History rollback/equivocation | Contiguous receipts, prefix preservation, conflicting digest rejection | No global detection across colluding replicas |
| SSRF/DNS rebinding | HTTPS, no redirects, public-only DNS, connection address pinning, hostname/TLS verification | Public endpoints can still be malicious |
| Malicious JSON/schema/dependencies | Duplicate-member rejection, JCS limits, depth/size/count bounds, cycle checks | Domain complexity needs operational budgets |
| Ciphertext substitution | Signed nonce+ciphertext digest, authenticated decryption, plaintext digest | Receipt alone does not promise future storage |
| Invitation theft | High-entropy token, HTTPS, token digest storage, owner review, expiry/revocation | Thief can reserve code; owner must inspect requester DID |
| Private-data leakage | Current read policy, explicit disclosure, cache controls, source grants | Already downloaded or public data cannot be recalled |

Production outbound connections MUST use only the validated public DNS answer
set, preserving original hostname for certificate verification/SNI. Checking
DNS and later reconnecting by an unchecked hostname is insufficient.
Unix sockets, file URLs and credential-bearing URLs are not peer transports.

## 4. Evidence confidentiality

Content keys and private signing keys MUST NOT be transmitted in plaintext.
Nonces MUST not repeat under a content key. Stored fragments MUST contain only
nonce/authenticator/ciphertext, not the content key.

The signed manifest is **not confidential**. It reveals names, media types,
target addresses, digests, sizes, retention and recipient DIDs. Plaintext
digests may permit guessing attacks on predictable content.

Recipient-key envelopes are fixed at upload. Removing a project member cannot
invalidate that member's previously acquired key or plaintext. A new recipient
cannot decrypt an old manifest unless a separately versioned re-encryption or
key-delivery workflow is defined. This draft does not invent such a workflow.

Ciphertext replication SHOULD avoid unnecessary metadata disclosure.
Expiry MUST gate reads but MUST NOT be advertised as verifiable erasure.

## 5. Operational obligations

Implementations SHOULD rate-limit public endpoints, budget cryptographic/DID
resolution work, protect operator APIs, monitor failures and back up signed
history plus keys under a documented recovery policy.

Core data mutations and their receipts MUST survive restart together. A system
MUST NOT acknowledge durable placement before the corresponding storage commit.
Filesystem/database coordination is implementation-specific; both must be
reconciled after a crash.

Audit logs SHOULD record actor, resource, message/digest, outcome and correlation
without tokens, operator credentials, private content or unwrapped document keys.

The demo's automatic local operator access MUST NOT be enabled on a production
public interface. Deployment topology and business-role labels do not replace
authorisation.
