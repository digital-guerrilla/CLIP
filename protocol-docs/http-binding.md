# HTTPS/JSON binding v1

## 1. Transport

Production endpoints MUST use HTTPS with normal certificate and hostname
verification. JSON requests/responses use `application/json`, UTF-8. DID
resolution may request `application/did+json`. No new CLIP-specific media type
is registered by this draft.

Signed objects are request bodies, not signatures of an HTTP request line.
An intermediary MUST NOT mutate secured JSON values. JSON whitespace and
member ordering do not affect JCS signatures, but **do** affect byte-pinned
import response integrity.

Automatic redirects MUST NOT be followed for DID resolution, imports or service
exchange. Service discovery MUST use the DID-advertised endpoint. The public
prototype profile restricts outbound production destinations to public IPs
and port 443, with optional operator hostname allowlisting.

HTTP on explicitly configured loopback is a development exception, not
production conformance. Operator secrets MUST never accompany peer requests.
The prototype's `x-api-key` authenticates a local operator, not an organisation's
cross-authority protocol identity.

## 2. Peer-facing operations

These are the demo's default routes; advertised service endpoints take precedence
where a service is defined.

| Operation | Method and default route | Body/result |
|---|---|---|
| Resolve controller document | `GET /.well-known/did.json` | DID document; path DIDs use method-specific location |
| Get public IFCX publication | `GET /ifc/v1/datasets/{id}/publication` | Signed publication |
| Submit graph/component assertion | `POST /ifc/v1/proposals` | Signed proposal -> `proposalId`, `proposalDigest`, `status: pending`; 201 |
| Decide graph proposal | `POST /ifc/v1/decisions` | Signed decision -> signed authority receipt; 200 |
| Read project | `POST /clip/v1/projects/read` | `projectRead` -> `projectView` |
| Redeem at invite owner | `POST /clip/v1/projects/invites/receive` | `{token,message}` -> join acknowledgement |
| Gossip sync | `POST /clip/v1/network/gossip/sync` | `{digest: securedPeerDigest}` -> same wrapper shape |
| Retain proof bundle | `POST /clip/v1/replication/receive` | `replication` -> signed acknowledgement |
| Store encrypted fragment | `POST /clip/v1/evidence/fragments` | `fragment` -> signed fragment receipt |
| Read repair fragment | `POST /clip/v1/evidence/fragments/read` | `fragmentRead` -> signed `fragment` |
| Workflow exchange | `POST /clip/v1/supply-chain/receive` | Supply-chain action -> actionResponse |
| Public catalogue | `GET /clip/v1/supply-chain/catalogue` | Published revision list |
| Public immutable revision | `GET /clip/v1/supply-chain/catalogue/{id}/revisions/{revision}` | Secured record snapshot |

Dynamic path segments MUST be percent-encoded as single segments. Query
parameters MUST be encoded separately. Clients MUST NOT interpret an opaque
dataset ID such as `urn:clip:project:...` as a URL.

Graph proposal/decision routing requires an authority base URL established
out of band in this version. Public data reads and metadata discovery do not
constitute write authorisation.

## 3. Local management and derived read operations

These routes are conveniences for controlling one's own node. Their existence
does not impose a Python SDK, GUI or local API-key mechanism on an independent
implementation. An implementation exposing them SHOULD preserve these shapes.

| Area | Routes | Access/semantics |
|---|---|---|
| Dataset registration | `GET/POST /ifc/v1/datasets` | POST `{file,trustedProposers}`; local operator |
| Proposer policy | `PUT /ifc/v1/datasets/{id}/trusted-proposers` | Unmanaged dataset policy; managed projects use membership |
| Graph reads | `GET /ifc/v1/datasets/{id}/graph`, `/components`, `/history` | Current read policy |
| Component query | `/components?entity_path=...&component_schema_id=...` | Structured address -> value |
| Project setup | `GET/POST /clip/v1/projects`, `PUT /clip/v1/projects/{id}/permissions` | Revision-checked local policy |
| Authoring templates/entities | `GET /ifc/v1/projects/templates`, `POST /ifc/v1/projects/{id}/entities` | Local authoring commits through proposal/decision |
| Invitations | `POST/GET /clip/v1/projects/{id}/invites` | Local owner; generate/list |
| Invite control | `POST /clip/v1/projects/invites/{inviteId}/revoke`, `/invites/redeem` | Local owner/requester respectively |
| Join review | `GET /clip/v1/projects/{id}/join-requests`, `POST /clip/v1/projects/join-requests/{requestId}/decision` | Owner; expected permission revision |
| Peer observation | `GET /clip/v1/network/gossip/peers` | Local health view |
| Replication management | `POST /clip/v1/replication/push`, `GET /clip/v1/replication/status` | Operator placement/status |
| Evidence management | `POST /clip/v1/evidence/upload`, `POST /clip/v1/evidence/{id}/replicate`, `/{id}/repair` | Local publisher |
| Evidence retention/read | `POST /clip/v1/evidence/retention/sweep`, `GET /clip/v1/evidence/{id}`, `/{id}/fragments/{index}` | Operator; expiry enforced |

Graph `/components` returns `authorityDid`, `datasetId`, `entityPath`,
`componentSchemaId`, `value`. `/history` returns `items` containing transaction
wrappers and receipts for that dataset, not a contiguous authority log.
`product_view=current|pinned` selects a projection; `refresh_products=true` on
graph reads requires operator authorisation and MUST surface refresh failures.

Supply-chain local record/document/submission routes are described in the
existing [workflow API guide](../docs/supply-chain-workflows.md#api). Their
inter-authority message semantics are specified in [workflows.md](workflows.md).

## 4. Failure mapping

No CLIP-specific signed error envelope is defined in v1. The prototype returns
HTTP errors with a JSON `detail`, either text or structured validation details.
Clients MUST NOT depend on English error-text matching.

| HTTP status | Meaning |
|---|---|
| 400 | Invalid operation/request not otherwise classified |
| 401 | Invalid authentication/proof or expired service envelope |
| 403 | Actor authenticated but policy/authority forbids operation |
| 404 | Resource unknown or intentionally hidden by access policy |
| 409 | Stale precondition, duplicate ID, conflicting immutable state |
| 410 | Expired/closed invitation or expired evidence retention |
| 413 | Transport body exceeds configured limit |
| 422 | Invalid shape, unsupported operation/profile or invalid domain value |
| 424 | Dependency resolution/verification or peer operation failed |
| 503 | Local authority/service not configured/available |
| 507 | Storage capacity exhausted |

Not every prototype code path uses identical classification; e.g. some malformed
fragments are returned as 401 and some dependency proofs as 424.
HTTP status is not a signed business decision. A failed operation MUST NOT
be represented by a positive receipt.

The binding MUST conceal private resources consistently, including lists.
Local diagnostic logs MAY retain more detail than a public failure response.
Sensitive tokens, content keys and operator keys MUST NOT be logged.

## 5. Retry and recovery

Clients MUST distinguish retryable transport failure from policy conflict:

- On timeout after submission, the peer may already have committed the outcome.
- Retry immutable business issues with the same secured content in a fresh
  service envelope; do not update its `created`/proof fields.
- Use bounded backoff for transient unavailability. Do not retry authentication
  failures indefinitely.
- On 409, retrieve/review current state before signing a new intent.
- Verify signed acknowledgements before considering placement/delivery complete.
- A partial fragment-placement failure does not undo prior acknowledged fragments.

A lost graph decision response has no universal v1 result-by-ID endpoint.
Recover through authorised history/project views or implementation-specific
operator facilities. The gap is recorded in [decisions.md](decisions.md).

## 6. Limits and caching

The binding MUST enforce body, response, nesting, processing and storage limits
before resource exhaustion. Existing fixed limits include 1,000,000 DID response
bytes, 16 MiB per import, 32 graph operations, 512 known peer DIDs and 10,000
replication history entries. Document/service/body byte limits are deployment
configured; senders MUST NOT assume unlimited uploads.

Private responses MUST NOT enter shared/public caches. This draft requires
`Cache-Control: no-store` for private project/document/invitation responses.
The prototype does not uniformly emit that header yet.

Import caches MUST retain the exact pinned response bytes and recheck both byte
integrity and current DID key authorisation. Cache expiry is local policy and
does not authorise replacing a pinned revision with "latest".
