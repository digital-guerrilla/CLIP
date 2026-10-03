# CLIP Operations

## API And Configuration Namespaces

CLIP owns the network: DID authentication, gossip, replication, evidence transport,
project permissions/invitations and supply-chain exchange use `/clip/v1`.
IFC owns the graph: datasets, graph/component transactions, entity authoring and
construction imports use `/ifc/v1`. IFCX remains the actual serialized file format;
its `ifcxVersion`, `children`, `inherits`, schemas and attributes are unchanged.

There are no `/ifcx/v1` route aliases or `IFCX_*` setting aliases. Update clients
and environment files together. Gossip uses `CLIP_GOSSIP_ENABLED`,
`CLIP_GOSSIP_SEEDS`, `CLIP_GOSSIP_INTERVAL`, `CLIP_GOSSIP_SUSPECT_TIMEOUT` and
`CLIP_GOSSIP_DEAD_TIMEOUT`; URL-based `GOSSIP_SEEDS` has been removed.

## Deployment Defaults

Set `DID_WEB_ID`, `DID_VERIFICATION_METHOD`, `NODE_API_BASE`, `API_KEY`,
`PRIVATE_KEY_FILE`, `DATABASE_URL` and `DOCUMENT_STORAGE_DIR` explicitly.
`DID_VERIFICATION_METHOD` must be a fragment of the local DID. Persist the key
file and database together; replacing the key changes the authority's ability
to verify existing records. Keep operator keys out of source control.

`CLIP_DEMO_OPEN_ACCESS` defaults to `false`. The six-node demo enables it for
local operator access without API keys; do not enable it outside loopback demos.

DID gossip defaults on. `CLIP_GOSSIP_SEEDS` is a comma-separated list of
`did:web` identifiers, not URLs. Disabling `CLIP_GOSSIP_ENABLED` stops gossip;
it does not enable an alternate protocol. Live generations survive restarts,
and dead peers are probed so they can rejoin.

`CLIP_TRUSTED_PUBLISHERS` is a comma-separated explicit DID allowlist for
imports and received replica/evidence placement. Empty means deny external
publishers. Dataset proposer lists remain separate owner-managed policy.

Supply-chain catalogue discovery and source selection also require explicit
publisher approval through `CLIP_TRUSTED_PUBLISHERS`. Scoped submission sender
approval is managed per receiving project under
`/clip/v1/supply-chain/projects/{projectId}/senders`; it does not grant broad
project read or Contributor privileges. See
[Supply-chain workflows](supply-chain-workflows.md) for immutable revisions,
recipient decisions and issuer-controlled workflow document grants.

## Egress And DNS

Production DID and import traffic requires HTTPS and public DNS answers. The
connection backend resolves and validates every answer, then connects to a
validated literal address while preserving the original TLS hostname. Private,
reserved, mixed-public/private answers and non-443 production ports fail closed.
Redirects and environment proxies are disabled. Configure
`CLIP_EGRESS_ALLOWED_HOSTS` for an additional exact hostname allowlist.

`CLIP_ALLOW_HTTP_LOOPBACK=true` is solely for local demos and tests. It permits
HTTP only to literal loopback/localhost destinations, not Docker service names
or arbitrary private networks. The Compose demo shares one network namespace
to preserve this rule. Production never enables that exception.

Enforce matching firewall and DNS policies outside the process. Disable metadata
service access, control DNS resolvers, terminate TLS, and monitor denied egress.
Application checks are not a substitute for network isolation.

## Key Rotation And Revocation

1. Back up the database, existing key and evidence-recipient keys securely.
2. Provision the new signing key under a new `DID_VERIFICATION_METHOD` fragment.
3. Put the previous public Multikey descriptor in JSON `DID_PREVIOUS_KEYS`, with
   timezone-aware `validFrom` and exclusive `validUntil` timestamps. Keep its
   `controller` equal to the authority DID. Publish only public key material.
4. Replace the active key file and restart the authority. Historic proofs are
   accepted only when their creation time lies within the previous key window.
5. For compromise, add the old method ID to JSON `DID_REVOKED_METHODS` and remove
   it from historic descriptors. Revocation invalidates all its proofs, even
   earlier ones. Reissue affected publications and explicitly renew import pins.

This is operator-managed rotation, not an automated ceremony. Historical key
descriptors must come from controlled records; backdated signatures cannot prove
when a key holder actually signed. Keep revocation records outside this node for
audit. Retain old X25519 recipient private keys until evidence is expired or
rewrapped; changing signing seeds also changes the derived evidence key.

## Persistence And Recovery

Startup applies numbered frozen migrations and records their checksums. Future
schema versions, changed migration checksums and incompatible existing tables
abort startup. Back up first and deploy only one schema upgrader at a time.
Migration 9 renames the earlier IFCX infrastructure/graph tables to `clip_*` and
`ifc_*`, and the ledger to `clip_schema_revisions`, without changing stored JSON,
signatures or prior migration checksums. This is not an old-API compatibility
layer. There is no automatic downgrade or conversion of pre-IFCX legacy data. SQLite is the
tested database; PostgreSQL/rolling migration qualification remains work.

Import cache entries bind URI and SRI pin. A corrupt entry is evicted and fetched
again. Expired entries are refreshed; pin mismatch fails, rather than silently
following an updated publisher snapshot. DID key checks remain live. An offline
DID therefore yields an explicit dependency failure rather than unchecked trust.

Replication push is operator-triggered and retryable. Stored acknowledgements
prove the receiver's placement claim, not indefinite availability. Use
`/clip/v1/replication/status` to inspect acknowledgements. Automated scheduling,
quorum guarantees and redundant placement policy are not implemented.

## Evidence Retention

Enable replica storage with `ENCRYPTED_STORAGE_OPT_IN` and set a finite
`ENCRYPTED_STORAGE_CAPACITY_BYTES`. Choose per-upload retention explicitly.
Only trusted publishers can place replicas. Replica nodes do not retain plaintext
keys. Inspect manifests/receipts, place evidence with `/evidence/{id}/replicate`,
and repair from a selected peer with `/evidence/{id}/repair`.

Run authenticated `POST /clip/v1/evidence/retention/sweep` from a scheduler to
physically remove expired fragments. Reads fail after expiry even before the
sweep. Deletion cannot revoke copies already decrypted by a recipient.

## Validation

Run the unit suite, the opt-in six-authority regression and independent proof
vectors before deployment. The console keeps operator keys only in memory and
renders authority data as text. Managed projects are private by default;
public libraries and unmanaged legacy datasets allow anonymous reads. Partner
project reads require audience-bound DID authentication and project membership.
OAuth/mTLS individual-user authorization, KMS and production monitoring remain
qualification work. Do not describe the present deployment as production-ready.