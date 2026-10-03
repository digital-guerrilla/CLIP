# CLIP Protocol Specification

**Construction Lineage Information Protocol (CLIP)**  
**Version:** 1.0-draft.1  
**Status:** working draft; not an adopted standard or production certification  
**Date:** 2026-10-03

CLIP is an application-layer protocol for exchanging attributable construction
information between independently controlled organisational authorities. It
separates an organisation's assertion, another authority's acceptance, the
resulting local state, and evidence of those actions.

This specification is programming-language, framework, database and deployment
agnostic. The interoperable v1 representation is JSON secured with Data Integrity
proofs. HTTPS is the first transport binding. IFCX is the first construction graph
profile, **not** the definition of CLIP itself.

## Reading order

| Document | Purpose | Status |
|---|---|---|
| [Core specification](specification.md) | Roles, identifiers, processing, state and conformance | Normative draft |
| [Message catalogue](messages.md) | Signed objects, exact fields, proofs and digests | Normative draft |
| [IFCX graph profile](ifcx-profile.md) | Construction data, composition and graph transactions | Normative draft |
| [Project and supply-chain profiles](workflows.md) | Membership, invitations, revisions, issues and decisions | Normative draft |
| [Federation and evidence profiles](federation.md) | Discovery, gossip, replication and encrypted evidence | Normative draft |
| [HTTPS/JSON binding](http-binding.md) | Endpoints, transport rules, errors and retries | Normative draft |
| [Security and privacy](security.md) | Trust boundaries, key lifecycle and threat handling | Normative draft |
| [Conformance and examples](conformance.md) | Implementation checklist, test cases and existing vectors | Normative tests; informative examples |
| [Compatibility and open decisions](decisions.md) | Prototype differences and decisions needed before release | Informative |
| [Core JSON Schema](schemas/core.schema.json) | Structural validation of core signed messages | Supporting draft |

Read the core and message catalogue first. Then implement only the profiles
needed by an application. A storage peer need not implement a supply-chain user
interface; a transaction verifier need not run gossip.

## Relationship to the demonstration codebase

The draft was derived from the **current working tree**, including uncommitted
changes, rather than only a historical release. Its baseline is the split
`/clip/v1` network/workflow and `/ifc/v1` graph interfaces. Existing legacy v3
interfaces and older `/ifcx/v1` aliases are not specified.

The draft deliberately does not make database tables, Python classes, SDK method
names, dashboard views, deployment roles or operator API keys protocol concepts.
Examples involving manufacturers, suppliers, contractors, owners and inspectors
are business roles, not globally enforced DID types.

The demonstration is evidence of implemented behaviour, not automatic proof of
conformance. Requirements that strengthen or resolve ambiguous prototype
behaviour are identified in [the compatibility register](decisions.md). No
implementation changes or compatibility promises are made by adding these docs.

## Scope and limitations

This package specifies attributable exchange, explicit acceptance, local
ordering, pinned dependencies, scoped disclosure and verifiable receipts.
It does not specify global consensus, a blockchain, a global asset registry,
geometry exchange, legal title transfer, individual-user identity, billing,
storage economics, guaranteed archival durability or certified IFC5 semantics.

A signature demonstrates integrity and authorised key use. It does not establish
that an engineering assertion is true or that a signatory has a legally recognised
role. Applications supply those policies.

## Versioning and editorial policy

`1.0-draft.1` is a **document version**, not a new field in existing signed
messages. `/v1` is a binding version; versioned schema/profile URNs identify
payload semantics. Changing signed fields, canonicalisation, digest coverage,
proof-purpose rules or state transitions requires an explicitly versioned
profile or incompatible binding. Unknown profiles MUST NOT be treated as v1.

The uppercase requirement words have the meaning described in the core
specification. All requirements remain draft requirements until adopted.
An implementation claiming compatibility with the prototype rather than this
draft should say so explicitly.

## External specifications

The following define primitives; CLIP does not redefine them:

- [RFC 2119](https://www.rfc-editor.org/rfc/rfc2119) and
  [RFC 8174](https://www.rfc-editor.org/rfc/rfc8174): requirement terminology.
- [RFC 8259](https://www.rfc-editor.org/rfc/rfc8259): JSON.
- [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785): JSON Canonicalization Scheme.
- [RFC 3339](https://www.rfc-editor.org/rfc/rfc3339): timestamp representation.
- [RFC 8032](https://www.rfc-editor.org/rfc/rfc8032): Ed25519.
- [DID Core](https://www.w3.org/TR/did-core/) and
  [did:web](https://w3c-ccg.github.io/did-method-web/): identity and resolution.
- [Data Integrity](https://www.w3.org/TR/vc-data-integrity/) and
  [Data Integrity EdDSA Cryptosuites](https://www.w3.org/TR/vc-di-eddsa/):
  `DataIntegrityProof` and `eddsa-jcs-2022`.
- [Multikey](https://www.w3.org/TR/controller-document/#multikey):
  verification-key representation.
- [RFC 4648](https://www.rfc-editor.org/rfc/rfc4648): Base64 encodings.
- [RFC 9110](https://www.rfc-editor.org/rfc/rfc9110): HTTP semantics.
- [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/schema):
  supporting structural schema.

The IFCX profile pins buildingSMART/IFC5-development commit
`1a63082ada967c683cfacee2005f8f749c8e1b79`; it is an alpha dependency.
The evidence profile additionally uses the public
[libsodium secretbox](https://doc.libsodium.org/secret-key_cryptography/secretbox)
and [sealed-box](https://doc.libsodium.org/public-key_cryptography/sealed_boxes)
formats, implementable without using that library.
