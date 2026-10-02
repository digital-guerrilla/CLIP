"""DID-authenticated CLIP peer gossip endpoints."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from ..core.crypto import NodeKeyManager
from ..core.did import (
    DidVerificationError,
    resolve_clip_gossip_endpoint,
    resolve_did_web_document,
    verify_clip_message_proof,
)
from ..core.clip_network import ClipPeerDigest, ClipPeerState
from ..dependencies import get_key_manager, require_api_key
from ..federation import clip_gossip
from ..federation.clip_gossip import ClipGossipError

router = APIRouter(prefix="/clip/v1/network/gossip", tags=["clip-gossip"])


class PeerDigestRequest(BaseModel):
    digest: ClipPeerDigest


@router.post("/sync", response_model=PeerDigestRequest)
async def sync_peer_digest(
    body: PeerDigestRequest,
    key_manager: NodeKeyManager = Depends(get_key_manager),
) -> PeerDigestRequest:
    digest = body.digest
    try:
        did_document = await resolve_did_web_document(digest.from_did)
        await resolve_clip_gossip_endpoint(
            did_document,
            expected_did=digest.from_did,
        )
    except DidVerificationError as error:
        raise HTTPException(status_code=401, detail="Peer DID or gossip service is invalid") from error
    if not verify_clip_message_proof(
        digest.model_dump(mode="json", by_alias=True),
        did_document,
    ):
        raise HTTPException(status_code=401, detail="Peer digest authentication failed")

    try:
        await clip_gossip.accept_signed_peer_digest(digest, did_document)
        response = await clip_gossip.build_signed_peer_digest(key_manager)
    except ClipGossipError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return PeerDigestRequest(digest=response)


@router.get("/peers", response_model=list[ClipPeerState])
async def list_clip_peers(
    _: None = Depends(require_api_key),
) -> list[ClipPeerState]:
    return await clip_gossip.get_clip_peers()