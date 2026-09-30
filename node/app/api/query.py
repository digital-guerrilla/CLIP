"""Authorization-aware installed asset search for backend integrations."""

from fastapi import APIRouter, Depends, Header, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.models import AssetListResponse, AssetRecord, RecordKind
from ..db.database import get_db
from ..db.orm_models import Asset
from ..dependencies import valid_api_key
from .assets import orm_to_record

router = APIRouter(prefix="/v3/records", tags=["query"])


@router.get("/query", response_model=AssetListResponse)
async def query_assets(
    manufacturer: str | None = None,
    model_number: str | None = None,
    serial_number: str | None = None,
    building: str | None = None,
    ifc_guid: str | None = None,
    linked_clip: str | None = None,
    record_kind: RecordKind | None = None,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> AssetListResponse:
    authenticated = valid_api_key(x_api_key)
    rows = await db.execute(select(Asset).order_by(Asset.created_at.desc()))
    records: list[AssetRecord] = []
    for row in rows.scalars():
        record = orm_to_record(row)
        if record.availability.visibility == "restricted" and not authenticated:
            continue
        subject = record.subject
        site = subject.site
        if record_kind and record.record_kind != record_kind:
            continue
        if manufacturer and subject.manufacturer != manufacturer:
            continue
        if model_number and subject.model_number != model_number:
            continue
        if serial_number and subject.serial_number != serial_number:
            continue
        if building and (site is None or site.building != building):
            continue
        if ifc_guid and (site is None or site.ifc_guid != ifc_guid):
            continue
        if linked_clip and linked_clip not in subject.linked_clips:
            continue
        records.append(record)
    return AssetListResponse(
        items=records[offset:offset + limit],
        total=len(records),
        limit=limit,
        offset=offset,
    )