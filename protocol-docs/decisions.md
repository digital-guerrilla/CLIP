# Compatibility register and pre-release decisions

This register is informative. It prevents draft requirements from being mistaken
for features already implemented. It is not a vulnerability review or a request
to alter the current demonstration.

## 1. Baseline

The baseline is the working tree inspected on 2026-10-03, including existing
uncommitted changes. The normative draft aims at independent interoperable
implementations while retaining existing message spellings and digest coverage.

| Area | Current prototype behaviour | Draft treatment / required next decision |
|---|---|---|
| Transport versus data | Network under `/clip/v1`; graph under `/ifc/v1`; IFCX alpha | Preserve separation; new graph formats require profiles |
| Service envelopes | Core uses `kind`; workflow uses `action` and top-level response `requestId` | Preserve two explicit profiles; do not claim one unified wire schema |
| Protocol version | Route versions, profile/schema URNs; no universal signed version field | Document version is not a wire change; capability negotiation deferred |
| Core proof verification | Typed model dump may add defaults/coerce/render values before proof verification | Draft requires original logical JSON verification; compatibility senders materialise defaults |
| JSON parsing | Outbound fetched JSON rejects duplicate members/non-finite constants; incoming framework parsing is not uniformly constrained | Draft requires unambiguous parsing on every protocol ingress |
| Context verification | Core cryptographic helper permits a proof-context prefix and substitutes it when hashing | Draft requires exact fixed context equality; stricter rule not universally implemented |
| Counter/numeric types | Model integer constraints; no universal JCS-safe upper bound | Draft requires exact JSON integer range and no boolean coercion |
| Acceptance precondition | Decision sequence checked atomically; proposal sequence not checked by local append path | Draft requires both at current sequence; replica verifier already expects proposal `n-1` |
| Proposal key at acceptance | Stored proposal not universally re-resolved at decision time | Draft rechecks proposer key; invitation acceptance already rechecks join key |
| Permission at acceptance | Managed-project contributor role rechecked | Preserve; unmanaged proposer-policy recheck semantics need release qualification |
| Submission sender policy | Authority, any member, or explicitly scoped sender allowed by project-access helper | Preserve; scoped approval permits nonmember submission without graph access |
| Multiple inheritance | Effective values follow map insertion order when branches conflict | JCS signatures do not bind object order; draft forbids/rejects ambiguous overlaps |
| Component override | Root appended contributions precede imported layers | Preserve; imported path collisions can still shadow a successful local contribution |
| Graph rejection label | `componentProposalRejection` used for graph rejection too | Preserve for wire compatibility; rename only in a new profile |
| Replication history | Full contiguous authority history with one dataset publication | Explicitly document privacy cost; dataset-only history is not compatible |
| Replica reconstruction | Verifies signatures/linkage, not replay from a signed initial state | Do not claim publication/history state equivalence or full recovery |
| Publication pin stability | Published proofs use current signing method; no immutable archive endpoint | Key rotation/response formatting can invalidate old byte pins; archive design needed |
| Import layers | Nominal 32; imported traversal can reject at `>=32` | Exact boundary to be fixed/qualified before stable release; use <=31 for prototype portability |
| Gossip freshness | Higher generation accepted without timestamp-age check | Draft adds freshness; generation alone cannot prove recent contact |
| Peer growth | Per-message 512 known-DID cap; global membership budget not clearly specified | Release needs eviction/admission and total-growth limits |
| Service replay | Freshness/audience plus profile idempotency; no universal message-ID replay cache | Do not promise exactly-once processing; add versioned replay policy if needed |
| Core retry recovery | Duplicate proposals/decisions may conflict; no universal result-by-ID API | Document recovery limits; stable release should define authenticated outcome retrieval |
| Document grants | Explicit revoked/expired grant denies even prior issue selection | Preserve and make explicit; downloaded copies remain uncontrollable |
| Evidence manifests | Outer typed model; inner manifest is a flexible dictionary | Draft requires full inner invariants; schema/receiver qualification still needed |
| Evidence algorithms | Secretbox and libsodium sealed-box bytes | Define byte algorithms, not library/API dependence; independent crypto vectors needed |
| Evidence revocation | Recipient envelopes frozen at upload | No retroactive revocation or automatic envelope update promised |
| Private caching | Access checked, private cache headers not uniform | Draft adds `no-store`; binding implementation work remains |
| Error vocabulary | HTTP detail strings/validation arrays; classifications vary | Keep status-level semantics; machine-readable signed errors require a new profile |
| Dependency limits | Sources capped, ancestry checked, transport bytes limited | Total graph work/node budgets need precise interoperable limits |

## 2. Questions before a stable 1.0

The following need explicit protocol-owner decisions, not accidental freezing
of demonstration implementation details:

1. Adopt the stricter context/raw-JSON/numeric/sequence/key recheck rules and
   define any migration period for prototype peers.
2. Resolve conflicting inheritance precedence with either an explicit signed
   order or an enduring rejection rule.
3. Decide whether future replication is authority-wide proof retention or
   dataset-scoped recoverable state with signed snapshot-sequence linkage.
4. Define capability discovery and direct graph-authority service advertisement.
5. Define an authenticated result-by-ID endpoint and a consistent idempotency/
   replay policy for core transactions.
6. Define immutable byte-publication archival URLs and historic DID verification
   policy across key rotation/revocation.
7. Publish complete machine-readable schemas for IFCX and optional workflow/
   evidence payloads, independent known-answer vectors and release test results.
8. Establish stable maximum body, dependency, membership and storage work budgets.
9. Decide governance for schema/profile/service identifiers, compatibility policy
   and release ownership.

These are deliberately not presented as already implemented new APIs.

## 3. Source map

This is a navigation aid for checking the baseline, not a normative dependency
on this code or programming language.

| Contract | Baseline implementation |
|---|---|
| Core transport messages | [clip_protocol.py](../node/app/core/clip_protocol.py) |
| Graph transactions | [ifc_protocol.py](../node/app/core/ifc_protocol.py) |
| Proof algorithm | [data_integrity.py](../node/app/core/data_integrity.py) |
| DID relationship/key policy | [did.py](../node/app/core/did.py) |
| Discovery document | [DID endpoint](../node/app/api/did.py) |
| Graph/schema semantics | [ifcx_models.py](../node/app/core/ifcx_models.py), [ifc_graph.py](../node/app/core/ifc_graph.py) |
| Atomic graph history | [ifc_store.py](../node/app/db/ifc_store.py), [transaction API](../node/app/api/ifc_transactions.py) |
| Import pinning/composition | [clip_layers.py](../node/app/federation/clip_layers.py) |
| Project/read/invite behaviour | [clip_projects.py](../node/app/api/clip_projects.py), [project_access.py](../node/app/core/project_access.py) |
| Business revision/issue/decision | [supply_chain.py](../node/app/core/supply_chain.py), [workflow API](../node/app/api/clip_supply_chain.py) |
| Membership | [clip_network.py](../node/app/core/clip_network.py), [clip_gossip.py](../node/app/federation/clip_gossip.py) |
| Replica verification | [clip_replication.py](../node/app/federation/clip_replication.py), [replica API](../node/app/api/clip_replication.py) |
| Evidence format | [content_crypto.py](../node/app/core/content_crypto.py), [evidence API](../node/app/api/clip_evidence.py) |
| Egress/parser protection | [egress.py](../node/app/core/egress.py) |
