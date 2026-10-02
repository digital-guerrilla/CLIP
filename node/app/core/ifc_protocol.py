"""IFC graph transaction contracts using IFCX serialization."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_serializer, model_validator

from .ifc_graph import IfcComponentAddress
from .ifcx_models import IfcxFile, IfcxModel, IfcxNode
from .clip_protocol import ClipDataIntegrityProof, ClipProtocolMessage


class IfcComponentChange(IfcxModel):
    action: Literal["set", "remove"]
    value: Any = None

    @model_validator(mode="after")
    def validate_value_presence(self) -> "IfcComponentChange":
        has_value = "value" in self.model_fields_set
        if self.action == "set" and not has_value:
            raise ValueError("A set operation requires a component value")
        if self.action == "set" and self.value is None:
            raise ValueError("A set operation cannot use null; use remove instead")
        if self.action == "remove" and has_value:
            raise ValueError("A remove operation cannot include a component value")
        return self

    @model_serializer(mode="wrap")
    def serialize_change(self, handler):
        serialized = handler(self)
        if self.action == "remove":
            serialized.pop("value", None)
        return serialized


class IfcProposalTransaction(ClipProtocolMessage):
    transaction_id: UUID = Field(default_factory=uuid4, alias="transactionId")
    actor_did: str = Field(alias="actorDid", pattern=r"^did:")
    target: IfcComponentAddress
    change: IfcComponentChange
    expected_sequence: int = Field(alias="expectedSequence", ge=0)
    schema_digest: str = Field(alias="schemaDigest", pattern=r"^[0-9a-f]{64}$")
    created: datetime
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_assertion_proof(self) -> "IfcProposalTransaction":
        if self.proof.proof_purpose != "assertionMethod":
            raise ValueError("Proposals require an assertionMethod proof")
        return self


class IfcDatasetAddress(IfcxModel):
    authority_did: str = Field(alias="authorityDid", pattern=r"^did:web:")
    dataset_id: str = Field(alias="datasetId", min_length=1)


class IfcGraphOperation(IfcxModel):
    action: Literal["create", "contribute"]
    node: IfcxNode


class IfcGraphProposalTransaction(ClipProtocolMessage):
    transaction_id: UUID = Field(default_factory=uuid4, alias="transactionId")
    actor_did: str = Field(alias="actorDid", pattern=r"^did:")
    target: IfcDatasetAddress
    operations: list[IfcGraphOperation] = Field(min_length=1, max_length=32)
    expected_sequence: int = Field(alias="expectedSequence", ge=0)
    schema_digest: str = Field(alias="schemaDigest", pattern=r"^[0-9a-f]{64}$")
    created: datetime
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_assertion_proof(self) -> "IfcGraphProposalTransaction":
        if self.proof.proof_purpose != "assertionMethod":
            raise ValueError("Graph proposals require an assertionMethod proof")
        return self


class IfcDecisionTransaction(ClipProtocolMessage):
    transaction_id: UUID = Field(default_factory=uuid4, alias="transactionId")
    actor_did: str = Field(alias="actorDid", pattern=r"^did:")
    decision: Literal["accept", "reject"]
    proposal_id: UUID = Field(alias="proposalId")
    proposal_digest: str = Field(alias="proposalDigest", pattern=r"^[0-9a-f]{64}$")
    expected_sequence: int = Field(alias="expectedSequence", ge=0)
    created: datetime
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_capability_proof(self) -> "IfcDecisionTransaction":
        if self.proof.proof_purpose != "capabilityInvocation":
            raise ValueError("Owner decisions require a capabilityInvocation proof")
        return self


class IfcAuthorityReceipt(ClipProtocolMessage):
    receipt_id: UUID = Field(default_factory=uuid4, alias="receiptId")
    authority_did: str = Field(alias="authorityDid", pattern=r"^did:")
    transaction_id: UUID = Field(alias="transactionId")
    accepted: bool
    sequence: int = Field(ge=0)
    transaction_digest: str = Field(alias="transactionDigest", pattern=r"^[0-9a-f]{64}$")
    created: datetime
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_assertion_proof(self) -> "IfcAuthorityReceipt":
        if self.proof.proof_purpose != "assertionMethod":
            raise ValueError("Authority receipts require an assertionMethod proof")
        return self


class IfcPublicationEnvelope(ClipProtocolMessage):
    publisher_did: str = Field(alias="publisherDid", pattern=r"^did:")
    file: IfcxFile
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_publisher_assertion(self) -> "IfcPublicationEnvelope":
        if self.proof.proof_purpose != "assertionMethod":
            raise ValueError("Dataset publications require an assertionMethod proof")
        if not self.proof.verification_method.startswith(f"{self.publisher_did}#"):
            raise ValueError("Publication proof key must be controlled by its publisher DID")
        return self

