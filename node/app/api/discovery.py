"""Signed authority descriptor for CLIP v3 discovery."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.models import VerificationMethod, WellKnownResponse
from ..dependencies import get_key_manager

router = APIRouter(tags=["discovery"])


@router.get("/.well-known/clip/server", response_model=WellKnownResponse)
async def well_known_server(
    key_manager: NodeKeyManager = Depends(get_key_manager),
) -> WellKnownResponse:
    now = datetime.now(timezone.utc).replace(microsecond=0)
    controller = settings.DID_WEB_ID or f"did:web:{settings.NODE_DOMAIN.replace(':', '%3A')}"
    method = VerificationMethod(
        id=f"{controller}#clip-record-signing",
        public_key_multibase=key_manager.public_key_multibase,
        public_key_base64=key_manager.public_key_b64,
        purposes=["record", "relationship-assertion", "relationship-acceptance"],
        valid_from=now,
    )
    descriptor = WellKnownResponse(
        authority=key_manager.public_key_multibase,
        genesis_public_key_multibase=key_manager.public_key_multibase,
        endpoints=[settings.NODE_API_BASE.rstrip("/")],
        verification_methods=[method],
        sequence=1,
        expires_at=now + timedelta(days=30),
        proof="unsigned",
    )
    document = descriptor.model_dump(mode="json")
    document["proof"] = key_manager.sign_record(document)
    return WellKnownResponse.model_validate(document)