"""
SWIM-inspired gossip protocol for CLIP node membership.

Design
------
Each node maintains a local membership table (GossipPeer rows in SQLite).
Every GOSSIP_INTERVAL seconds the node:

  1. Picks a random *alive* peer from its table.
  2. POSTs its full digest (all known peers + their states) to
    POST {peer}/v3/gossip/sync
  3. Receives the peer's digest in response.
  4. Merges: for each peer in the received digest, if the remote generation
     is higher than what we know, update our local state.

Health tracking (SWIM-lite)
  - A peer's `generation` is incremented by that peer on each outbound gossip.
  - If we haven't heard from a peer for GOSSIP_SUSPECT_TIMEOUT seconds, mark it
    'suspect' and propagate that state.
  - After GOSSIP_DEAD_TIMEOUT more seconds, mark it 'dead'.
  - Dead peers are retained (not deleted) so we can distinguish "never heard of"
    from "heard of but lost contact".

Bootstrap
  - GOSSIP_SEEDS is a comma-separated list of peer base URLs.
    - On startup, the node fetches /.well-known/clip/server from each seed and
    inserts them into the membership table with generation=0.

Producer-mode nodes (NODE_ROLE=producer) skip gossip entirely — they have no
peer list and answer requests only for their own records. Gossip runs only on
resolver/full-mode nodes.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone

import httpx

from ..config import settings
from ..core.models import GossipDigest, GossipPeerState
from ..db.database import AsyncSessionLocal
from ..db.orm_models import GossipPeer
from ..federation.resolver import fetch_well_known

logger = logging.getLogger("clip.gossip")

# Module-level heartbeat counter — incremented each gossip round
_generation: int = 0


# ---------------------------------------------------------------------------
# Public API used by the FastAPI lifespan
# ---------------------------------------------------------------------------

async def bootstrap_peers() -> None:
    """
    Seed the membership table from GOSSIP_SEEDS and WELL-KNOWN discovery.
    Called once at startup (after DB init).  Any seeds that are not yet
    reachable are retried in a background task so nodes can start in parallel.
    """
    seeds = [s.strip() for s in settings.GOSSIP_SEEDS.split(",") if s.strip()]
    if not seeds:
        return

    logger.info("[gossip] Bootstrapping from %d seed(s)", len(seeds))
    pending = await _try_bootstrap_seeds(seeds)
    if pending:
        asyncio.create_task(_retry_bootstrap(pending))


async def _try_bootstrap_seeds(seeds: list[str]) -> list[str]:
    """Attempt to bootstrap each seed. Returns list of seeds that failed."""
    pending: list[str] = []
    async with AsyncSessionLocal() as session:
        for seed_url in seeds:
            try:
                authority = seed_url.removeprefix("https://").removeprefix("http://").rstrip("/")
                wk = await fetch_well_known(authority, timeout=5)
                await _upsert_peer(session, GossipPeerState(
                    node_id=wk.authority,
                    endpoint=wk.endpoints[0],
                    public_key=wk.verification_methods[0].public_key_base64,
                    status="alive",
                    last_seen=_now(),
                    generation=0,
                ))
                logger.info("[gossip] Discovered seed peer: %s @ %s", wk.authority, wk.endpoints[0])
            except Exception as exc:
                logger.debug("[gossip] Seed %s not ready: %s", seed_url, exc)
                pending.append(seed_url)
        await session.commit()
    return pending


async def _retry_bootstrap(seeds: list[str]) -> None:
    """Retry unreachable seeds with exponential back-off (max ~60 s)."""
    delays = [3, 5, 10, 15, 30, 60]
    remaining = list(seeds)
    for delay in delays:
        await asyncio.sleep(delay)
        remaining = await _try_bootstrap_seeds(remaining)
        if not remaining:
            logger.info("[gossip] All seeds successfully bootstrapped")
            return
    if remaining:
        logger.warning("[gossip] Could not reach seeds after retries: %s", remaining)


async def gossip_loop() -> None:
    """
    Background coroutine — runs continuously until cancelled.
    Calls one gossip round every GOSSIP_INTERVAL seconds.
    """
    if settings.NODE_ROLE == "producer":
        logger.info("[gossip] NODE_ROLE=producer — gossip disabled")
        return

    logger.info(
        "[gossip] Starting gossip loop (interval=%ds, suspect_timeout=%ds)",
        settings.GOSSIP_INTERVAL,
        settings.GOSSIP_SUSPECT_TIMEOUT,
    )
    while True:
        try:
            await _gossip_round()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.warning("[gossip] Round failed: %s", exc)
        await asyncio.sleep(settings.GOSSIP_INTERVAL)


# ---------------------------------------------------------------------------
# Core gossip round
# ---------------------------------------------------------------------------

async def _gossip_round() -> None:
    global _generation
    _generation += 1

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GossipPeer))).scalars().all()

    peers = [_row_to_state(r) for r in rows]
    alive = [p for p in peers if p.status == "alive" and p.node_id != settings.NODE_DOMAIN]

    if not alive:
        logger.debug("[gossip] No alive peers to gossip with")
        return

    target = random.choice(alive)
    my_digest = await build_digest(generation=_generation)

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.post(
                f"{target.endpoint.rstrip('/')}/v3/gossip/sync",
                json={"digest": my_digest.model_dump(mode="json")},
            )
            if resp.status_code != 200:
                logger.warning("[gossip] Sync to %s returned HTTP %d", target.node_id, resp.status_code)
                await _mark_suspect(target.node_id)
                return

            remote_digest = GossipDigest(**resp.json()["digest"])
            await merge_digest(remote_digest)
            logger.debug("[gossip] Synced with %s (gen=%d)", target.node_id, _generation)

    except Exception as exc:
        logger.warning("[gossip] Could not reach %s: %s", target.node_id, exc)
        await _mark_suspect(target.node_id)

    # After a successful round, age peers that we haven't heard from
    await _age_peers()


# ---------------------------------------------------------------------------
# Digest building & merging (called by HTTP endpoint too)
# ---------------------------------------------------------------------------

async def build_digest(generation: int | None = None) -> GossipDigest:
    """Build this node's current digest to send to a peer."""
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GossipPeer))).scalars().all()

    peers = [_row_to_state(r) for r in rows]

    # Always include ourselves so peers learn about us
    self_state = GossipPeerState(
        node_id=settings.NODE_DOMAIN,
        endpoint=settings.NODE_API_BASE,
        status="alive",
        last_seen=_now(),
        generation=generation if generation is not None else _generation,
    )
    # Replace stale self entry if present
    peers = [p for p in peers if p.node_id != settings.NODE_DOMAIN]
    peers.insert(0, self_state)

    return GossipDigest(from_node=settings.NODE_DOMAIN, peers=peers)


async def merge_digest(remote: GossipDigest) -> None:
    """
    Merge a received digest into our local membership table.

    Rule: accept a remote peer state only if its generation is strictly
    higher than what we already have, OR if we have no entry for it yet.
    Never downgrade a 'dead' entry back to 'alive' based purely on hearsay
    — the peer must contact us directly.
    """
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        for remote_peer in remote.peers:
            if remote_peer.node_id == settings.NODE_DOMAIN:
                continue  # never update our own entry via gossip

            result = await session.execute(
                select(GossipPeer).where(GossipPeer.node_id == remote_peer.node_id)
            )
            existing = result.scalar_one_or_none()

            if existing is None:
                await _upsert_peer(session, remote_peer)
                logger.info("[gossip] Learned about new peer: %s", remote_peer.node_id)
            elif remote_peer.generation > existing.generation:
                # Only allow alive→suspect/dead upgrades via gossip, not dead→alive
                new_status = remote_peer.status
                if existing.status == "dead" and new_status == "alive":
                    new_status = "dead"  # keep dead until direct contact
                existing.endpoint = remote_peer.endpoint
                existing.public_key = remote_peer.public_key or existing.public_key
                existing.status = new_status
                existing.generation = remote_peer.generation
                existing.last_seen = remote_peer.last_seen or existing.last_seen

        await session.commit()


# ---------------------------------------------------------------------------
# Health management
# ---------------------------------------------------------------------------

async def _mark_suspect(node_id: str) -> None:
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(select(GossipPeer).where(GossipPeer.node_id == node_id))
        peer = result.scalar_one_or_none()
        if peer and peer.status == "alive":
            peer.status = "suspect"
            await session.commit()
            logger.info("[gossip] Marked %s as suspect", node_id)


async def _age_peers() -> None:
    """Promote suspect→dead based on timeouts."""
    now = _now()
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GossipPeer))).scalars().all()
        changed = False
        for peer in rows:
            if peer.last_seen is None:
                continue
            last = peer.last_seen
            if last.tzinfo is None:
                last = last.replace(tzinfo=timezone.utc)
            age = (now - last).total_seconds()
            if peer.status == "alive" and age > settings.GOSSIP_SUSPECT_TIMEOUT:
                peer.status = "suspect"
                logger.info("[gossip] %s aged to suspect (%.0fs since last seen)", peer.node_id, age)
                changed = True
            elif peer.status == "suspect" and age > settings.GOSSIP_DEAD_TIMEOUT:
                peer.status = "dead"
                logger.info("[gossip] %s aged to dead (%.0fs since last seen)", peer.node_id, age)
                changed = True
        if changed:
            await session.commit()


# ---------------------------------------------------------------------------
# Helpers called by the gossip HTTP endpoint
# ---------------------------------------------------------------------------

async def register_direct_contact(node_id: str, endpoint: str, public_key: str | None, generation: int) -> None:
    """
    Called when a peer contacts us directly through the gossip sync endpoint.
    Direct contact always resets status to 'alive' and updates last_seen.
    """
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        result = await session.execute(select(GossipPeer).where(GossipPeer.node_id == node_id))
        existing = result.scalar_one_or_none()
        if existing is None:
            await _upsert_peer(session, GossipPeerState(
                node_id=node_id,
                endpoint=endpoint,
                public_key=public_key,
                status="alive",
                last_seen=_now(),
                generation=generation,
            ))
        else:
            existing.endpoint = endpoint
            if public_key:
                existing.public_key = public_key
            existing.status = "alive"
            existing.generation = max(existing.generation, generation)
            existing.last_seen = _now()
        await session.commit()


async def get_all_peers() -> list[GossipPeerState]:
    async with AsyncSessionLocal() as session:
        from sqlalchemy import select
        rows = (await session.execute(select(GossipPeer))).scalars().all()
    return [_row_to_state(r) for r in rows]


# ---------------------------------------------------------------------------
# ORM helpers
# ---------------------------------------------------------------------------

async def _upsert_peer(session, state: GossipPeerState) -> None:
    from sqlalchemy import select
    result = await session.execute(select(GossipPeer).where(GossipPeer.node_id == state.node_id))
    existing = result.scalar_one_or_none()
    if existing is None:
        session.add(GossipPeer(
            node_id=state.node_id,
            endpoint=state.endpoint,
            public_key=state.public_key,
            status=state.status,
            generation=state.generation,
            last_seen=state.last_seen or _now(),
        ))
    else:
        existing.endpoint = state.endpoint
        existing.public_key = state.public_key or existing.public_key
        existing.status = state.status
        existing.generation = max(existing.generation, state.generation)
        existing.last_seen = state.last_seen or existing.last_seen


def _row_to_state(row: GossipPeer) -> GossipPeerState:
    return GossipPeerState(
        node_id=row.node_id,
        endpoint=row.endpoint,
        public_key=row.public_key,
        status=row.status,  # type: ignore[arg-type]
        last_seen=row.last_seen,
        generation=row.generation,
    )


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)
