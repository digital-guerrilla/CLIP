"""SQLAlchemy persistence for IFC graph and CLIP infrastructure state."""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint, LargeBinary
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class SupplyChainRecord(Base):
    __tablename__ = "clip_supply_chain_records"
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    authority_did: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)
    project_id: Mapped[str | None] = mapped_column(String, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    record_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainRevision(Base):
    __tablename__ = "clip_supply_chain_revisions"
    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    record_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, primary_key=True)
    public: Mapped[bool] = mapped_column(Boolean, nullable=False)
    snapshot_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainDocument(Base):
    __tablename__ = "clip_supply_chain_documents"
    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    record_id: Mapped[str] = mapped_column(String, nullable=False)
    metadata_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)


class SupplyChainDocumentGrant(Base):
    __tablename__ = "clip_supply_chain_document_grants"
    document_id: Mapped[str] = mapped_column(String, primary_key=True)
    recipient_did: Mapped[str] = mapped_column(String, primary_key=True)
    grant_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainSubmission(Base):
    __tablename__ = "clip_supply_chain_submissions"
    submission_id: Mapped[str] = mapped_column(String, primary_key=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    submission_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainOperation(Base):
    __tablename__ = "clip_supply_chain_operations"
    operation_key: Mapped[str] = mapped_column(String, primary_key=True)
    request_digest: Mapped[str] = mapped_column(String, nullable=False)
    response_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainProject(Base):
    __tablename__ = "clip_supply_chain_projects"
    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    project_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class SupplyChainSenderPolicy(Base):
    __tablename__ = "clip_supply_chain_sender_policies"
    project_id: Mapped[str] = mapped_column(String, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    senders: Mapped[list] = mapped_column(JSON, nullable=False)


class IfcAuthoritySequence(Base):
    __tablename__ = "ifc_authority_sequences"

    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IfcProposalRecord(Base):
    __tablename__ = "ifc_proposals"

    proposal_id: Mapped[str] = mapped_column(String, primary_key=True)
    authority_did: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    proposer_did: Mapped[str] = mapped_column(String, nullable=False)
    proposal_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    proposal_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    decision_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    decision_receipt_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IfcDatasetRecord(Base):
    __tablename__ = "ifc_datasets"

    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    schema_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    file_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IfcDatasetTrustPolicy(Base):
    __tablename__ = "ifc_dataset_trust_policies"

    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    trusted_proposers: Mapped[list[str]] = mapped_column(JSON, nullable=False, default=list)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipProjectPolicy(Base):
    __tablename__ = "clip_project_policies"

    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    visibility: Mapped[str] = mapped_column(String(16), nullable=False)
    members: Mapped[dict[str, str]] = mapped_column(JSON, nullable=False, default=dict)
    revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipProjectInvite(Base):
    __tablename__ = "clip_project_invites"
    __table_args__ = (UniqueConstraint("token_digest"),)

    invite_id: Mapped[str] = mapped_column(String, primary_key=True)
    authority_did: Mapped[str] = mapped_column(String, nullable=False)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    token_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipProjectJoinRequest(Base):
    __tablename__ = "clip_project_join_requests"
    __table_args__ = (UniqueConstraint("invite_id"),)

    request_id: Mapped[str] = mapped_column(String, primary_key=True)
    invite_id: Mapped[str] = mapped_column(String, ForeignKey("clip_project_invites.invite_id"), nullable=False)
    authority_did: Mapped[str] = mapped_column(String, nullable=False)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False)
    actor_did: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    request_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    decision_json: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipPeerRecord(Base):
    __tablename__ = "clip_peers"

    peer_did: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="unknown")
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    discovered_from: Mapped[str | None] = mapped_column(String, nullable=True)


class ClipGossipGeneration(Base):
    __tablename__ = "clip_gossip_generations"

    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class IfcAcceptedTransaction(Base):
    __tablename__ = "ifc_accepted_transactions"
    __table_args__ = (
        UniqueConstraint("authority_did", "sequence"),
    )

    transaction_id: Mapped[str] = mapped_column(String, primary_key=True)
    authority_did: Mapped[str] = mapped_column(String, nullable=False, index=True)
    dataset_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    transaction_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IfcReceiptRecord(Base):
    __tablename__ = "ifc_receipts"
    __table_args__ = (
        UniqueConstraint("authority_did", "sequence"),
    )

    receipt_id: Mapped[str] = mapped_column(String, primary_key=True)
    transaction_id: Mapped[str] = mapped_column(
        String,
        ForeignKey("ifc_accepted_transactions.transaction_id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    authority_did: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    transaction_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    receipt_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipReplicaBundle(Base):
    __tablename__ = "clip_replica_bundles"
    authority_did: Mapped[str] = mapped_column(String, primary_key=True)
    dataset_id: Mapped[str] = mapped_column(String, primary_key=True)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    digest: Mapped[str] = mapped_column(String(64), nullable=False)
    bundle_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipReplicationAck(Base):
    __tablename__ = "clip_replication_acks"
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    peer_did: Mapped[str] = mapped_column(String, primary_key=True)
    acknowledgement_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ClipEvidenceRecord(Base):
    __tablename__ = "clip_evidence"
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    publisher_did: Mapped[str] = mapped_column(String, nullable=False)
    manifest_json: Mapped[dict] = mapped_column(JSON, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ClipFragmentReceipt(Base):
    __tablename__ = "clip_fragment_receipts"
    evidence_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    fragment_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    peer_did: Mapped[str] = mapped_column(String, primary_key=True)
    receipt_json: Mapped[dict] = mapped_column(JSON, nullable=False)


class ClipImportCache(Base):
    __tablename__ = "clip_import_cache"
    uri: Mapped[str] = mapped_column(String, primary_key=True)
    integrity: Mapped[str] = mapped_column(String, primary_key=True)
    content: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)