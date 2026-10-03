# CLIP IFCX graph profile

## 1. Profile boundary

This profile uses the IFCX alpha vocabulary pinned to buildingSMART commit
`1a63082ada967c683cfacee2005f8f749c8e1b79`, with `ifcxVersion: "ifcx_alpha"`.
It does not claim compatibility with arbitrary future IFCX/IFC5 revisions.
IFC4X3_ADD2 class checking is an authoring convention, not STEP generation or
IFC5 certification.

IFCX describes construction data; CLIP adds publisher proofs, byte-pinned
imports, controlled changes, accepted history and a deletion extension.

## 2. File and schema structure

An IFCX file has four required members:

| Member | Shape |
|---|---|
| `header` | `id`, `ifcxVersion`, `dataVersion`, `author`, `timestamp` strings |
| `imports` | Ordered array of `{uri, integrity}` |
| `schemas` | Map of schema ID to schema definition |
| `data` | Ordered array of node contributions |

Node contributions have `path`, `children`, `inherits`, `attributes`.
The last three are maps and SHOULD be explicit even when empty. `children`
and `inherits` map relationship names to target paths or null. `attributes`
maps schema IDs to values.

A schema is `{ "uri": optionalURI, "value": valueDescription }`.
Value-description fields are `dataType`, `optional`, `inherits` (schema IDs),
`quantityKind`, `enumRestrictions`, `arrayRestrictions`,
`objectRestrictions`. Optional fields may appear as null in the prototype's
normalised wire representation.

| `dataType` | Validation |
|---|---|
| `Real` | Finite JSON number; not boolean |
| `Integer` | Integral number; not boolean |
| `Boolean` | JSON boolean |
| `String`, `DateTime`, `Reference` | String; domain profiles add format/reference constraints |
| `Enum` | String in required `enumRestrictions.options` |
| `Array` | Required `arrayRestrictions.value`; optional `min`/`max` length bounds |
| `Object` | Object; `objectRestrictions.values` describes declared fields |
| `Blob` | Reserved by the pinned model; unsupported by this CLIP attribute-validation profile |

Schema inheritance MUST resolve to known schemas and MUST NOT cycle.
Attributes MUST reference known schemas. Declared object fields marked optional
may be absent; required ones may not. This is an IFCX schema language, not
interchangeable with JSON Schema.
Unknown object members remain permitted by this profile unless a domain schema
adds restrictions. `Blob` attributes MUST be rejected; the encrypted-evidence
profile represents binary content separately using Base64.

## 3. Registration and schema preconditions

The authority MUST validate a root file and its resolved dependencies before
registering it. Dataset identity MUST match `header.id`. Duplicate registration
of the same local dataset ID MUST conflict, not silently replace its history.

The CLIP component-deletion schema is added at registration and included in
`schemaDigest`. The proposer allowlist is local policy outside IFCX semantics.
An update to it MUST NOT be represented as a construction component.

The schema digest algorithm is defined in [messages.md](messages.md).
Changing schema/import definitions invalidates outstanding schema preconditions.
V1 does not define a general remote schema-migration operation.

## 4. Imports and stack order

Each remote import MUST reference a signed publication and MUST pin its exact
response bytes with SHA-256 SRI. The importing authority MUST trust the
publisher, not merely trust the URL or TLS certificate.

The resolver traverses depth-first in each file's import-array order:

```text
root, first import, first import's descendants, second import, ...
```

Each URI is expanded once; every occurrence's byte pin is still checked.
Cyclic URI/dataset chains and different contents reusing the same dataset ID in
one composition MUST fail.

The compatibility limits are 16 MiB per fetched publication, nesting depth 8
(root depth 0), and a nominal 32 layers including root. The prototype has an
off-by-one boundary for imported compositions; see [decisions.md](decisions.md).

**Later layers override earlier layers.** Imports come after the root, so an
import at the same entity path can override a root contribution. A consumer
MUST NOT assume that the root always wins.

## 5. Composition algorithm

For each layer, in order, then each node contribution, in data-array order:

1. Create or locate the entity at its exact `path`.
2. Replace schema definitions with later definitions of the same schema ID.
3. Merge `attributes` by schema ID; later values replace earlier whole values.
4. Merge named `children`; a later null removes that named relationship.
5. Merge named `inherits`; a later null removes that named inheritance edge.

Object-valued attributes are replaced as values, not recursively merged.
Null attributes are not component deletion.

To compute effective components, resolve inherited entities, combine their
effective components, then apply the entity's own components and finally its
deletion mask. Do not expose the mask itself as an effective construction
component.

Inheritance references first resolve by exact entity path; otherwise the
prototype supports slash-separated traversal from an entity through named child
edges. Missing targets and cycles MUST fail when effective values are resolved.

JSON object member order is not a signed precedence mechanism. Until a versioned
inheritance-order rule is adopted, draft-conforming producers MUST NOT generate
multiple inherited branches with conflicting effective values for the same
component. Consumers MUST reject that ambiguity rather than choose based on
parser insertion order. Non-conflicting branches are permitted.

## 6. Deletion extension

Schema ID: `urn:clip:ifcx:component-deletions:v1`.

Its value is an IFCX array of strings, each string identifying a schema ID
masked on that entity. The definition is:

```json
{
  "uri": "urn:clip:ifcx:component-deletions:v1",
  "value": {
    "dataType": "Array",
    "arrayRestrictions": {"value": {"dataType": "String"}}
  }
}
```

This is the semantic definition; the prototype serializer also emits nullable
description members. A conflicting definition MUST be rejected.

A component `remove` appends a local node contribution updating the mask.
It MUST target a currently effective component. A later `set` appends its
non-null value and removes that schema ID from the local mask. Neither operation
may publicly target the mask schema itself.

A consumer that ignores this extension cannot claim CLIP effective-component
conformance.

## 7. Graph operations

- `create`: path MUST not exist in the composed graph or earlier creates in the
  same batch.
- `contribute`: path MUST belong to the local root file before the operation
  batch; imported-only entities cannot be contributed to by this operation.
- Operations append contributions to the local root; they do not rewrite an
  imported publisher's file.
- The entire resulting graph MUST validate before commit. Named child targets
  MUST exist; containment and inheritance MUST be acyclic.
- The batch is atomic. V1 has no graph-entity delete operation.

Component proposals are narrower: they address an effective entity/component
in the composed dataset and append a local value/mask. They do not transfer
control of the imported source. Imported later layers can still shadow a local
contribution at the same path.

## 8. Construction extensions and projections

Versioned `urn:clip:construction:*:v1` schemas represent source provenance,
events, references and evidence. Implementations MUST preserve their schema
IDs; they MUST NOT present them as buildingSMART-defined schemas.

IFC STEP and COBie mappings are adapters, not alternative CLIP message types.
Geometry, complete IFC property vocabularies and full COBie validation are
outside this profile.

Current/pinned manufacturer projections are derived read views. They MUST NOT
rewrite immutable IFCX publications, dependency pins or signed issue snapshots.
Current views may report previously observed verified revisions without
claiming that a live publisher refresh succeeded.
