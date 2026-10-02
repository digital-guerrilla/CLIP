"""DID service discovery, proof-bundle replication and signed acknowledgements."""

from datetime import datetime, timezone

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.egress import request_json
from ..core.did import resolve_did_web_document, resolve_clip_service_endpoint
from ..core.clip_protocol import ClipServiceMessage
from ..db.database import get_db
from ..db.orm_models import IfcAcceptedTransaction, IfcReceiptRecord, ClipReplicaBundle, ClipReplicationAck, ClipProjectPolicy
from ..core.project_access import can_read_project
from ..dependencies import get_key_manager, require_api_key
from ..federation.clip_replication import authenticate_service_message, digest_json, sign_service_message, verify_replication_bundle
from .ifc_transactions import publish_dataset

router = APIRouter(prefix="/clip/v1/replication", tags=["replication"])


class ReplicationRequest(BaseModel):
    peer_did: str = Field(alias="peerDid", pattern=r"^did:web:")
    dataset_id: str = Field(alias="datasetId", min_length=1)


@router.post("/receive")
async def receive_bundle(message: ClipServiceMessage, session: AsyncSession = Depends(get_db)) -> dict:
    try:
        document = await authenticate_service_message(message, "replication")
        dataset_id, sequence = await verify_replication_bundle(message.payload, message.actor_did, document)
    except (ValueError, KeyError, httpx.HTTPError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    if message.actor_did == settings.DID_WEB_ID:
        raise HTTPException(status_code=409, detail="Cannot replicate local authority state to itself")
    if message.actor_did not in {value.strip() for value in settings.CLIP_TRUSTED_PUBLISHERS.split(",")}:
        raise HTTPException(status_code=403, detail="Replication publisher is not trusted by this operator")
    digest = digest_json(message.payload)
    existing = await session.get(ClipReplicaBundle, (message.actor_did, dataset_id), with_for_update=True)
    if existing is not None:
        if sequence < existing.sequence or (sequence == existing.sequence and digest != existing.digest):
            raise HTTPException(status_code=409, detail="Replicated state is stale or conflicting")
        prior = existing.bundle_json["transactions"]
        if message.payload["transactions"][:len(prior)] != prior:
            raise HTTPException(status_code=409, detail="Replica history cannot be rewritten")
        existing.sequence, existing.digest, existing.bundle_json = sequence, digest, message.payload
        existing.received_at = datetime.now(timezone.utc)
    else:
        session.add(ClipReplicaBundle(authority_did=message.actor_did, dataset_id=dataset_id, sequence=sequence,
            digest=digest, bundle_json=message.payload, received_at=datetime.now(timezone.utc)))
    await session.commit()
    return sign_service_message("replicationAcknowledgement", message.actor_did, {
        "digest": digest, "authorityDid": message.actor_did, "datasetId": dataset_id, "sequence": sequence,
    })


@router.post("/push")
async def push_bundle(body: ReplicationRequest, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    policies = (await session.execute(select(ClipProjectPolicy).where(ClipProjectPolicy.authority_did == settings.DID_WEB_ID))).scalars().all()
    if any(not can_read_project(policy, actor_did=body.peer_did) for policy in policies):
        raise HTTPException(status_code=403, detail="Full authority replication requires access to every project")
    publication = await publish_dataset(body.dataset_id, session, get_key_manager(), settings.API_KEY)
    entries = (await session.execute(
        select(IfcAcceptedTransaction, IfcReceiptRecord)
        .join(IfcReceiptRecord, IfcReceiptRecord.transaction_id == IfcAcceptedTransaction.transaction_id)
        .where(IfcAcceptedTransaction.authority_did == settings.DID_WEB_ID)
        .order_by(IfcAcceptedTransaction.sequence)
    )).all()
    bundle = {"publication": publication, "transactions": [
        {"transaction": transaction.transaction_json, "receipt": receipt.receipt_json} for transaction, receipt in entries
    ]}
    digest = digest_json(bundle)
    try:
        document = await resolve_did_web_document(body.peer_did)
        endpoint = await resolve_clip_service_endpoint(document, expected_did=body.peer_did, service_type="ClipReplicationService", fragment="clip-replication")
        response = await request_json("POST", endpoint, body=sign_service_message("replication", body.peer_did, bundle))
        acknowledgement = ClipServiceMessage.model_validate(response)
        await authenticate_service_message(acknowledgement, "replicationAcknowledgement")
        if acknowledgement.actor_did != body.peer_did or acknowledgement.payload != {
            "digest": digest, "authorityDid": settings.DID_WEB_ID, "datasetId": body.dataset_id, "sequence": len(entries),
        }:
            raise ValueError("Replication acknowledgement does not match the sent bundle")
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    record = await session.get(ClipReplicationAck, (digest, body.peer_did))
    if record is None:
        session.add(ClipReplicationAck(digest=digest, peer_did=body.peer_did, acknowledgement_json=response, received_at=datetime.now(timezone.utc)))
    await session.commit()
    return response


@router.get("/status")
async def replication_status(session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    replicas = (await session.execute(select(ClipReplicaBundle))).scalars().all()
    acknowledgements = (await session.execute(select(ClipReplicationAck))).scalars().all()
    return {"replicas": [{"authorityDid": row.authority_did, "datasetId": row.dataset_id, "sequence": row.sequence, "digest": row.digest} for row in replicas],
        "acknowledgements": [row.acknowledgement_json for row in acknowledgements]}