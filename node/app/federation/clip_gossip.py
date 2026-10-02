"""DID-authenticated gossip membership, kept separate from IFCX asset data."""

import asyncio
from datetime import datetime, timezone
import logging
import random

import httpx
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from ..config import settings
from ..core.egress import request_json
from ..core.crypto import NodeKeyManager
from ..core.data_integrity import add_data_integrity_proof
from ..core.did import (
    DidVerificationError,
    resolve_clip_gossip_endpoint,
    resolve_did_web_document,
    verify_clip_message_proof,
)
from ..core.clip_network import ClipPeerDigest, ClipPeerState
from ..db.database import AsyncSessionLocal
from ..db.orm_models import ClipGossipGeneration, ClipPeerRecord

logger = logging.getLogger("clip.clip_gossip")


class ClipGossipError(ValueError):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _local_peer_identity() -> tuple[str, str]:
    did = settings.DID_WEB_ID
    verification_method = settings.DID_VERIFICATION_METHOD
    if not did.startswith("did:web:") or not verification_method.startswith(f"{did}#"):
        raise ClipGossipError("CLIP gossip requires a local did:web id and verification method")
    return did, verification_method


async def _next_generation(did: str) -> int:
    async with AsyncSessionLocal() as session:
        try:
            async with session.begin_nested():
                session.add(ClipGossipGeneration(authority_did=did, generation=0))
                await session.flush()
        except IntegrityError:
            pass
        generation = (await session.execute(
            update(ClipGossipGeneration)
            .where(ClipGossipGeneration.authority_did == did)
            .values(generation=ClipGossipGeneration.generation + 1)
            .returning(ClipGossipGeneration.generation)
        )).scalar_one()
        await session.commit()
        return generation


async def build_signed_peer_digest(key_manager: NodeKeyManager) -> ClipPeerDigest:
    local_did, verification_method = _local_peer_identity()
    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(select(ClipPeerRecord).order_by(ClipPeerRecord.peer_did))
        ).scalars().all()
    known_dids = [row.peer_did for row in rows if row.peer_did != local_did]
    unsigned = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "fromDid": local_did,
        "generation": await _next_generation(local_did),
        "knownDids": known_dids,
        "created": _now().isoformat().replace("+00:00", "Z"),
    }
    signed = add_data_integrity_proof(
        unsigned,
        key_manager.private_key_bytes,
        verification_method=verification_method,
        proof_purpose="authentication",
        created=_now(),
    )
    return ClipPeerDigest.model_validate(signed)


async def _upsert_unknown_peer(peer_did: str, discovered_from: str | None = None) -> None:
    local_did, _ = _local_peer_identity()
    if peer_did == local_did:
        return
    async with AsyncSessionLocal() as session:
        peer = await session.get(ClipPeerRecord, peer_did)
        if peer is None:
            session.add(ClipPeerRecord(
                peer_did=peer_did,
                status="unknown",
                generation=0,
                last_seen=_now(),
                discovered_from=discovered_from,
            ))
            await session.commit()


async def bootstrap_did_peers() -> None:
    seeds = [value.strip() for value in settings.CLIP_GOSSIP_SEEDS.split(",") if value.strip()]
    if not seeds:
        return
    pending = await _try_bootstrap_dids(seeds)
    if pending:
        asyncio.create_task(_retry_did_bootstrap(pending))


async def _try_bootstrap_dids(seeds: list[str]) -> list[str]:
    pending: list[str] = []
    for peer_did in seeds:
        if not peer_did.startswith("did:web:"):
            logger.warning("[clip-gossip] Ignoring non-did:web seed")
            continue
        try:
            document = await resolve_did_web_document(peer_did)
            await resolve_clip_gossip_endpoint(document, expected_did=peer_did)
            await _upsert_unknown_peer(peer_did, discovered_from="seed")
        except Exception as error:
            logger.warning("[clip-gossip] Could not bootstrap DID seed %s: %s", peer_did, error)
            pending.append(peer_did)
    return pending


async def _retry_did_bootstrap(seeds: list[str]) -> None:
    remaining = list(seeds)
    for delay in (3, 5, 10, 20, 30):
        await asyncio.sleep(delay)
        remaining = await _try_bootstrap_dids(remaining)
        if not remaining:
            return
    if remaining:
        logger.warning("[clip-gossip] Could not resolve seed DID(s): %s", remaining)


async def accept_signed_peer_digest(
    digest: ClipPeerDigest,
    did_document: dict,
) -> None:
    local_did, _ = _local_peer_identity()
    digest_document = digest.model_dump(mode="json", by_alias=True)
    if digest.from_did == local_did:
        raise ClipGossipError("Refusing a gossip digest from this node's own DID")
    if not verify_clip_message_proof(digest_document, did_document):
        raise ClipGossipError("Peer digest authentication failed")

    await _upsert_direct_peer(digest.from_did, digest.generation)
    for peer_did in digest.known_dids:
        await _upsert_unknown_peer(peer_did, discovered_from=digest.from_did)


async def _upsert_direct_peer(peer_did: str, generation: int) -> None:
    local_did, _ = _local_peer_identity()
    if peer_did == local_did:
        return
    async with AsyncSessionLocal() as session:
        peer = await session.get(ClipPeerRecord, peer_did)
        if peer is not None and generation <= peer.generation:
            raise ClipGossipError("Peer digest generation is stale or replayed")
        if peer is None:
            session.add(ClipPeerRecord(
                peer_did=peer_did,
                status="alive",
                generation=generation,
                last_seen=_now(),
            ))
        else:
            peer.status = "alive"
            peer.generation = max(peer.generation, generation)
            peer.last_seen = _now()
        await session.commit()


async def get_clip_peers() -> list[ClipPeerState]:
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ClipPeerRecord))).scalars().all()
    return [
        ClipPeerState(
            did=row.peer_did,
            status=row.status,
            generation=row.generation,
            lastSeen=row.last_seen,
        )
        for row in rows
    ]


async def _mark_suspect(peer_did: str) -> None:
    async with AsyncSessionLocal() as session:
        peer = await session.get(ClipPeerRecord, peer_did)
        if peer is not None and peer.status in {"alive", "unknown"}:
            peer.status = "suspect"
            await session.commit()


async def _age_peers() -> None:
    now = _now()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ClipPeerRecord))).scalars().all()
        changed = False
        for peer in rows:
            if peer.last_seen is None:
                continue
            last_seen = peer.last_seen
            if last_seen.tzinfo is None:
                last_seen = last_seen.replace(tzinfo=timezone.utc)
            age = (now - last_seen).total_seconds()
            if peer.status == "alive" and age > settings.CLIP_GOSSIP_SUSPECT_TIMEOUT:
                peer.status = "suspect"
                changed = True
            elif peer.status in {"unknown", "suspect"} and age > settings.CLIP_GOSSIP_DEAD_TIMEOUT:
                peer.status = "dead"
                changed = True
        if changed:
            await session.commit()


async def gossip_round(key_manager: NodeKeyManager) -> None:
    local_did, _ = _local_peer_identity()
    async with AsyncSessionLocal() as session:
        rows = (await session.execute(select(ClipPeerRecord))).scalars().all()
    candidates = [
        row for row in rows
        if row.peer_did != local_did
    ]
    if not candidates:
        return

    target = random.choice(candidates)
    try:
        document = await resolve_did_web_document(target.peer_did)
        endpoint = await resolve_clip_gossip_endpoint(
            document,
            expected_did=target.peer_did,
        )
        request_digest = await build_signed_peer_digest(key_manager)
        response = await request_json("POST", endpoint, body={"digest": request_digest.model_dump(mode="json", by_alias=True)})
        remote_digest = ClipPeerDigest.model_validate(response["digest"])
        if remote_digest.from_did != target.peer_did:
            raise ClipGossipError("Gossip response DID does not match the contacted peer")
        await accept_signed_peer_digest(remote_digest, document)
    except Exception as error:
        logger.warning("[clip-gossip] Sync with %s failed: %s", target.peer_did, error)
        await _mark_suspect(target.peer_did)
    await _age_peers()


async def clip_gossip_loop(key_manager: NodeKeyManager) -> None:
    while True:
        try:
            await gossip_round(key_manager)
        except asyncio.CancelledError:
            raise
        except Exception as error:
            logger.warning("[clip-gossip] Round failed: %s", error)
        await asyncio.sleep(settings.CLIP_GOSSIP_INTERVAL)