"""DID-addressed CLIP peer discovery contracts, separate from the IFCX asset graph."""

from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .clip_protocol import ClipDataIntegrityProof, ClipProtocolMessage


class ClipPeerDigest(ClipProtocolMessage):
    from_did: str = Field(alias="fromDid", pattern=r"^did:web:")
    generation: int = Field(ge=0)
    known_dids: list[str] = Field(alias="knownDids", max_length=512)
    created: datetime
    proof: ClipDataIntegrityProof

    @field_validator("known_dids")
    @classmethod
    def validate_peer_dids(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("knownDids cannot contain duplicates")
        if any(not value.startswith("did:web:") for value in values):
            raise ValueError("Only did:web peer identifiers are supported")
        return values

    @model_validator(mode="after")
    def require_sender_authentication(self) -> "ClipPeerDigest":
        if self.proof.proof_purpose != "authentication":
            raise ValueError("Peer digests require an authentication proof")
        if not self.proof.verification_method.startswith(f"{self.from_did}#"):
            raise ValueError("Peer digest key must be controlled by fromDid")
        return self


class ClipPeerState(ClipProtocolMessage):
    did: str = Field(pattern=r"^did:web:")
    status: Literal["unknown", "alive", "suspect", "dead"]
    generation: int = Field(ge=0)
    last_seen: datetime | None = Field(default=None, alias="lastSeen")