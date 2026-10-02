"""CLIP transport authentication and evidence contracts."""

from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .ifc_graph import IfcComponentAddress
from .ifcx_models import IfcxModel


def _protocol_context() -> list[Any]:
    return [
        "https://w3id.org/security/data-integrity/v2",
        {"@vocab": "urn:clip:protocol:"},
    ]


class ClipProtocolMessage(IfcxModel):
    context: list[Any] = Field(default_factory=_protocol_context, alias="@context")


class ClipDataIntegrityProof(IfcxModel):
    context: Any | None = Field(default=None, alias="@context")
    type: Literal["DataIntegrityProof"]
    cryptosuite: Literal["eddsa-jcs-2022"]
    created: datetime
    verification_method: str = Field(alias="verificationMethod", min_length=1)
    proof_purpose: Literal["assertionMethod", "capabilityInvocation", "authentication"] = Field(alias="proofPurpose")
    proof_value: str = Field(alias="proofValue", pattern=r"^z[1-9A-HJ-NP-Za-km-z]+$")


class ClipServiceMessage(ClipProtocolMessage):
    message_id: UUID = Field(default_factory=uuid4, alias="messageId")
    actor_did: str = Field(alias="actorDid", pattern=r"^did:web:")
    audience_did: str = Field(alias="audienceDid", pattern=r"^did:web:")
    kind: Literal["replication", "replicationAcknowledgement", "fragment", "fragmentReceipt", "fragmentRead", "projectRead", "projectView", "projectJoin", "projectJoinAcknowledgement", "projectJoinDecision"]
    payload: dict[str, Any]
    created: datetime
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_service_authentication(self) -> "ClipServiceMessage":
        if self.proof.proof_purpose != "authentication":
            raise ValueError("Service messages require an authentication proof")
        return self


class ClipEvidenceManifest(ClipProtocolMessage):
    evidence_id: str = Field(alias="evidenceId", pattern=r"^[0-9a-f]{64}$")
    publisher_did: str = Field(alias="publisherDid", pattern=r"^did:web:")
    target: IfcComponentAddress
    manifest: dict[str, Any]
    proof: ClipDataIntegrityProof

    @model_validator(mode="after")
    def require_evidence_assertion(self) -> "ClipEvidenceManifest":
        if self.proof.proof_purpose != "assertionMethod" or self.target.authority_did != self.publisher_did:
            raise ValueError("Evidence requires its authority's assertion proof")
        return self
