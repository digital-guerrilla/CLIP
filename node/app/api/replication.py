"""Replication visibility and explicit retry operations."""

import asyncio

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.guid import parse_clip
from ..db.database import get_db
from ..db.orm_models import Asset, ReplicationJob
from ..dependencies import require_api_key
from .assets import _push_to_peers, orm_to_record

router = APIRouter(prefix="/v3/replication", tags=["replication"])


@router.get("/status/{authority}/{record_uuid}")
async def replication_status(
    authority: str,
    record_uuid: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> dict:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    rows = await db.execute(
        select(ReplicationJob).where(ReplicationJob.record_id == record_id).order_by(ReplicationJob.created_at.desc())
    )
    jobs = list(rows.scalars())
    return {
        "record_id": record_id,
        "jobs": [{
            "job_id": job.job_id,
            "endpoint": job.endpoint,
            "status": job.status,
            "attempts": job.attempts,
            "last_error": job.last_error,
            "updated_at": job.updated_at,
        } for job in jobs],
    }


@router.post("/retry/{authority}/{record_uuid}", status_code=202)
async def retry_replication(
    authority: str,
    record_uuid: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> dict:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    row = (await db.execute(select(Asset).where(Asset.id == record_id))).scalar_one_or_none()
    if row is None or not row.is_authoritative:
        raise HTTPException(status_code=404, detail="Authoritative record not found")
    record = orm_to_record(row)
    asyncio.create_task(_push_to_peers(record))
    return {"status": "queued", "record_id": record_id, "version": record.version}