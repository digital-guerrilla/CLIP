"""Node-authorised authoring and DID-authenticated supply-chain exchange."""

import base64
from datetime import datetime
from typing import Literal
from urllib.parse import quote

import rfc8785
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import Response
from pydantic import Field, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.ifcx_models import IfcxModel
from ..core import supply_chain as service
from ..db.database import get_db
from ..db.orm_models import (
    SupplyChainRecord, SupplyChainRevision, SupplyChainSubmission, SupplyChainProject,
    ClipProjectPolicy,
    SupplyChainDocumentGrant,
    SupplyChainSenderPolicy, ClipProjectJoinRequest,
)
from ..dependencies import require_api_key

router = APIRouter(prefix="/clip/v1/supply-chain", tags=["supply-chain"])


class Source(IfcxModel):
    authority_did: str = Field(alias="authorityDid", pattern=r"^did:web:", max_length=512)
    record_id: str = Field(alias="recordId", min_length=1, max_length=512)
    revision: int = Field(ge=1)
    quantity: float | None = Field(default=None, gt=0, allow_inf_nan=False, strict=True)
    unit: str | None = Field(default=None, min_length=1, max_length=40)
    serials: list[str] = Field(default_factory=list, max_length=10000)
    ifc_class: str | None = Field(default=None, alias="ifcClass", pattern=r"^Ifc")
    digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    dataset_id: str | None = Field(default=None, alias="datasetId", max_length=512)
    entity_path: str | None = Field(default=None, alias="entityPath", max_length=512)
    component_schema: str | None = Field(default=None, alias="componentSchema", max_length=512)


class RecordCreate(IfcxModel):
    kind: Literal["product", "offering", "supply", "installation", "asset"]
    name: str = Field(min_length=1, max_length=160)
    project_id: str | None = Field(default=None, alias="projectId", max_length=512)
    ifc_class: str = Field(default="IfcBuildingElementProxyType", alias="ifcClass", pattern=r"^Ifc", max_length=100)
    data: dict = Field(default_factory=dict)
    sources: list[Source] = Field(default_factory=list, max_length=32)
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=1, max_length=160)

    @field_validator("name")
    @classmethod
    def name_required(cls, value):
        if not value.strip():
            raise ValueError("Name is required")
        return value.strip()


class RecordUpdate(RecordCreate):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class RecordAdopt(IfcxModel):
    dataset_id: str = Field(alias="datasetId", min_length=1, max_length=512)
    entity_path: str = Field(alias="entityPath", min_length=1, max_length=512)
    kind: Literal["product"] = "product"
    expected_sequence: int = Field(alias="expectedSequence", ge=0)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=160)


class RevisionRequest(IfcxModel):
    expected_revision: int = Field(alias="expectedRevision", ge=1)


class Discover(IfcxModel):
    authority_did: str = Field(alias="authorityDid", pattern=r"^did:web:", max_length=512)


class Connect(Discover):
    project_id: str = Field(alias="projectId", min_length=1, max_length=512)


class DocumentUpload(RevisionRequest):
    name: str = Field(min_length=1, max_length=160)
    media_type: str = Field(alias="mediaType", pattern=r"^[a-zA-Z0-9.+-]+/[a-zA-Z0-9.+-]+$", max_length=100)
    content: str = Field(min_length=1)
    visibility: Literal["private", "public"] = "private"
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=1, max_length=160)
    replaces_document_id: str | None = Field(default=None, alias="replacesDocumentId", min_length=1, max_length=512)


class DocumentDetach(RevisionRequest):
    idempotency_key: str | None = Field(default=None, alias="idempotencyKey", min_length=1, max_length=160)


class DocumentGrant(IfcxModel):
    recipient_did: str = Field(alias="recipientDid", pattern=r"^did:web:", max_length=512)
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    active: bool = True
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    @field_validator("expires_at")
    @classmethod
    def timezone_required(cls, value):
        if value and value.tzinfo is None:
            raise ValueError("Evidence expiry requires a timezone")
        return value


class SubmissionCreate(IfcxModel):
    recipient_did: str = Field(alias="recipientDid", pattern=r"^did:web:", max_length=512)
    project_id: str = Field(alias="projectId", min_length=1, max_length=512)
    record_ids: list[str] = Field(alias="recordIds", min_length=1, max_length=32)
    document_ids: list[str] = Field(default_factory=list, alias="documentIds", max_length=100)
    supersedes: str | None = Field(default=None, max_length=512)
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=160)


class IssueRequest(RevisionRequest):
    idempotency_key: str = Field(alias="idempotencyKey", min_length=1, max_length=160)


class DecisionRequest(IssueRequest):
    decision: Literal["accept", "reject", "request-changes"]
    reason: str = Field(default="", max_length=2000)
    record_ids: list[str] | None = Field(default=None, alias="recordIds", min_length=1, max_length=32)


class ServiceMessage(IfcxModel):
    context: list = Field(alias="@context")
    message_id: str = Field(alias="messageId", min_length=1, max_length=100)
    actor_did: str = Field(alias="actorDid", pattern=r"^did:web:", max_length=512)
    audience_did: str = Field(alias="audienceDid", pattern=r"^did:web:", max_length=512)
    action: Literal["catalogue", "projectConnect", "projectJoinStatus", "issue", "decision", "documentRead"]
    payload: dict
    created: datetime
    proof: dict


def dump(body):
    return body.model_dump(mode="json", by_alias=True, exclude_none=True)


@router.get("/schema")
async def workflow_schema():
    return {"profile": service.RECORD_PROFILE, "authentication": "node-api-key",
        "kinds": [
            {"kind": "product", "name": "Manufacturer product", "publicCatalogue": True,
                "sourceKinds": ["product", "offering"], "sourceQuantityRequired": True, "ifcClassKind": "type"},
            {"kind": "offering", "name": "Supplier offering", "publicCatalogue": True,
                "sourceKinds": ["product", "offering"], "sourceCount": 1, "ifcClassKind": "matching-type"},
            {"kind": "supply", "name": "Project supply", "publicCatalogue": False,
                "sourceKinds": ["offering"], "sourceCount": 1, "dataRequired": ["quantity", "unit"],
                "dataOptional": ["serials", "batch"], "ifcClassKind": "matching-type"},
            {"kind": "installation", "name": "Installation", "publicCatalogue": False,
                "sourceKinds": ["supply", "installation", "asset"],
                "supplySourceRequires": ["acceptedFrom", "quantity", "unit"], "sourceOptional": ["serials"],
                "ifcClassKind": "occurrence"},
            {"kind": "asset", "name": "Recipient asset", "publicCatalogue": False,
                "sourceKinds": ["installation", "asset", "supply"], "ifcClassKind": "occurrence"},
        ], "typeClasses": ["IfcPumpType", "IfcDoorType", "IfcBuildingElementProxyType"],
        "occurrenceClasses": ["IfcPump", "IfcDoor", "IfcBuildingElementProxy"],
        "decisions": ["accept", "reject", "request-changes"],
        "sourceAddressFields": ["authorityDid", "recordId", "revision", "datasetId", "entityPath", "digest", "componentSchema"]}


@router.get("/records")
async def list_records(kind: str | None = None, projectId: str | None = None,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    query = select(SupplyChainRecord).where(SupplyChainRecord.authority_did == service.authority())
    if kind:
        query = query.where(SupplyChainRecord.kind == kind)
    if projectId:
        query = query.where(SupplyChainRecord.project_id == projectId)
    rows = (await session.execute(query)).scalars()
    return {"items": [row.record_json for row in rows]}


@router.post("/records", status_code=201)
async def create_record(body: RecordCreate, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.save_record(session, dump(body))


@router.post("/records/adopt", status_code=201)
async def adopt_record(body: RecordAdopt, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.adopt_record(session, dump(body))


@router.get("/records/adopt/candidates")
@router.get("/records/adoption-candidates")
async def adoption_candidates(session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.adoption_candidates(session)


@router.get("/revisions")
async def owned_revisions(session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    rows = (await session.execute(select(SupplyChainRevision).join(
        SupplyChainRecord,
        (SupplyChainRecord.authority_did == SupplyChainRevision.authority_did)
        & (SupplyChainRecord.record_id == SupplyChainRevision.record_id),
    ).where(SupplyChainRecord.authority_did == service.authority())
        .order_by(SupplyChainRevision.record_id, SupplyChainRevision.revision))).scalars()
    return {"items": [row.snapshot_json for row in rows]}


@router.get("/records/{record_id}")
async def get_record(record_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return (await service.record(session, record_id)).record_json


@router.put("/records/{record_id}")
async def update_record(record_id: str, body: RecordUpdate, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.save_record(session, dump(body), record_id)


@router.post("/records/{record_id}/publish")
async def publish_record(record_id: str, body: RevisionRequest, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    row = await service.record(session, record_id)
    from sqlalchemy import update
    claimed = await session.execute(update(SupplyChainRecord).where(
        SupplyChainRecord.record_id == record_id, SupplyChainRecord.revision == body.expected_revision
    ).values(revision=body.expected_revision).returning(SupplyChainRecord.record_id))
    if claimed.scalar_one_or_none() is None:
        raise HTTPException(409, "Record changed; refresh before publishing")
    existing = await session.get(SupplyChainRevision, (service.authority(), record_id, row.revision))
    if existing and existing.public:
        await session.commit()
        return existing.snapshot_json
    snapshot = await service.freeze(session, row, public=True)
    dataset = await session.get(service.IfcDatasetRecord, (service.authority(), row.record_json["datasetId"]))
    await service.graph_commit(session, dataset, [row.record_json])
    return snapshot


@router.get("/records/{record_id}/revisions")
async def record_revisions(record_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    await service.record(session, record_id)
    rows = (await session.execute(select(SupplyChainRevision).where(
        SupplyChainRevision.authority_did == service.authority(), SupplyChainRevision.record_id == record_id)
        .order_by(SupplyChainRevision.revision))).scalars()
    return {"items": [row.snapshot_json for row in rows]}


@router.post("/records/{record_id}/revisions")
async def freeze_private_revision(record_id: str, body: RevisionRequest,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    from sqlalchemy import update
    row = await service.record(session, record_id)
    claimed = await session.execute(update(SupplyChainRecord).where(
        SupplyChainRecord.record_id == record_id, SupplyChainRecord.revision == body.expected_revision
    ).values(revision=body.expected_revision).returning(SupplyChainRecord.record_id))
    if claimed.scalar_one_or_none() is None:
        raise HTTPException(409, "Record changed; refresh before freezing")
    snapshot = await service.freeze(session, row)
    await session.commit()
    return snapshot


@router.get("/catalogue")
async def public_catalogue(session: AsyncSession = Depends(get_db)):
    return await service.catalogue(session)


@router.get("/catalogue/{record_id}/revisions/{revision}")
async def public_revision(record_id: str, revision: int, session: AsyncSession = Depends(get_db)):
    row = await session.get(SupplyChainRevision, (service.authority(), record_id, revision))
    if not row or not row.public:
        raise HTTPException(404, "Unknown public catalogue revision")
    return Response(rfc8785.dumps(row.snapshot_json), media_type="application/json",
        headers={"ETag": '"' + service.digest_json(row.snapshot_json) + '"', "Cache-Control": "public, immutable, max-age=31536000"})


@router.get("/catalogue/{record_id}/revisions/{revision}/documents/{document_id}")
async def public_revision_document(record_id: str, revision: int, document_id: str,
    session: AsyncSession = Depends(get_db)):
    row = await session.get(SupplyChainRevision, (service.authority(), record_id, revision))
    if not row or not row.public:
        raise HTTPException(404, "Unknown public catalogue revision")
    metadata = next((item for item in row.snapshot_json["documents"] if item["id"] == document_id and "content" in item), None)
    if not metadata:
        raise HTTPException(404, "Document is not deliberately public in this revision")
    return Response(base64.b64decode(metadata["content"], validate=True), media_type=metadata["mediaType"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(metadata["name"]),
            "Cache-Control": "public, immutable, max-age=31536000", "X-Content-Type-Options": "nosniff"})


class DependencyPreview(IfcxModel):
    sources: list[Source] = Field(min_length=1, max_length=32)


@router.post("/dependencies/preview")
async def preview_dependencies(body: DependencyPreview, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    items = [await service.source_revision(session, dump(source)) for source in body.sources]
    return {"items": items, "digests": [service.digest_json(item) for item in items]}


@router.post("/catalogue/discover")
async def discover_catalogue(body: Discover, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    service.require_trusted_publisher(body.authority_did)
    from ..core.ifc_product_resolver import discover_published
    result = await discover_published(session, body.authority_did)
    await session.commit()
    return result


@router.post("/records/{record_id}/documents", status_code=201)
async def upload_document(record_id: str, body: DocumentUpload, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.upload_document(session, await service.record(session, record_id), dump(body))


@router.get("/records/{record_id}/documents")
async def list_documents(record_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return {"items": (await service.record(session, record_id)).record_json["documents"]}


@router.post("/records/{record_id}/documents/{document_id}/detach")
async def detach_document(record_id: str, document_id: str, body: DocumentDetach,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    return await service.detach_document(session, await service.record(session, record_id), document_id, dump(body))


@router.get("/records/{record_id}/documents/{document_id}/grants")
async def list_document_grants(record_id: str, document_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    row = await service.record(session, record_id)
    document = await session.get(service.SupplyChainDocument, document_id)
    if not document or document.record_id != row.record_id:
        raise HTTPException(403, "Only the original document authority can inspect its grants")
    rows = (await session.execute(select(SupplyChainDocumentGrant).where(SupplyChainDocumentGrant.document_id == document_id))).scalars()
    return {"items": [item.grant_json for item in rows]}


@router.post("/records/{record_id}/documents/{document_id}/grants")
async def grant_document(record_id: str, document_id: str, body: DocumentGrant,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    return await service.grant_document(session, await service.record(session, record_id), document_id, dump(body))


@router.get("/documents/{document_id}")
async def read_source_document(document_id: str, authorityDid: str, projectId: str | None = None,
    recordId: str | None = None, revision: int | None = None,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    if recordId is not None or revision is not None:
        if not recordId or revision is None or revision < 1:
            raise HTTPException(422, "A published document requires both recordId and a positive revision")
        row = await session.get(SupplyChainRevision, (authorityDid, recordId, revision))
        if not row or not row.public:
            raise HTTPException(404, "Published catalogue revision is not cached at this node")
        metadata = next((item for item in row.snapshot_json["documents"]
            if item["id"] == document_id and item["visibility"] == "public" and "content" in item), None)
        if metadata is None:
            raise HTTPException(404, "Document is not deliberately public in this revision")
        try:
            content = base64.b64decode(metadata["content"], validate=True)
        except (ValueError, TypeError) as error:
            raise HTTPException(424, "Cached publication contains invalid document bytes") from error
    elif authorityDid == service.authority():
        document, content = await service.authorised_document(session, document_id)
        metadata = document.metadata_json
    else:
        metadata = await service.remote(authorityDid, "documentRead", {"documentId": document_id, "projectId": projectId})
        try:
            content = base64.b64decode(metadata["content"], validate=True)
        except (KeyError, ValueError) as error:
            raise HTTPException(424, "Remote source returned invalid evidence bytes") from error
    if service.hashlib.sha256(content).hexdigest() != metadata["digest"]:
        raise HTTPException(424, "Evidence digest verification failed")
    return Response(content, media_type=metadata["mediaType"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(metadata["name"]),
            "X-Content-Type-Options": "nosniff"})


@router.get("/records/{record_id}/documents/{document_id}")
async def read_document(record_id: str, document_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    row = await service.record(session, record_id)
    metadata = next((item for item in row.record_json["documents"] if item["id"] == document_id), None)
    if not metadata:
        raise HTTPException(404, "Document is not attached to this record")
    if metadata["authorityDid"] == service.authority():
        _, content = await service.authorised_document(session, document_id)
    elif "content" in metadata:
        content = base64.b64decode(metadata["content"], validate=True)
    else:
        result = await service.remote(metadata["authorityDid"], "documentRead",
            {"documentId": document_id, "projectId": row.project_id})
        content = base64.b64decode(result["content"], validate=True)
    if service.hashlib.sha256(content).hexdigest() != metadata["digest"]:
        raise HTTPException(424, "Evidence digest verification failed")
    return Response(content, media_type=metadata["mediaType"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(metadata["name"]),
            "X-Content-Type-Options": "nosniff"})


@router.get("/projects")
async def projects(session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    local = (await session.execute(select(ClipProjectPolicy).where(ClipProjectPolicy.authority_did == service.authority()))).scalars()
    joined = (await session.execute(select(SupplyChainProject))).scalars()
    return {"items": [{"projectId": item.dataset_id, "authorityDid": item.authority_did,
        "name": item.name, "visibility": item.visibility, "revision": item.revision, "local": True}
        for item in local] + [{**item.project_json, "local": False} for item in joined]}


@router.post("/projects/connect")
async def connect_project(body: Connect, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    if body.authority_did == service.authority():
        policy = await service.project_access(session, body.project_id, service.authority())
        return {"projectId": policy.dataset_id, "authorityDid": policy.authority_did, "name": policy.name,
            "visibility": policy.visibility, "revision": policy.revision, "local": True}
    result = await service.remote(body.authority_did, "projectConnect", {"projectId": body.project_id})
    if (result.get("authorityDid"), result.get("projectId")) != (body.authority_did, body.project_id):
        raise HTTPException(424, "Project service returned a different project")
    row = await session.get(SupplyChainProject, (body.authority_did, body.project_id))
    if row:
        row.project_json = {**row.project_json, **result, "status": "accepted"}
    else:
        session.add(SupplyChainProject(authority_did=body.authority_did, project_id=body.project_id, project_json={**result, "status": "accepted"}))
    await session.commit()
    return {**result, "local": False}


class SenderPermissions(IfcxModel):
    expected_revision: int = Field(alias="expectedRevision", ge=0)
    senders: list[str] = Field(max_length=100)

    @field_validator("senders")
    @classmethod
    def did_senders(cls, values):
        if len(values) != len(set(values)) or any(not item.startswith("did:web:") or len(item) > 512 for item in values):
            raise ValueError("Senders must be distinct did:web organisations")
        return values


@router.get("/projects/{project_id}/senders")
async def project_senders(project_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    await service.project_access(session, project_id, service.authority())
    row = await session.get(SupplyChainSenderPolicy, project_id)
    return {"projectId": project_id, "revision": row.revision if row else 0, "senders": row.senders if row else []}


@router.put("/projects/{project_id}/senders")
async def update_project_senders(project_id: str, body: SenderPermissions,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    from sqlalchemy import update
    from sqlalchemy.exc import IntegrityError
    await service.project_access(session, project_id, service.authority())
    row = await session.get(SupplyChainSenderPolicy, project_id)
    if row:
        changed = await session.execute(update(SupplyChainSenderPolicy).where(
            SupplyChainSenderPolicy.project_id == project_id, SupplyChainSenderPolicy.revision == body.expected_revision
        ).values(revision=body.expected_revision + 1, senders=body.senders).returning(SupplyChainSenderPolicy.project_id))
        if changed.scalar_one_or_none() is None:
            raise HTTPException(409, "Scoped sender permissions changed")
    else:
        if body.expected_revision != 0:
            raise HTTPException(409, "Scoped sender permissions changed")
        session.add(SupplyChainSenderPolicy(project_id=project_id, revision=1, senders=body.senders))
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise HTTPException(409, "Scoped sender permissions changed concurrently") from error
    return {"projectId": project_id, "revision": body.expected_revision + 1, "senders": body.senders}


@router.post("/projects/refresh")
async def refresh_project(body: Connect, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    row = await session.get(SupplyChainProject, (body.authority_did, body.project_id))
    if not row:
        raise HTTPException(404, "Unknown joined/pending project")
    request_id = row.project_json.get("joinRequestId")
    if not request_id:
        return await connect_project(body, session, None)
    result = await service.remote(body.authority_did, "projectJoinStatus",
        {"projectId": body.project_id, "joinRequestId": request_id})
    if result.get("projectId") != body.project_id or result.get("joinRequestId") != request_id:
        raise HTTPException(424, "Join status response does not match the local invitation")
    row.project_json = {**row.project_json, **result}
    await session.commit()
    return {**row.project_json, "local": False}


@router.get("/submissions")
async def submissions(direction: str | None = None, projectId: str | None = None,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    rows = (await session.execute(select(SupplyChainSubmission))).scalars()
    return {"items": [row.submission_json for row in rows
        if (not direction or direction == row.direction or row.submission_json.get("localRecipient"))
        and (not projectId or projectId == row.submission_json["projectId"])]}


@router.post("/submissions", status_code=201)
async def create_submission(body: SubmissionCreate, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.create_submission(session, dump(body))


@router.get("/submissions/{submission_id}")
async def get_submission(submission_id: str, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return (await service.submission(session, submission_id)).submission_json


@router.get("/submissions/{submission_id}/documents/{document_id}")
async def read_submission_document(submission_id: str, document_id: str,
    session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)):
    submission = (await service.submission(session, submission_id)).submission_json
    issue = submission.get("issue")
    if not issue:
        raise HTTPException(409, "Draft has not disclosed an immutable issue")
    metadata = next((item for item in issue.get("documents", []) if item["id"] == document_id), None)
    if metadata is None:
        metadata = next((item for record in issue["records"] for item in record.get("documents", [])
            if item["id"] == document_id and item.get("visibility") == "public"), None)
    if metadata is None:
        raise HTTPException(404, "Evidence was not disclosed in this issue")
    if "content" in metadata:
        content = base64.b64decode(metadata["content"], validate=True)
    elif metadata["authorityDid"] == service.authority():
        _, content = await service.authorised_document(session, document_id)
    else:
        result = await service.remote(metadata["authorityDid"], "documentRead",
            {"documentId": document_id, "projectId": submission["projectId"]})
        content = base64.b64decode(result["content"], validate=True)
    if service.hashlib.sha256(content).hexdigest() != metadata["digest"]:
        raise HTTPException(424, "Issue evidence digest verification failed")
    return Response(content, media_type=metadata["mediaType"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(metadata["name"]),
            "X-Content-Type-Options": "nosniff"})


@router.post("/submissions/{submission_id}/issue")
async def issue_submission(submission_id: str, body: IssueRequest, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.issue_submission(session, await service.submission(session, submission_id), dump(body))


@router.post("/submissions/{submission_id}/decision")
async def decide_submission(submission_id: str, body: DecisionRequest, session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key)):
    return await service.decide_submission(session, await service.submission(session, submission_id), dump(body))


@router.post("/receive")
async def receive(body: ServiceMessage, session: AsyncSession = Depends(get_db)):
    if body.audience_did != service.authority() or body.created.tzinfo is None:
        raise HTTPException(401, "Service message has wrong audience or no timezone")
    age = (service.now() - body.created).total_seconds()
    try:
        proof_created = datetime.fromisoformat(body.proof.get("created", "").replace("Z", "+00:00"))
    except ValueError as error:
        raise HTTPException(401, "Service proof timestamp is invalid") from error
    if not -30 <= age <= 300 or proof_created != body.created:
        raise HTTPException(401, "Service message is expired or proof timestamp mismatches")
    try:
        await service.verify(dump(body), body.actor_did, "authentication")
    except HTTPException as error:
        raise HTTPException(401, error.detail) from error
    if body.action == "catalogue":
        result = await service.catalogue(session)
    elif body.action == "projectConnect":
        policy = await service.project_access(session, body.payload.get("projectId"), body.actor_did)
        result = {"authorityDid": policy.authority_did, "projectId": policy.dataset_id,
            "name": policy.name, "visibility": policy.visibility, "revision": policy.revision}
    elif body.action == "projectJoinStatus":
        request = await session.get(ClipProjectJoinRequest, body.payload.get("joinRequestId"))
        if not request or request.actor_did != body.actor_did or request.dataset_id != body.payload.get("projectId"):
            raise HTTPException(403, "Join request is not owned by this organisation")
        policy = await session.get(ClipProjectPolicy, (service.authority(), request.dataset_id))
        if policy is None:
            raise HTTPException(404, "The invitation project is no longer available")
        result = {"authorityDid": service.authority(), "projectId": request.dataset_id,
            "joinRequestId": request.request_id, "name": policy.name, "role": request.role,
            "status": request.status, "decision": request.decision_json}
    elif body.action == "issue":
        received = await service.receive_issue(session, body.payload.get("issue", {}), body.actor_did)
        result = {"id": received["id"], "status": received["status"], "issueDigest": service.digest_json(received["issue"])}
    elif body.action == "decision":
        received = await service.receive_decision(session, body.payload.get("decision", {}), body.actor_did)
        result = {"id": received["id"], "status": received["status"]}
    else:
        document, content = await service.authorised_document(session, body.payload.get("documentId"),
            body.actor_did)
        result = {**document.metadata_json, "content": base64.b64encode(content).decode()}
    return service.service_message(body.actor_did, body.action + "Response", result, request_id=body.message_id)
