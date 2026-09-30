"""Pydantic models for the CLIP v3 wire protocol."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .guid import is_valid_clip, parse_clip


SCHEMA_VERSION = "3.0"
PROOF_TYPE = "ClipJcsEd25519Signature2026"


class RecordKind(str, Enum):
    TYPE = "type"
    INSTANCE = "instance"
    ASSERTION = "assertion"
    COLLECTION = "collection"


class RelationshipRole(str, Enum):
    MANUFACTURER = "manufacturer"
    SUPPLIER = "supplier"
    MAIN_CONTRACTOR = "main_contractor"
    INSTALLER = "installer"
    OWNER = "owner"
    OPERATOR = "operator"
    MAINTAINER = "maintainer"
    INSPECTOR = "inspector"


class RelationshipType(str, Enum):
    DEFINES_TYPE = "defines_type"
    INSTALLED_BY = "installed_by"
    CUSTODY_EVENT = "custody_event"
    PROCURED_UNDER = "procured_under"
    COMMISSIONED_BY = "commissioned_by"
    CONTAINS_COMPONENT = "contains_component"
    LOCATED_IN = "located_in"
    MAINTAINED_BY = "maintained_by"
    INSPECTED_BY = "inspected_by"
    REPLACED_BY = "replaced_by"
    SUPERSEDES = "supersedes"
    DECOMMISSIONED_BY = "decommissioned_by"


class RelationshipState(str, Enum):
    PROPOSED = "proposed"
    ACCEPTED = "accepted"
    SUPERSEDED = "superseded"
    REVOKED = "revoked"
    DISPUTED = "disputed"


class DocumentRef(BaseModel):
    url: str | None = None
    ipfs_cid: str | None = None
    media_type: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    name: str | None = None


class Site(BaseModel):
    building: str | None = None
    storey: str | None = None
    space: str | None = None
    ifc_guid: str | None = None


class AssetSubject(BaseModel):
    """A minimal signed projection with profile-specific extension claims."""

    model_config = ConfigDict(extra="allow")

    name: str = Field(min_length=1)
    manufacturer: str | None = None
    model_number: str | None = None
    serial_number: str | None = None
    asset_owner: str | None = None
    site: Site | None = None
    documents: list[DocumentRef] = Field(default_factory=list)
    linked_clips: list[str] = Field(default_factory=list)
    attributes: dict[str, Any] = Field(default_factory=dict)

    @field_validator("linked_clips")
    @classmethod
    def validate_linked_clips(cls, values: list[str]) -> list[str]:
        return [parse_clip(value).full_id for value in values]


class AvailabilityPolicy(BaseModel):
    minimum_verified_replicas: int = Field(1, ge=1)
    snapshot_every_version: bool = True
    allow_content_networks: bool = True
    visibility: Literal["public", "restricted"] = "public"
    allowed_nodes: list[str] = Field(default_factory=list)


class Proof(BaseModel):
    type: Literal["ClipJcsEd25519Signature2026"] = PROOF_TYPE
    verification_method: str = Field(min_length=1)
    created: datetime
    proof_purpose: str = "assertionMethod"
    proof_value: str = Field(min_length=16)


class TargetIntegrity(BaseModel):
    mode: Literal["latest", "minimum_version", "snapshot"] = "latest"
    version: int | None = Field(None, ge=1)
    sha256: str | None = Field(None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def validate_constraint(self) -> "TargetIntegrity":
        if self.mode == "minimum_version" and self.version is None:
            raise ValueError("minimum_version integrity requires version")
        if self.mode == "snapshot" and (self.version is None or self.sha256 is None):
            raise ValueError("snapshot integrity requires version and sha256")
        return self


class AssetRelationship(BaseModel):
    relationship_id: str
    source: str
    target: str
    role: RelationshipRole
    relation_type: RelationshipType
    asserted_by: str
    accepted_by: str | None = None
    asserted_at: datetime
    effective_from: datetime | None = None
    effective_to: datetime | None = None
    state: RelationshipState = RelationshipState.PROPOSED
    target_integrity: TargetIntegrity = Field(
        default_factory=lambda: TargetIntegrity(mode="latest", version=None, sha256=None)
    )
    claims: dict[str, Any] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    evidence: list[DocumentRef] = Field(default_factory=list)
    proofs: list[Proof] = Field(min_length=1)

    @field_validator("source", "target")
    @classmethod
    def validate_clip(cls, value: str) -> str:
        if not is_valid_clip(value):
            raise ValueError(f"Invalid CLIP URI: {value!r}")
        return value

    @field_validator("references")
    @classmethod
    def validate_references(cls, values: list[str]) -> list[str]:
        return [parse_clip(value).full_id for value in values]


class RelationshipProposalRequest(BaseModel):
    source: str
    target: str
    role: RelationshipRole
    relation_type: RelationshipType
    target_integrity: TargetIntegrity = Field(
        default_factory=lambda: TargetIntegrity(mode="latest", version=None, sha256=None)
    )
    claims: dict[str, Any] = Field(default_factory=dict)
    references: list[str] = Field(default_factory=list)
    evidence: list[DocumentRef] = Field(default_factory=list)
    effective_from: datetime | None = None
    effective_to: datetime | None = None

    @field_validator("source", "target")
    @classmethod
    def validate_clip(cls, value: str) -> str:
        return parse_clip(value).full_id

    @field_validator("references")
    @classmethod
    def validate_references(cls, values: list[str]) -> list[str]:
        return [parse_clip(value).full_id for value in values]


class RelationshipTransitionRequest(BaseModel):
    new_state: RelationshipState
    reason: str = Field(min_length=1, max_length=2000)
    evidence: list[DocumentRef] = Field(default_factory=list)


class AssetRecord(BaseModel):
    id: str
    authority: str
    controller: str
    schema_version: Literal["3.0"] = SCHEMA_VERSION
    record_kind: RecordKind
    subject: AssetSubject
    relationships: list[AssetRelationship] = Field(default_factory=list)
    availability: AvailabilityPolicy = Field(
        default_factory=lambda: AvailabilityPolicy(
            minimum_verified_replicas=1,
            snapshot_every_version=True,
            allow_content_networks=True,
        )
    )
    created_at: datetime
    updated_at: datetime
    version: int = Field(1, ge=1)
    proof: Proof

    @model_validator(mode="after")
    def validate_identity_and_edges(self) -> "AssetRecord":
        parsed = parse_clip(self.id)
        if parsed.authority_key_fingerprint != self.authority:
            raise ValueError("Record authority does not match its CLIP fingerprint")
        if any(edge.source != self.id for edge in self.relationships):
            raise ValueError("Every relationship source must equal the containing record id")
        return self


class AssetCreateRequest(BaseModel):
    record_kind: RecordKind
    subject: AssetSubject
    controller: str | None = None
    relationships: list[AssetRelationship] = Field(default_factory=list)
    availability: AvailabilityPolicy = Field(
        default_factory=lambda: AvailabilityPolicy(
            minimum_verified_replicas=1,
            snapshot_every_version=True,
            allow_content_networks=True,
        )
    )


class AssetUpdateRequest(BaseModel):
    subject: AssetSubject
    controller: str | None = None
    relationships: list[AssetRelationship] = Field(default_factory=list)
    availability: AvailabilityPolicy = Field(
        default_factory=lambda: AvailabilityPolicy(
            minimum_verified_replicas=1,
            snapshot_every_version=True,
            allow_content_networks=True,
        )
    )


class AssetHistoryEntry(BaseModel):
    record: AssetRecord


class VerificationMethod(BaseModel):
    id: str
    type: Literal["Ed25519VerificationKey2020"] = "Ed25519VerificationKey2020"
    public_key_multibase: str
    public_key_base64: str
    purposes: list[str]
    valid_from: datetime
    valid_until: datetime | None = None
    revoked_at: datetime | None = None


class WellKnownResponse(BaseModel):
    authority: str
    genesis_public_key_multibase: str
    protocol_version: Literal["3.0"] = SCHEMA_VERSION
    endpoints: list[str]
    mirrors: list[str] = Field(default_factory=list)
    verification_methods: list[VerificationMethod]
    sequence: int = Field(1, ge=1)
    expires_at: datetime
    proof: str


class NodeInfo(BaseModel):
    routing_host: str
    authority: str
    protocol_version: Literal["3.0"] = SCHEMA_VERSION
    supported_record_kinds: list[RecordKind] = Field(default_factory=lambda: list(RecordKind))
    role: str
    encrypted_storage_opt_in: bool = False
    encrypted_storage_capacity_bytes: int = Field(0, ge=0)


class ResolveResponse(BaseModel):
    record: AssetRecord
    verified: bool
    source: Literal["local_authoritative", "local_cache", "remote_authoritative"]
    authority_endpoint: str | None = None
    trust_state: Literal["verified_current", "verified_stale"] = "verified_current"
    verified_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResolveGraphRequest(BaseModel):
    root: str
    depth: int = Field(2, ge=0, le=5)
    max_nodes: int = Field(50, ge=1, le=100)
    view: Literal["public", "partner", "confidential"] = "public"

    @field_validator("root")
    @classmethod
    def validate_root(cls, value: str) -> str:
        return parse_clip(value).full_id


class GraphNode(BaseModel):
    status: Literal["verified_current", "verified_stale", "snapshot_verified"]
    record: AssetRecord
    source: str
    verified_at: datetime


class GraphReference(BaseModel):
    source: str
    target: str
    relationship_id: str | None = None
    relation_type: RelationshipType | None = None
    provenance: Literal["relationship_reference", "import_reference"] = "relationship_reference"


class GraphFailure(BaseModel):
    clip: str
    status: Literal[
        "restricted", "not_found", "unavailable", "invalid_signature",
        "authority_mismatch", "unsupported_schema", "revoked",
    ]
    reason_code: str
    last_verified_at: datetime | None = None
    retryable: bool = False


class GraphLimits(BaseModel):
    requested_depth: int
    reached_depth: int
    max_nodes: int
    truncated: bool


class ResolveGraphResponse(BaseModel):
    root: str
    complete: bool
    nodes: dict[str, GraphNode]
    edges: list[AssetRelationship]
    references: list[GraphReference]
    failures: list[GraphFailure]
    limits: GraphLimits
    resolved_at: datetime


class AssetListResponse(BaseModel):
    items: list[AssetRecord]
    total: int
    limit: int
    offset: int


class BulkRelationshipProposalRequest(BaseModel):
    proposals: list[RelationshipProposalRequest] = Field(min_length=1, max_length=500)


class BulkRelationshipProposalResult(BaseModel):
    index: int
    status: Literal["proposed", "failed"]
    relationship: AssetRelationship | None = None
    error: str | None = None


class BulkRelationshipProposalResponse(BaseModel):
    batch_id: str
    total: int
    proposed: int
    failed: int
    results: list[BulkRelationshipProposalResult]


class GossipPeerState(BaseModel):
    node_id: str
    endpoint: str
    public_key: str | None = None
    status: Literal["alive", "suspect", "dead"] = "alive"
    last_seen: datetime | None = None
    generation: int = 0


class GossipDigest(BaseModel):
    from_node: str
    peers: list[GossipPeerState] = Field(default_factory=list)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class GossipSyncRequest(BaseModel):
    digest: GossipDigest


class GossipSyncResponse(BaseModel):
    digest: GossipDigest