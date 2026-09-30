"""Owner-side bulk import of COBie/CSV/JSON asset records."""

import asyncio
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.guid import generate_clip, parse_clip
from ..core.models import AssetCreateRequest, AssetRecord
from ..db.database import get_db
from ..db.orm_models import Asset, ImportJob
from ..dependencies import get_key_manager, require_api_key
from ..imports.cobie import map_row, parse_rows
from .assets import _controller, _push_to_peers, _signed_record, record_to_storage, orm_to_record

router = APIRouter(prefix="/v3/imports", tags=["imports"])


class AssetImportRequest(BaseModel):
    source_format: str = "csv"
    content: str = Field(min_length=1)
    field_mapping: dict[str, str] = Field(default_factory=dict)
    dry_run: bool = False
    deduplicate: bool = True
    max_rows: int = Field(1000, ge=1, le=10000)


class ImportRowResult(BaseModel):
    row: int
    status: str
    clip: str | None = None
    linked_clips: list[str] = Field(default_factory=list)
    error: str | None = None


class AssetImportResponse(BaseModel):
    import_id: str
    source_format: str
    total: int
    created: int
    existing: int
    failed: int
    dry_run: bool
    rows: list[ImportRowResult]


class ImportStatusResponse(BaseModel):
    import_id: str
    status: str
    result: AssetImportResponse | None = None


def _dedupe_key(request: AssetCreateRequest) -> tuple[str | None, ...]:
    subject = request.subject
    return (
        subject.manufacturer,
        subject.model_number,
        subject.serial_number,
        subject.site.ifc_guid if subject.site else None,
    )


@router.post("/assets", response_model=AssetImportResponse, status_code=200)
async def import_assets(
    body: AssetImportRequest,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
    x_idempotency_key: str | None = Header(default=None),
) -> AssetImportResponse:
    if x_idempotency_key:
        previous = (await db.execute(
            select(ImportJob).where(ImportJob.idempotency_key == x_idempotency_key)
        )).scalar_one_or_none()
        if previous is not None:
            if previous.result_json is None:
                raise HTTPException(status_code=409, detail="Import with this idempotency key is still running")
            return AssetImportResponse.model_validate(previous.result_json)
    import_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    job = ImportJob(
        import_id=import_id,
        idempotency_key=x_idempotency_key,
        status="running",
        created_at=now,
        updated_at=now,
    )
    db.add(job)
    await db.flush()
    try:
        rows = parse_rows(body.content, body.source_format.lower())
    except (ValueError, TypeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    if len(rows) > body.max_rows:
        raise HTTPException(status_code=413, detail=f"Import exceeds {body.max_rows} rows")

    existing_rows = list((await db.execute(select(Asset))).scalars()) if body.deduplicate else []
    existing_by_key = {
        _dedupe_key(AssetCreateRequest(
            record_kind=record.record_kind,
            subject=orm_to_record(record).subject,
        )): record
        for record in existing_rows
    }
    results: list[ImportRowResult] = []
    pending: list[AssetRecord] = []
    now = datetime.now(timezone.utc)
    for row_number, row in enumerate(rows, start=2):
        try:
            request = map_row(row, row_number=row_number, mapping=body.field_mapping)
            key = _dedupe_key(request)
            existing = existing_by_key.get(key) if body.deduplicate else None
            if existing is not None:
                results.append(ImportRowResult(
                    row=row_number,
                    status="existing",
                    clip=existing.id,
                    linked_clips=request.subject.linked_clips,
                ))
                continue
            record_id = generate_clip(settings.NODE_DOMAIN, key_manager.public_key_multibase)
            record = _signed_record(
                record_id=record_id,
                record_kind=request.record_kind.value,
                subject=request.subject,
                relationships=[],
                availability=request.availability,
                controller=request.controller or _controller(),
                created_at=now,
                updated_at=now,
                version=1,
                key_manager=key_manager,
            )
            pending.append(record)
            results.append(ImportRowResult(
                row=row_number,
                status="validated" if body.dry_run else "created",
                clip=record.id,
                linked_clips=request.subject.linked_clips,
            ))
            existing_by_key[key] = Asset(
                id=record.id,
                record_kind=record.record_kind.value,
                record_json=record.model_dump(mode="json"),
            )
        except (ValueError, TypeError, KeyError) as error:
            results.append(ImportRowResult(row=row_number, status="failed", error=str(error)))

    if not body.dry_run:
        for record in pending:
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
                created_at=record.created_at,
                updated_at=record.updated_at,
                version=record.version,
                is_authoritative=True,
                verified_at=record.updated_at,
            ))
        await db.commit()
        for record in pending:
            asyncio.create_task(_push_to_peers(record))

    response = AssetImportResponse(
        import_id=import_id,
        source_format=body.source_format.lower(),
        total=len(rows),
        created=len(pending) if not body.dry_run else 0,
        existing=sum(item.status == "existing" for item in results),
        failed=sum(item.status == "failed" for item in results),
        dry_run=body.dry_run,
        rows=results,
    )
    job.status = "complete"
    job.result_json = response.model_dump(mode="json")
    job.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return response


@router.get("/{import_id}", response_model=ImportStatusResponse)
async def get_import_status(
    import_id: str,
    db: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> ImportStatusResponse:
    job = (await db.execute(
        select(ImportJob).where(ImportJob.import_id == import_id)
    )).scalar_one_or_none()
    if job is None:
        raise HTTPException(status_code=404, detail="Import not found")
    return ImportStatusResponse(
        import_id=job.import_id,
        status=job.status,
        result=AssetImportResponse.model_validate(job.result_json) if job.result_json else None,
    )