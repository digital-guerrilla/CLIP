"""Project setup and inherited organisation permissions."""

import base64
import binascii
from datetime import datetime, timedelta, timezone
import hashlib
import json
import secrets
from typing import Literal
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import Field, field_validator
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.ifcx_models import IfcxFile, IfcxModel
from ..core.ifc_protocol import IfcGraphProposalTransaction, IfcDecisionTransaction
from ..core.clip_protocol import ClipServiceMessage
from ..core.ifc_graph import flatten_ifc_layers
from ..core.ifc_authoring import TEMPLATES, PARENT_CLASSES, author_entity
from ..core.data_integrity import add_data_integrity_proof
from ..core.did import resolve_did_web_document, resolve_clip_service_endpoint, verify_clip_message_proof
from ..core.egress import request_json
from ..core.project_access import require_dataset_read
from ..db.database import get_db
from ..db.ifc_store import DuplicateIfcDataset, register_ifc_dataset
from ..db.orm_models import IfcDatasetTrustPolicy, ClipProjectPolicy, IfcDatasetRecord, IfcAuthoritySequence, IfcProposalRecord, ClipProjectInvite, ClipProjectJoinRequest
from ..dependencies import require_api_key, get_key_manager
from ..federation.clip_layers import resolve_ifc_layers, ClipFederationError
from ..federation.clip_replication import authenticate_service_message, sign_service_message, digest_json
from ..imports.ifcx import construction_schemas, SOURCE_SCHEMA
from .ifc_transactions import _local_authority, submit_proposal, decide_proposal, resolve_dataset_graph, dataset_history, publish_dataset
import httpx

router = APIRouter(prefix="/clip/v1/projects", tags=["projects"])
graph_router = APIRouter(prefix="/ifc/v1/projects", tags=["ifc"])
LIBRARY_SCHEMA = "urn:clip:construction:product-library:v1"


class ProjectCreate(IfcxModel):
    name: str = Field(min_length=1, max_length=160)
    visibility: Literal["private", "public"] = "private"
    kind: Literal["project", "product-library"] = "project"

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Project name is required")
        return value.strip()


class ProjectPermissions(IfcxModel):
    visibility: Literal["private", "public"]
    members: dict[str, Literal["viewer", "contributor"]] = Field(default_factory=dict, max_length=100)
    expected_revision: int = Field(alias="expectedRevision", ge=1)

    @field_validator("members")
    @classmethod
    def organisation_members(cls, value: dict[str, str]) -> dict[str, str]:
        if any(not did.startswith("did:web:") or len(did) > 512 for did in value):
            raise ValueError("Project members must be did:web organisations")
        if settings.DID_WEB_ID in value:
            raise ValueError("The project authority already manages this project")
        return value


def project_summary(policy: ClipProjectPolicy) -> dict:
    return {"projectId": policy.dataset_id, "authorityDid": policy.authority_did,
        "name": policy.name, "visibility": policy.visibility, "members": policy.members,
        "revision": policy.revision}


@router.get("")
async def list_projects(session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    pending = dict((await session.execute(select(ClipProjectJoinRequest.dataset_id, func.count()).where(
        ClipProjectJoinRequest.authority_did == authority_did, ClipProjectJoinRequest.status == "pending",
    ).group_by(ClipProjectJoinRequest.dataset_id))).all())
    rows = (await session.execute(select(ClipProjectPolicy).where(ClipProjectPolicy.authority_did == authority_did).order_by(ClipProjectPolicy.name))).scalars()
    return {"items": [{**project_summary(row), "pendingJoinRequests": pending.get(row.dataset_id, 0)} for row in rows]}


@graph_router.get("/templates")
async def get_templates() -> dict:
    return {"ifcSchema": "IFC4X3_ADD2", "ifcxProfile": "ifcx_alpha",
        "items": [{"id": key, "name": value[0], "ifcClass": value[1]} for key, value in TEMPLATES.items()],
        "parents": {key: sorted(values) for key, values in PARENT_CLASSES.items()}}


class EntityCreate(IfcxModel):
    name: str = Field(min_length=1, max_length=160)
    template: str
    parent_path: str | None = Field(default=None, alias="parentPath")
    type_path: str | None = Field(default=None, alias="typePath")

    @field_validator("name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        return ProjectCreate.nonempty_name(value)


@graph_router.post("/{project_id}/entities", status_code=201)
async def create_entity(project_id: str, body: EntityCreate, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, method = _local_authority()
    policy = await session.get(ClipProjectPolicy, (authority_did, project_id))
    dataset = await session.get(IfcDatasetRecord, (authority_did, project_id))
    if policy is None or dataset is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    try:
        file = IfcxFile.model_validate(dataset.file_json)
        resolved = await resolve_ifc_layers(file)
        graph = flatten_ifc_layers(resolved.layers, authority_did=authority_did, dataset_id=project_id)
        library = any(LIBRARY_SCHEMA in node.attributes for node in file.data)
        if library and body.template not in {"door-type", "pump-type"}:
            raise ValueError("Product libraries contain product types, not installed occurrences")
        entity = author_entity(body.template, body.name, type_path=body.type_path)
        class_name = TEMPLATES[body.template][1]
        if body.type_path:
            type_class = graph.effective_components(body.type_path).get(SOURCE_SCHEMA, {}).get("class")
            if class_name not in {"IfcDoor", "IfcPump"} or type_class != class_name + "Type":
                raise ValueError("The selected product type is incompatible with this occurrence")
        operations = [{"action": "create", "node": entity.model_dump(mode="json", by_alias=True)}]
        if body.parent_path:
            if body.parent_path not in {node.path for node in file.data}:
                raise ValueError("Parent must belong to this project")
            parent_class = graph.effective_components(body.parent_path).get(SOURCE_SCHEMA, {}).get("class")
            if class_name not in PARENT_CLASSES.get(parent_class, set()):
                raise ValueError("The selected parent cannot contain this IFC class")
            operations.append({"action": "contribute", "node": {"path": body.parent_path,
                "children": {entity.path.split("/")[-1]: entity.path}}})
    except (KeyError, ValueError, ClipFederationError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    state = await session.get(IfcAuthoritySequence, authority_did)
    sequence = state.sequence if state else 0
    now = datetime.now(timezone.utc)
    context = ["https://w3id.org/security/data-integrity/v2", {"@vocab": "urn:clip:protocol:"}]
    key = get_key_manager()
    proposal = IfcGraphProposalTransaction.model_validate(add_data_integrity_proof({
        "@context": context, "transactionId": str(uuid4()), "actorDid": authority_did,
        "target": {"authorityDid": authority_did, "datasetId": project_id},
        "operations": operations, "expectedSequence": sequence, "schemaDigest": dataset.schema_digest,
        "created": now.isoformat().replace("+00:00", "Z"),
    }, key.private_key_bytes, verification_method=method, proof_purpose="assertionMethod", created=now))
    unsigned = proposal.model_dump(mode="json", by_alias=True, exclude={"proof"})
    proposal = IfcGraphProposalTransaction.model_validate(add_data_integrity_proof(unsigned, key.private_key_bytes,
        verification_method=method, proof_purpose="assertionMethod", created=now))
    pending = await submit_proposal(proposal, session)
    decision = IfcDecisionTransaction.model_validate(add_data_integrity_proof({
        "@context": context, "transactionId": str(uuid4()), "actorDid": authority_did,
        "decision": "accept", "proposalId": pending["proposalId"], "proposalDigest": pending["proposalDigest"],
        "expectedSequence": sequence, "created": now.isoformat().replace("+00:00", "Z"),
    }, key.private_key_bytes, verification_method=method, proof_purpose="capabilityInvocation", created=now))
    receipt = await decide_proposal(decision, session, key)
    return {"entityPath": entity.path, "ifcClass": class_name, "projectId": project_id, "receipt": receipt}


@router.post("/read")
async def read_project(message: ClipServiceMessage, session: AsyncSession = Depends(get_db)) -> dict:
    try:
        await authenticate_service_message(message, "projectRead")
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    project_id = message.payload.get("projectId")
    if not isinstance(project_id, str) or not project_id or len(project_id) > 512:
        raise HTTPException(status_code=422, detail="projectId is required")
    await require_dataset_read(session, settings.DID_WEB_ID, project_id, actor_did=message.actor_did)
    policy = await session.get(ClipProjectPolicy, (settings.DID_WEB_ID, project_id))
    if policy is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    graph = await resolve_dataset_graph(project_id, session, settings.API_KEY)
    history = await dataset_history(project_id, session, settings.API_KEY)
    pending = (await session.execute(select(IfcProposalRecord).where(IfcProposalRecord.authority_did == settings.DID_WEB_ID,
        IfcProposalRecord.dataset_id == project_id).order_by(IfcProposalRecord.created_at).limit(200))).scalars()
    publication = await publish_dataset(project_id, session, get_key_manager(), settings.API_KEY)
    return sign_service_message("projectView", message.actor_did, {"requestId": str(message.message_id),
        "project": {"projectId": project_id, "authorityDid": policy.authority_did, "name": policy.name,
            "visibility": policy.visibility, "revision": policy.revision},
        "publication": publication, "graph": graph, "history": history["items"],
        "assertions": [{"proposal": row.proposal_json, "status": row.status} for row in pending]})


@router.post("", status_code=201)
async def create_project(body: ProjectCreate, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    project_id = "urn:clip:project:" + str(uuid4())
    now = datetime.now(timezone.utc)
    schemas = construction_schemas()
    root = {"path": "project", "attributes": {"ifc::name": body.name,
        SOURCE_SCHEMA: {"format": "CLIP", "id": project_id, "class": "IfcProject", "properties": {}}}}
    if body.kind == "product-library":
        project_id = "urn:clip:products:" + str(uuid4())
        schemas[LIBRARY_SCHEMA] = {"value": {"dataType": "Object"}}
        root = {"path": "library", "attributes": {"ifc::name": body.name, LIBRARY_SCHEMA: {"name": body.name}}}
    file = IfcxFile.model_validate({
        "header": {"id": project_id, "ifcxVersion": "ifcx_alpha", "dataVersion": "1.0.0", "author": authority_did, "timestamp": now.isoformat().replace("+00:00", "Z")},
        "imports": [], "schemas": schemas, "data": [root],
    })
    policy = ClipProjectPolicy(authority_did=authority_did, dataset_id=project_id, name=body.name,
        visibility=body.visibility, members={}, revision=1, updated_at=now)
    try:
        digest = await register_ifc_dataset(session, authority_did=authority_did, dataset_id=project_id, file=file, project=policy)
    except DuplicateIfcDataset as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {**project_summary(policy), "schemaDigest": digest}


@router.put("/{project_id}/permissions")
async def set_project_permissions(project_id: str, body: ProjectPermissions, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    policy = await session.get(ClipProjectPolicy, (authority_did, project_id))
    if policy is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    await _apply_project_permissions(session, policy, body)
    await session.commit()
    await session.refresh(policy)
    return project_summary(policy)


async def _apply_project_permissions(session: AsyncSession, policy: ClipProjectPolicy, body: ProjectPermissions) -> None:
    now = datetime.now(timezone.utc)
    result = await session.execute(update(ClipProjectPolicy).where(
        ClipProjectPolicy.authority_did == policy.authority_did, ClipProjectPolicy.dataset_id == policy.dataset_id,
        ClipProjectPolicy.revision == body.expected_revision,
    ).values(visibility=body.visibility, members=body.members, revision=body.expected_revision + 1, updated_at=now))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Project permissions changed; refresh before saving")
    trust = await session.get(IfcDatasetTrustPolicy, (policy.authority_did, policy.dataset_id))
    trust.trusted_proposers = [did for did, role in body.members.items() if role == "contributor"]
    trust.updated_at = now


class ProjectInviteCreate(IfcxModel):
    role: Literal["viewer", "contributor"] = "viewer"
    expires_hours: int = Field(default=168, alias="expiresHours", ge=1, le=720)


class ProjectInviteCode(IfcxModel):
    authority_did: str = Field(alias="authorityDid", pattern=r"^did:web:", max_length=512)
    invite_id: UUID = Field(alias="inviteId")
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")


class ProjectInviteRedeem(IfcxModel):
    code: str = Field(min_length=1, max_length=2048)


class ProjectJoinEnvelope(IfcxModel):
    token: str = Field(pattern=r"^[A-Za-z0-9_-]{43}$")
    message: ClipServiceMessage


class ProjectJoinDecision(IfcxModel):
    decision: Literal["accept", "reject"]
    expected_revision: int = Field(alias="expectedRevision", ge=1)


def _decode_invite_code(code: str) -> ProjectInviteCode:
    try:
        prefix, encoded = code.strip().split(".", 1)
        if prefix != "CLIP1":
            raise ValueError("Unsupported invite code")
        content = base64.b64decode(encoded + "=" * (-len(encoded) % 4), altchars=b"-_", validate=True)
        return ProjectInviteCode.model_validate(json.loads(content))
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise HTTPException(status_code=422, detail="Invalid project invite code") from error


def _invite_summary(invite: ClipProjectInvite) -> dict:
    return {"inviteId": invite.invite_id, "role": invite.role, "status": invite.status,
        "expiresAt": invite.expires_at.replace(tzinfo=timezone.utc), "created": invite.created_at.replace(tzinfo=timezone.utc)}


@router.post("/{project_id}/invites", status_code=201)
async def create_project_invite(project_id: str, body: ProjectInviteCreate, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    if await session.get(ClipProjectPolicy, (authority_did, project_id)) is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    now = datetime.now(timezone.utc)
    token = secrets.token_urlsafe(32)
    invite = ClipProjectInvite(invite_id=str(uuid4()), authority_did=authority_did, dataset_id=project_id,
        token_digest=hashlib.sha256(token.encode("ascii")).hexdigest(), role=body.role, status="active",
        expires_at=now + timedelta(hours=body.expires_hours), created_at=now)
    session.add(invite)
    await session.commit()
    content = ProjectInviteCode(authorityDid=authority_did, inviteId=invite.invite_id, token=token).model_dump(mode="json", by_alias=True)
    code = "CLIP1." + base64.urlsafe_b64encode(json.dumps(content, separators=(",", ":")).encode()).decode().rstrip("=")
    return {**_invite_summary(invite), "projectId": project_id, "code": code}


@router.get("/{project_id}/invites")
async def list_project_invites(project_id: str, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    if await session.get(ClipProjectPolicy, (authority_did, project_id)) is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    rows = (await session.execute(select(ClipProjectInvite).where(
        ClipProjectInvite.authority_did == authority_did, ClipProjectInvite.dataset_id == project_id,
    ).order_by(ClipProjectInvite.created_at.desc()).limit(100))).scalars()
    return {"items": [_invite_summary(row) for row in rows]}


@router.post("/invites/{invite_id}/revoke")
async def revoke_project_invite(invite_id: UUID, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    result = await session.execute(update(ClipProjectInvite).where(
        ClipProjectInvite.invite_id == str(invite_id), ClipProjectInvite.authority_did == authority_did,
        ClipProjectInvite.status.in_(["active", "pending"]),
    ).values(status="revoked"))
    if result.rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Invite is unavailable or already closed")
    await session.commit()
    return {"inviteId": str(invite_id), "status": "revoked"}


@router.post("/invites/redeem")
async def redeem_project_invite(body: ProjectInviteRedeem, _: None = Depends(require_api_key), session: AsyncSession = Depends(get_db)) -> dict:
    code = _decode_invite_code(body.code)
    if code.authority_did == settings.DID_WEB_ID:
        raise HTTPException(status_code=409, detail="Enter this code on the invited organisation's node")
    message = sign_service_message("projectJoin", code.authority_did, {
        "inviteId": str(code.invite_id), "tokenDigest": hashlib.sha256(code.token.encode("ascii")).hexdigest(),
    })
    try:
        document = await resolve_did_web_document(code.authority_did)
        endpoint = await resolve_clip_service_endpoint(document, expected_did=code.authority_did,
            service_type="ClipProjectInviteService", fragment="clip-project-invites")
        response = await request_json("POST", endpoint, body={"token": code.token, "message": message})
        acknowledgement = ClipServiceMessage.model_validate(response)
        await authenticate_service_message(acknowledgement, "projectJoinAcknowledgement")
        if acknowledgement.actor_did != code.authority_did or acknowledgement.payload.get("requestMessageId") != message["messageId"]:
            raise ValueError("Invite acknowledgement does not match this request")
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    from ..db.orm_models import SupplyChainProject
    payload = acknowledgement.payload
    project_id = payload["projectId"]
    joined = await session.get(SupplyChainProject, (code.authority_did, project_id))
    project = {"authorityDid": code.authority_did, "projectId": project_id,
        "name": payload["projectName"], "status": payload["status"], "role": payload["role"],
        "joinRequestId": payload["joinRequestId"], "joinRequest": message, "acknowledgement": response}
    if joined:
        joined.project_json = project
    else:
        session.add(SupplyChainProject(authority_did=code.authority_did, project_id=project_id, project_json=project))
    await session.commit()
    return response


@router.post("/invites/receive")
async def receive_project_join(body: ProjectJoinEnvelope, session: AsyncSession = Depends(get_db)) -> dict:
    message = body.message
    try:
        await authenticate_service_message(message, "projectJoin")
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    authority_did, _ = _local_authority()
    if message.actor_did == authority_did:
        raise HTTPException(status_code=409, detail="This authority already manages the project")
    digest = hashlib.sha256(body.token.encode("ascii")).hexdigest()
    if message.payload != {"inviteId": message.payload.get("inviteId"), "tokenDigest": digest}:
        raise HTTPException(status_code=403, detail="Invite token does not match the signed request")
    invite_id = message.payload.get("inviteId")
    if not isinstance(invite_id, str) or len(invite_id) > 64:
        raise HTTPException(status_code=422, detail="inviteId is required")
    invite = await session.get(ClipProjectInvite, invite_id)
    if invite is None or invite.authority_did != authority_did or not secrets.compare_digest(invite.token_digest, digest):
        raise HTTPException(status_code=404, detail="Invite is unavailable")
    now = datetime.now(timezone.utc)
    if invite.expires_at.replace(tzinfo=timezone.utc) <= now or invite.status in {"revoked", "rejected", "accepted"}:
        raise HTTPException(status_code=410, detail="Invite has expired or is closed")
    policy = await session.get(ClipProjectPolicy, (authority_did, invite.dataset_id))
    if policy is None:
        raise HTTPException(status_code=404, detail="Invite is unavailable")
    existing = (await session.execute(select(ClipProjectJoinRequest).where(ClipProjectJoinRequest.invite_id == invite_id))).scalar_one_or_none()
    if existing is not None:
        if existing.actor_did != message.actor_did or existing.status != "pending":
            raise HTTPException(status_code=409, detail="This single-use invite already has a join request")
        request = existing
    else:
        if message.actor_did in policy.members:
            raise HTTPException(status_code=409, detail="This organisation is already a project participant")
        claimed = await session.execute(update(ClipProjectInvite).where(
            ClipProjectInvite.invite_id == invite_id, ClipProjectInvite.status == "active",
            ClipProjectInvite.expires_at > now,
        ).values(status="pending").execution_options(synchronize_session=False))
        if claimed.rowcount != 1:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Invite changed; retry the join request")
        request = ClipProjectJoinRequest(request_id=str(uuid4()), invite_id=invite_id, authority_did=authority_did,
            dataset_id=invite.dataset_id, actor_did=message.actor_did, role=invite.role, status="pending",
            request_json=message.model_dump(mode="json", by_alias=True), created_at=now, updated_at=now)
        session.add(request)
        await session.commit()
    return sign_service_message("projectJoinAcknowledgement", message.actor_did, {
        "requestMessageId": str(message.message_id), "joinRequestId": request.request_id,
        "projectId": invite.dataset_id, "projectName": policy.name, "role": request.role, "status": request.status,
    })


@router.get("/{project_id}/join-requests")
async def list_project_join_requests(project_id: str, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    policy = await session.get(ClipProjectPolicy, (authority_did, project_id))
    if policy is None:
        raise HTTPException(status_code=404, detail="Unknown project")
    rows = (await session.execute(select(ClipProjectJoinRequest, ClipProjectInvite).join(
        ClipProjectInvite, ClipProjectInvite.invite_id == ClipProjectJoinRequest.invite_id,
    ).where(ClipProjectJoinRequest.authority_did == authority_did, ClipProjectJoinRequest.dataset_id == project_id)
        .order_by(ClipProjectJoinRequest.created_at.desc()).limit(100))).all()
    return {"revision": policy.revision, "items": [{"joinRequestId": row.request_id, "actorDid": row.actor_did,
        "role": row.role, "status": row.status, "inviteStatus": invite.status, "expiresAt": invite.expires_at.replace(tzinfo=timezone.utc),
        "created": row.created_at.replace(tzinfo=timezone.utc), "request": row.request_json, "decision": row.decision_json} for row, invite in rows]}


@router.post("/join-requests/{request_id}/decision")
async def decide_project_join(request_id: UUID, body: ProjectJoinDecision, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    authority_did, _ = _local_authority()
    request = await session.get(ClipProjectJoinRequest, str(request_id))
    if request is None or request.authority_did != authority_did:
        raise HTTPException(status_code=404, detail="Unknown join request")
    if request.status != "pending":
        raise HTTPException(status_code=409, detail="This join request has already been decided")
    invite = await session.get(ClipProjectInvite, request.invite_id)
    policy = await session.get(ClipProjectPolicy, (authority_did, request.dataset_id))
    if invite is None or policy is None:
        raise HTTPException(status_code=404, detail="Unknown project invitation")
    now = datetime.now(timezone.utc)
    status = "accepted" if body.decision == "accept" else "rejected"
    if body.decision == "accept":
        if invite.expires_at.replace(tzinfo=timezone.utc) <= now:
            raise HTTPException(status_code=410, detail="Invite has expired; issue a new invite")
        if invite.status != "pending" or request.actor_did in policy.members:
            raise HTTPException(status_code=409, detail="Invite or membership changed; review the request")
        if policy.revision != body.expected_revision:
            raise HTTPException(status_code=409, detail="Project permissions changed; refresh before deciding")
        if len(policy.members) >= 100:
            raise HTTPException(status_code=409, detail="Project participant limit reached")
        try:
            document = await resolve_did_web_document(request.actor_did)
        except (ValueError, httpx.HTTPError) as error:
            raise HTTPException(status_code=424, detail=str(error)) from error
        if not verify_clip_message_proof(request.request_json, document):
            raise HTTPException(status_code=403, detail="Join request signing key is no longer authorised")
        now = datetime.now(timezone.utc)
        permissions = ProjectPermissions(visibility=policy.visibility, expectedRevision=body.expected_revision,
            members={**policy.members, request.actor_did: request.role})
        await _apply_project_permissions(session, policy, permissions)
    closed = await session.execute(update(ClipProjectInvite).where(
        ClipProjectInvite.invite_id == invite.invite_id, ClipProjectInvite.status == "pending",
        *([ClipProjectInvite.expires_at > now] if body.decision == "accept" else []),
    ).values(status=status).execution_options(synchronize_session=False))
    if body.decision == "accept" and closed.rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Invite changed; refresh before deciding")
    receipt = sign_service_message("projectJoinDecision", request.actor_did, {
        "joinRequestId": request.request_id, "projectId": request.dataset_id, "role": request.role,
        "decision": body.decision, "requestDigest": digest_json(request.request_json),
        "projectRevision": body.expected_revision + 1 if body.decision == "accept" else policy.revision,
    })
    decided = await session.execute(update(ClipProjectJoinRequest).where(
        ClipProjectJoinRequest.request_id == request.request_id, ClipProjectJoinRequest.status == "pending",
    ).values(status=status, decision_json=receipt, updated_at=now))
    if decided.rowcount != 1:
        await session.rollback()
        raise HTTPException(status_code=409, detail="Join request changed; refresh before deciding")
    await session.commit()
    return receipt