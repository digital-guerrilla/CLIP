# CLIP Implementation Roadmap

## Codebase Review

The repository now has one protocol surface and one record schema. The working
demonstration covers self-certifying identity, canonical record proofs, signed
discovery, cross-authority relationship consent, immutable history, cache
fallback, bounded graph resolution, and six service roles.

The remaining work is production hardening, interoperability, and operational
assurance rather than another wire-format migration.

## Priority 0 - Conformance And Security

- Publish JSON Schema 2020-12 files and cross-language golden JCS/proof vectors.
- Verify both relationship proofs during every graph traversal, not only during
  owner acceptance.
- Reject duplicate JSON keys and enforce request, response, relationship, and
  canonicalization size limits.
- Add SSRF protection, production HTTPS-only discovery, redirect policy, DNS
  rebinding checks, and bounded HTTP connection pools.
- Add signed descriptor sequence persistence, expiry enforcement, rollback
  detection, key history, rotation, revocation, and controller delegation.
- Replace static write API keys with scoped OAuth/OIDC or mTLS authorization.

Exit gate: malicious descriptors, records, relationships, redirects, and replay
attempts fail deterministically without poisoning cache state.

## Priority 1 - Durability And Failure Recovery

- Introduce database migrations and schema version checks.
- Store immutable publish bundles containing record bytes, descriptor chain,
  relationship proofs, and evidence manifest.
- Implement replica acknowledgements and `under_replicated` publication state.
- Add mirror and content-addressed fallback with digest enforcement.
- Exercise offline authority, restored authority, corrupt cache, and lost DNS
  scenarios in automated multi-service tests.

Exit gate: a complete owner graph remains auditable with one authority offline,
and every stale or missing branch is reported explicitly.

## Priority 2 - Lifecycle And Privacy Profiles

- Add lifecycle DAG validation for issuer sequence, previous event, causes,
  supersession, dispute, revocation, and deterministic reducers.
- Add ownership transfer and controller delegation ceremonies.
- Implement separately signed public, partner, and confidential projections.
- Partition caches by authorization context and add privacy-preserving audit logs.
- Add encrypted evidence delivery and optional selective-disclosure credentials.

Exit gate: public callers cannot infer restricted fields and authorized parties
can reproduce lifecycle state from the same verified event set.

## Priority 3 - Interoperability And Operations

- Add IFC and JSON-LD mappings without changing signed source bytes.
- Add ETag/conditional retrieval, circuit breakers, metrics, traces, and health
  reporting for descriptor, record, cache, and graph outcomes.
- Add SDK cancellation, progress callbacks, typed failure results, and CLI tools.
- Test PostgreSQL, HSM/KMS signing providers, backup/restore, and rolling upgrades.
- Produce an independent implementation for conformance testing.

Exit gate: two independent implementations exchange and verify the same records,
and operators can diagnose partial graphs without logging confidential payloads.