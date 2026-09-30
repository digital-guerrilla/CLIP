"""Gossip protocol HTTP endpoints."""

import logging

from fastapi import APIRouter

from ..core.models import GossipSyncRequest, GossipSyncResponse, GossipPeerState
from ..federation import gossip as gossip_engine

logger = logging.getLogger("clip.gossip.api")

router = APIRouter(prefix="/v3/gossip", tags=["gossip"])


@router.post("/sync", response_model=GossipSyncResponse)
async def gossip_sync(body: GossipSyncRequest) -> GossipSyncResponse:
    """
    Accept a gossip digest from a peer and return our own digest.

    The peer's self-entry is extracted from the digest and registered as a
    direct contact (resetting its status to 'alive' unconditionally).
    """
    remote_digest = body.digest

    # Find the sender's own entry in the digest (node_id == from_node)
    sender_entry: GossipPeerState | None = next(
        (p for p in remote_digest.peers if p.node_id == remote_digest.from_node),
        None,
    )
    if sender_entry:
        await gossip_engine.register_direct_contact(
            node_id=sender_entry.node_id,
            endpoint=sender_entry.endpoint,
            public_key=sender_entry.public_key,
            generation=sender_entry.generation,
        )

    # Merge the rest of the digest (indirect knowledge)
    await gossip_engine.merge_digest(remote_digest)

    # Respond with our own digest
    my_digest = await gossip_engine.build_digest()
    return GossipSyncResponse(digest=my_digest)


@router.get("/peers", response_model=list[GossipPeerState])
async def list_peers() -> list[GossipPeerState]:
    """
    Return this node's current view of the network membership.
    Useful for debugging and monitoring.
    """
    return await gossip_engine.get_all_peers()
