"""Authoritative CLIP v3 record publication and history endpoints."""

import asyncio
import json
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.guid import generate_clip, parse_clip
from ..core.models import (
    AssetCreateRequest,
    AssetHistoryEntry,
    AssetListResponse,
    AssetRecord,
    AssetRelationship,
    AssetSubject,
    AssetUpdateRequest,
    AvailabilityPolicy,
    Proof,
)
from ..db.database import AsyncSessionLocal, get_db
from ..db.orm_models import Asset, AssetHistory, ReplicationJob
from ..dependencies import get_key_manager, require_api_key, valid_api_key

router = APIRouter(prefix="/v3/records", tags=["records"])


def orm_to_record(asset: Asset) -> AssetRecord:
    return AssetRecord.model_validate(asset.record_json)


def record_to_storage(record: AssetRecord) -> tuple[dict, str]:
    document = record.model_dump(mode="json")
    return document, json.dumps(document, separators=(",", ":"), ensure_ascii=True)


def _controller() -> str:
    return settings.DID_WEB_ID or f"did:web:{settings.NODE_DOMAIN.replace(':', '%3A')}"


def _signed_record(
    *,
    record_id: str,
    record_kind: str,
    subject: AssetSubject,
    relationships: list[AssetRelationship],
    availability: AvailabilityPolicy,
    controller: str,
    created_at: datetime,
    updated_at: datetime,
    version: int,
    key_manager: NodeKeyManager,
) -> AssetRecord:
    proof_created = updated_at
    document = {
        "id": record_id,
        "authority": key_manager.public_key_multibase,
        "controller": controller,
        "schema_version": "3.0",
        "record_kind": record_kind,
        "subject": subject.model_dump(mode="json"),
        "relationships": [item.model_dump(mode="json") for item in relationships],
        "availability": availability.model_dump(mode="json"),
        "created_at": created_at.isoformat(),
        "updated_at": updated_at.isoformat(),
        "version": version,
        "proof": Proof(
            verification_method=f"{controller}#clip-record-signing",
            created=proof_created,
            proof_value="unsigned-placeholder",
        ).model_dump(mode="json"),
    }
    normalized = AssetRecord.model_validate(document)
    signature = key_manager.sign_record(normalized.model_dump(mode="json"))
    document = normalized.model_dump(mode="json")
    document["proof"]["proof_value"] = signature
    return AssetRecord.model_validate(document)


@router.get("", response_model=AssetListResponse)
async def list_assets(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    scope: str = Query("authority", pattern="^(authority|network)$"),
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AssetListResponse:
    rows = await db.execute(select(Asset).order_by(Asset.created_at.desc()))
    authenticated = valid_api_key(x_api_key)
    visible = [
        row for row in rows.scalars()
        if (scope == "network" or row.is_authoritative)
        and (orm_to_record(row).availability.visibility == "public" or authenticated)
    ]
    return AssetListResponse(
        items=[orm_to_record(row) for row in visible[offset:offset + limit]],
        total=len(visible),
        limit=limit,
        offset=offset,
    )


@router.get("/{authority}/{record_uuid}", response_model=AssetRecord)
async def get_asset(
    authority: str,
    record_uuid: str,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AssetRecord:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    row = (await db.execute(select(Asset).where(Asset.id == record_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record not found: {record_id}")
    record = orm_to_record(row)
    if record.availability.visibility == "restricted" and not valid_api_key(x_api_key):
        raise HTTPException(status_code=403, detail="Record access is restricted")
    return record


@router.post("", response_model=AssetRecord, status_code=201)
async def create_asset(
    body: AssetCreateRequest,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> AssetRecord:
    record_id = generate_clip(settings.NODE_DOMAIN, key_manager.public_key_multibase)
    if body.relationships:
        raise HTTPException(
            status_code=422,
            detail="Create the record first, then add relationships with PUT so their source can equal its CLIP",
        )
    now = datetime.now(timezone.utc)
    record = _signed_record(
        record_id=record_id,
        record_kind=body.record_kind.value,
        subject=body.subject,
        relationships=[],
        availability=body.availability,
        controller=body.controller or _controller(),
        created_at=now,
        updated_at=now,
        version=1,
        key_manager=key_manager,
    )
    document, raw_payload = record_to_storage(record)
    parsed = parse_clip(record.id)
    db.add(Asset(
        id=record.id,
        routing_host=parsed.routing_host,
        authority=record.authority,
        controller=record.controller,
        record_kind=record.record_kind.value,
        record_json=document,
        raw_payload=raw_payload,
        created_at=now,
        updated_at=now,
        version=1,
        is_authoritative=True,
        verified_at=now,
    ))
    await db.commit()
    asyncio.create_task(_push_to_peers(record))
    return record


@router.put("/{authority}/{record_uuid}", response_model=AssetRecord)
async def update_asset(
    authority: str,
    record_uuid: str,
    body: AssetUpdateRequest,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> AssetRecord:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    row = (await db.execute(select(Asset).where(Asset.id == record_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record not found: {record_id}")
    if not row.is_authoritative or authority != key_manager.public_key_multibase:
        raise HTTPException(status_code=403, detail="This node is not authoritative for the record")

    previous = orm_to_record(row)
    if body.relationships != previous.relationships:
        raise HTTPException(
            status_code=422,
            detail="Relationships can only be changed through the signed proposal workflow",
        )
    return await replace_asset_subject(
        row=row,
        subject=body.subject,
        controller=body.controller or previous.controller,
        availability=body.availability,
        db=db,
        key_manager=key_manager,
    )


async def replace_asset_subject(
    *,
    row: Asset,
    subject: AssetSubject,
    controller: str,
    availability: AvailabilityPolicy | None = None,
    db: AsyncSession,
    key_manager: NodeKeyManager,
) -> AssetRecord:
    """Create, persist, and federate the next signed version of an authoritative record."""
    previous = orm_to_record(row)
    db.add(AssetHistory(
        asset_id=row.id,
        version=row.version,
        record_json=row.record_json,
        raw_payload=row.raw_payload,
        updated_at=row.updated_at,
    ))
    now = datetime.now(timezone.utc)
    record = _signed_record(
        record_id=row.id,
        record_kind=previous.record_kind.value,
        subject=subject,
        relationships=previous.relationships,
        availability=availability or previous.availability,
        controller=controller,
        created_at=_utc(row.created_at),
        updated_at=now,
        version=row.version + 1,
        key_manager=key_manager,
    )
    row.record_json, row.raw_payload = record_to_storage(record)
    row.controller = record.controller
    row.updated_at = now
    row.version = record.version
    row.verified_at = now
    await db.commit()
    asyncio.create_task(_push_to_peers(record))
    return record


@router.get("/{authority}/{record_uuid}/history", response_model=list[AssetHistoryEntry])
async def get_asset_history(
    authority: str,
    record_uuid: str,
    db: AsyncSession = Depends(get_db),
) -> list[AssetHistoryEntry]:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    rows = await db.execute(
        select(AssetHistory)
        .where(AssetHistory.asset_id == record_id)
        .order_by(AssetHistory.version.asc())
    )
    return [AssetHistoryEntry(record=AssetRecord.model_validate(row.record_json)) for row in rows.scalars()]


async def _push_to_peers(record: AssetRecord) -> None:
    try:
        import httpx
        from ..federation.gossip import get_all_peers

        peers = [peer for peer in await get_all_peers() if peer.status == "alive"]
        endpoints = replication_endpoints(record.availability, peers)
        async with httpx.AsyncClient(timeout=5) as client:
            for endpoint in endpoints:
                job_id = str(uuid.uuid4())
                now = datetime.now(timezone.utc)
                async with AsyncSessionLocal() as session:
                    session.add(ReplicationJob(
                        job_id=job_id,
                        record_id=record.id,
                        endpoint=endpoint,
                        status="pending",
                        created_at=now,
                        updated_at=now,
                    ))
                    await session.commit()
                try:
                    response = await client.post(
                        f"{endpoint}/v3/federation/sync",
                        json=record.model_dump(mode="json"),
                    )
                    response.raise_for_status()
                    status = "succeeded"
                    error = None
                except Exception as exc:
                    status = "failed"
                    error = str(exc)
                async with AsyncSessionLocal() as session:
                    job = await session.get(ReplicationJob, job_id)
                    if job is not None:
                        job.status = status
                        job.attempts = 1
                        job.last_error = error
                        job.updated_at = datetime.now(timezone.utc)
                        await session.commit()
    except Exception:
        return


def replication_endpoints(availability: AvailabilityPolicy, peers: list) -> list[str]:
    if availability.visibility == "public":
        return sorted({peer.endpoint.rstrip("/") for peer in peers})

    from ..federation.resolver import _discovery_scheme

    allowed = {node.lower() for node in availability.allowed_nodes}
    endpoints = {
        peer.endpoint.rstrip("/")
        for peer in peers
        if (urlsplit(peer.endpoint).netloc or peer.node_id).lower() in allowed
    }
    endpoints.update(
        f"{_discovery_scheme(node)}://{node}"
        for node in availability.allowed_nodes
        if node.lower() != settings.NODE_DOMAIN.lower()
    )
    return sorted(endpoints)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)