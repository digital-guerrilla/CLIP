# CLIP IFCX: Governed Construction Evidence

## Model

Organizations control `did:web` identities. Native IFCX paths identify product
types, occurrences and construction events. Schema-typed components contain
facts; hierarchy and inheritance remain native IFCX. Components have structured
addresses, not individual DIDs.

## Governance

A contractor signs a component proposal. The owner verifies the actor's DID,
proposer policy, schema and expected sequence, then separately accepts or rejects.
Only acceptance changes the graph. A signed receipt preserves the agreement and
the authority-local sequence; there is no global consensus.

## Federation

Manufacturer publications are DID-signed and imported with exact byte pins and
publisher allowlists. Separate DID gossip discovers services. Authenticated
replication preserves proof bundles without transferring authority. Receiver
acknowledgements commit to the replicated digest and sequence.

## Evidence

Documents are encrypted into authenticated fragments. Signed manifests bind
target components, ciphertext, retention and recipient-sealed keys. Replicas
store no plaintext keys. Missing fragments can be repaired against the original
manifest. Evidence attachment still needs owner acceptance.

## Demonstration

Start the six-node PowerShell demo and open the owner console on port 8104.
Observe manufacturer type inheritance, the accepted installation event, DID
peers and transaction receipts. The opt-in regression additionally proves owner
restart, offline/rejoin, transaction replication, cache recovery and evidence repair.

## Boundaries

The vocabulary is pinned IFCX alpha, not a final IFC5 standard. IFC4.3 and COBie
converters are semantic profiles, not geometry engines. This is a research
implementation with explicit production qualification work, not a certified
identity, confidentiality or durability service.