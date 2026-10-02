# CLIP And IFC Graph Qualification Roadmap

## Implemented Migration

Separate CLIP network and IFC graph APIs, SDK and console; default DID gossip; authenticated proof-bundle
replication; sealed recipient evidence keys, signed manifests and fragment
receipts; retention and repair; IFC4.3/COBie mapping; publisher trust and
revocation policy; versioned migrations; DNS-pinned egress; and multi-authority
restart, offline/rejoin, replication and corrupt-cache recovery regression.

## Remaining Qualification

- Validate geometry/unit/property mappings against representative IFC4.3 projects
  and additional COBie sheets; the current converter preserves semantic source
  properties but does not convert geometry.
- Exchange fixtures with an external IFCX implementation. Independent JCS and
  Ed25519 vectors do not establish full upstream IFCX interoperability.
- Qualify PostgreSQL, concurrent migration/replication and rolling upgrades.
- Add automatic placement scheduling, quorum monitoring and retention repair SLAs.
- Replace whole-node operator API keys with scoped OAuth/OIDC or mTLS for
  individual users. Managed projects already enforce private dataset reads;
  public libraries and unmanaged legacy datasets remain anonymously readable.
- Integrate KMS/HSM signing, automated evidence rewrapping, external revocation
  audit and compromise recovery ceremonies.
- Add production telemetry, rate limits, tenant storage quotas and security review.

Exit gate: no production claim until these requirements are independently tested.