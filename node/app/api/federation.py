"""Verified CLIP v3 record replication endpoint."""

import json
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.guid import parse_clip
from ..core.models import AssetRecord
from ..db.database import get_db
from ..db.orm_models import Asset
from ..federation.resolver import fetch_well_known

router = APIRouter(prefix="/v3/federation", tags=["federation"])


@router.post("/sync", status_code=202)
async def receive_sync(request: Request, db: AsyncSession = Depends(get_db)) -> dict:
    raw_payload = await request.body()
    try:
        record = AssetRecord.model_validate(json.loads(raw_payload))
        parsed = parse_clip(record.id)
        descriptor = await fetch_well_known(parsed.routing_host, settings.FEDERATION_TIMEOUT)
        methods = {item.id: item for item in descriptor.verification_methods}
        method = methods.get(record.proof.verification_method)
        valid = (
            descriptor.authority == parsed.authority_key_fingerprint == record.authority
            and method is not None
            and "record" in method.purposes
            and NodeKeyManager.verify(
                record.model_dump(mode="json"),
                record.proof.proof_value,
                method.public_key_base64,
            )
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid record: {exc}") from exc
    if not valid:
        raise HTTPException(status_code=400, detail="Record proof verification failed")
    if parsed.routing_host.lower() == settings.NODE_DOMAIN.lower():
        raise HTTPException(status_code=400, detail="Cannot replicate a locally authoritative record")
    if (
        record.availability.visibility == "restricted"
        and settings.NODE_DOMAIN.lower() not in {node.lower() for node in record.availability.allowed_nodes}
    ):
        raise HTTPException(status_code=403, detail="This node is not granted access to the restricted record")

    now = datetime.now(timezone.utc)
    document = record.model_dump(mode="json")
    existing = (await db.execute(select(Asset).where(Asset.id == record.id))).scalar_one_or_none()
    if existing and record.version <= existing.version:
        return {"status": "ignored", "reason": "version not newer"}
    if existing:
        existing.record_json = document
        existing.raw_payload = raw_payload.decode()
        existing.controller = record.controller
        existing.record_kind = record.record_kind.value
        existing.updated_at = record.updated_at
        existing.version = record.version
        existing.cached_at = now
        existing.verified_at = now
    else:
        db.add(Asset(
            id=record.id,
            routing_host=parsed.routing_host,
            authority=record.authority,
            controller=record.controller,
            record_kind=record.record_kind.value,
            record_json=document,
            raw_payload=raw_payload.decode(),
            created_at=record.created_at,
            updated_at=record.updated_at,
            version=record.version,
            is_authoritative=False,
            cached_at=now,
            verified_at=now,
        ))
    await db.commit()
    return {"status": "accepted", "digest": record.id, "verified_at": now.isoformat()}