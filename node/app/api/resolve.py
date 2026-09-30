"""Bounded, cycle-safe CLIP v3 graph resolution."""

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.guid import parse_clip
from ..core.models import (
    AssetRecord,
    GraphFailure,
    GraphReference,
    GraphLimits,
    GraphNode,
    ResolveGraphRequest,
    ResolveGraphResponse,
    ResolveResponse,
)
from ..db.database import AsyncSessionLocal
from ..db.orm_models import Asset
from ..dependencies import valid_api_key
from ..federation.resolver import resolve_clip

router = APIRouter(prefix="/v3", tags=["resolution"])


class RestrictedRecordError(Exception):
    pass


async def _resolve_one(clip: str, db: AsyncSession, allow_restricted: bool) -> ResolveResponse:
    parsed = parse_clip(clip)
    row = (await db.execute(select(Asset).where(Asset.id == clip))).scalar_one_or_none()
    if row and AssetRecord.model_validate(row.record_json).availability.visibility == "restricted" and not allow_restricted:
        raise RestrictedRecordError("Record access is restricted")
    if row and row.is_authoritative:
        return ResolveResponse(
            record=AssetRecord.model_validate(row.record_json),
            verified=True,
            source="local_authoritative",
            authority_endpoint=settings.NODE_API_BASE,
        )
    if row and row.cached_at and settings.CACHE_TTL > 0:
        age = datetime.now(timezone.utc) - _utc(row.cached_at)
        if age < timedelta(seconds=settings.CACHE_TTL):
            return ResolveResponse(
                record=AssetRecord.model_validate(row.record_json),
                verified=True,
                source="local_cache",
                verified_at=_utc(row.verified_at or row.cached_at),
            )
    try:
        record, endpoint, raw_payload = await resolve_clip(clip, settings.FEDERATION_TIMEOUT)
    except ValueError:
        if row:
            return ResolveResponse(
                record=AssetRecord.model_validate(row.record_json),
                verified=True,
                source="local_cache",
                trust_state="verified_stale",
                verified_at=_utc(row.verified_at or row.cached_at or row.updated_at),
            )
        raise

    now = datetime.now(timezone.utc)
    document = record.model_dump(mode="json")
    if row:
        if record.version >= row.version:
            row.record_json = document
            row.raw_payload = raw_payload
            row.controller = record.controller
            row.record_kind = record.record_kind.value
            row.updated_at = record.updated_at
            row.version = record.version
            row.cached_at = now
            row.verified_at = now
    else:
        db.add(Asset(
            id=record.id,
            routing_host=parsed.routing_host,
            authority=record.authority,
            controller=record.controller,
            record_kind=record.record_kind.value,
            record_json=document,
            raw_payload=raw_payload,
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
            is_authoritative=False,
            cached_at=now,
            verified_at=now,
        ))
    await db.commit()
    return ResolveResponse(
        record=record,
        verified=True,
        source="remote_authoritative",
        authority_endpoint=endpoint,
        verified_at=now,
    )


@router.post("/resolve-graph", response_model=ResolveGraphResponse)
async def resolve_graph(
    request: ResolveGraphRequest,
    x_api_key: str | None = Header(default=None),
) -> ResolveGraphResponse:
    authenticated = valid_api_key(x_api_key)
    if request.view != "public" and not authenticated:
        raise HTTPException(status_code=401, detail="Partner and confidential views require the local node API key")
    allow_restricted = request.view != "public" and authenticated
    nodes: dict[str, GraphNode] = {}
    edges = []
    references: list[GraphReference] = []
    failures: list[GraphFailure] = []
    frontier = {request.root}
    scheduled = {request.root}
    reached_depth = 0
    truncated = False

    for depth in range(request.depth + 1):
        if not frontier:
            break
        reached_depth = depth
        async def resolve_independently(clip: str) -> ResolveResponse:
            async with AsyncSessionLocal() as session:
                return await _resolve_one(clip, session, allow_restricted)

        outcomes = await asyncio.gather(
            *[resolve_independently(clip) for clip in sorted(frontier)],
            return_exceptions=True,
        )
        next_frontier: set[str] = set()
        for clip, outcome in zip(sorted(frontier), outcomes):
            if not isinstance(outcome, ResolveResponse):
                restricted = isinstance(outcome, RestrictedRecordError)
                failures.append(GraphFailure(
                    clip=clip,
                    status="restricted" if restricted else "unavailable",
                    reason_code=str(outcome),
                    retryable=not restricted,
                ))
                continue
            nodes[clip] = GraphNode(
                status=outcome.trust_state,
                record=outcome.record,
                source=outcome.source,
                verified_at=outcome.verified_at,
            )
            for edge in sorted(outcome.record.relationships, key=lambda item: item.relationship_id):
                edges.append(edge)
                if edge.target in scheduled or depth >= request.depth:
                    pass
                elif len(scheduled) >= request.max_nodes:
                    truncated = True
                else:
                    scheduled.add(edge.target)
                    next_frontier.add(edge.target)
                for target in edge.references:
                    references.append(GraphReference(
                        source=edge.source,
                        target=target,
                        relationship_id=edge.relationship_id,
                        relation_type=edge.relation_type,
                    ))
                    if target in scheduled or depth >= request.depth:
                        continue
                    if len(scheduled) >= request.max_nodes:
                        truncated = True
                        continue
                    scheduled.add(target)
                    next_frontier.add(target)
            for target in outcome.record.subject.linked_clips:
                references.append(GraphReference(
                    source=outcome.record.id,
                    target=target,
                    provenance="import_reference",
                ))
                if target in scheduled or depth >= request.depth:
                    continue
                if len(scheduled) >= request.max_nodes:
                    truncated = True
                    continue
                scheduled.add(target)
                next_frontier.add(target)
        frontier = next_frontier

    if request.root not in nodes:
        if any(failure.clip == request.root and failure.status == "restricted" for failure in failures):
            raise HTTPException(status_code=403, detail="Root record access is restricted")
        reason = failures[0].reason_code if failures else "Root could not be resolved"
        raise HTTPException(status_code=502, detail=reason)
    return ResolveGraphResponse(
        root=request.root,
        complete=not failures and not truncated,
        nodes=dict(sorted(nodes.items())),
        edges=sorted(edges, key=lambda item: item.relationship_id),
        references=sorted(references, key=lambda item: (item.relationship_id, item.target)),
        failures=sorted(failures, key=lambda item: item.clip),
        limits=GraphLimits(
            requested_depth=request.depth,
            reached_depth=reached_depth,
            max_nodes=request.max_nodes,
            truncated=truncated,
        ),
        resolved_at=datetime.now(timezone.utc),
    )


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)