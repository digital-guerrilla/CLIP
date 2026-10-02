"""Encrypted IFCX evidence, recipient key wrapping, receipts, retention and repair."""

import base64
from datetime import datetime, timedelta, timezone
import hashlib
from pathlib import Path
import shutil

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response
import httpx
import nacl.public
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.content_crypto import encrypt_fragments
from ..core.data_integrity import add_data_integrity_proof, base58btc_decode
from ..core.did import resolve_did_web_document, resolve_clip_service_endpoint, verify_clip_message_proof
from ..core.egress import request_json
from ..core.ifc_graph import IfcComponentAddress, flatten_ifc_layers
from ..core.ifcx_models import IfcxFile
from ..core.clip_protocol import ClipEvidenceManifest, ClipServiceMessage
from ..db.database import get_db
from ..db.orm_models import IfcDatasetRecord, ClipEvidenceRecord, ClipFragmentReceipt, ClipProjectPolicy
from ..core.project_access import can_read_project
from ..dependencies import get_key_manager, require_api_key
from ..federation.clip_replication import authenticate_service_message, digest_json, sign_service_message
from ..state import is_storage_opt_in

router = APIRouter(prefix="/clip/v1/evidence", tags=["documents"])
EVIDENCE_SCHEMA = "urn:clip:construction:evidence-reference:v1"


class EvidenceUpload(BaseModel):
    target: IfcComponentAddress
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(default="application/octet-stream", alias="mediaType")
    content: str
    recipients: list[str] = Field(default_factory=list, max_length=100)
    retention_seconds: int = Field(alias="retentionSeconds", ge=1, le=31536000)
    chunk_size: int = Field(default=1048576, alias="chunkSize", ge=1024, le=16777216)


class PeerRequest(BaseModel):
    peer_did: str = Field(alias="peerDid", pattern=r"^did:web:")


def fragment_path(evidence_id: str, index: int) -> Path:
    if len(evidence_id) != 64 or any(character not in "0123456789abcdef" for character in evidence_id) or index < 0:
        raise HTTPException(status_code=422, detail="Invalid evidence address")
    return Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted" / evidence_id / f"{index}.bin"


def check_retention(record: ClipEvidenceRecord) -> None:
    expires = record.expires_at
    if expires is not None:
        if expires.tzinfo is None:
            expires = expires.replace(tzinfo=timezone.utc)
        if expires <= datetime.now(timezone.utc):
            raise HTTPException(status_code=410, detail="Evidence retention expired")


async def evidence_record(evidence_id: str, session: AsyncSession) -> ClipEvidenceRecord:
    record = await session.get(ClipEvidenceRecord, evidence_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Unknown evidence manifest")
    check_retention(record)
    return record


def check_fragment(manifest: dict, index: int, content: bytes) -> dict:
    fragments = manifest["manifest"]["fragments"]
    item = next((item for item in fragments if item["index"] == index), None)
    if item is None or len(content) != item["size"] or hashlib.sha256(content).hexdigest() != item["digest"]:
        raise ValueError("Fragment does not match its signed manifest")
    return item


@router.post("/upload", status_code=201)
async def upload_evidence(body: EvidenceUpload, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    if body.target.authority_did != settings.DID_WEB_ID or body.target.component_schema_id != EVIDENCE_SCHEMA:
        raise HTTPException(status_code=403, detail="Evidence target must be a local IFCX evidence-reference component")
    dataset = await session.get(IfcDatasetRecord, (settings.DID_WEB_ID, body.target.dataset_id))
    if dataset is None:
        raise HTTPException(status_code=404, detail="Unknown evidence dataset")
    policy = await session.get(ClipProjectPolicy, (settings.DID_WEB_ID, body.target.dataset_id))
    if policy is not None:
        recipient_dids = sorted({settings.DID_WEB_ID, *policy.members})
        if set(body.recipients) - set(recipient_dids):
            raise HTTPException(status_code=403, detail="Evidence recipients must inherit project membership")
    else:
        recipient_dids = body.recipients
    if not recipient_dids:
        raise HTTPException(status_code=422, detail="Evidence requires recipients")
    graph = flatten_ifc_layers([IfcxFile.model_validate(dataset.file_json)], authority_did=settings.DID_WEB_ID, dataset_id=body.target.dataset_id)
    if body.target.entity_path not in graph.entities or EVIDENCE_SCHEMA not in graph.schemas:
        raise HTTPException(status_code=422, detail="Target entity and evidence-reference schema must exist before upload")
    try:
        content = base64.b64decode(body.content, validate=True)
        if not content or len(content) > settings.MAX_DOCUMENT_BYTES:
            raise ValueError("Evidence is empty or exceeds the configured size limit")
        key, manifest, fragments = encrypt_fragments(content, body.chunk_size)
        recipients = []
        for did in sorted(set(recipient_dids)):
            document = await resolve_did_web_document(did)
            agreement = set(document.get("keyAgreement", []))
            method = next((method for method in document.get("verificationMethod", []) if method.get("id") in agreement and method.get("controller") == did and method.get("type") == "Multikey"), None)
            if method is None:
                raise ValueError("Recipient has no supported key agreement key")
            decoded = base58btc_decode(method["publicKeyMultibase"])
            if len(decoded) != 34 or decoded[:2] != bytes.fromhex("ec01"):
                raise ValueError("Recipient key must be an X25519 Multikey")
            wrapped = nacl.public.SealedBox(nacl.public.PublicKey(decoded[2:])).encrypt(key)
            recipients.append({"did": did, "verificationMethod": method["id"], "wrappedKey": base64.b64encode(wrapped).decode("ascii")})
    except (ValueError, httpx.HTTPError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    expires = datetime.now(timezone.utc) + timedelta(seconds=body.retention_seconds)
    evidence_id = manifest.encrypted_content_sha256
    signed = add_data_integrity_proof({
        "@context": ["https://w3id.org/security/data-integrity/v2", {"@vocab": "urn:clip:protocol:"}],
        "evidenceId": evidence_id, "publisherDid": settings.DID_WEB_ID,
        "target": body.target.model_dump(mode="json", by_alias=True),
        "manifest": {"name": body.name, "mediaType": body.media_type, "expiresAt": expires.isoformat().replace("+00:00", "Z"),
            "contentSha256": manifest.content_sha256, "encryptedContentSha256": manifest.encrypted_content_sha256,
            "chunkSize": manifest.chunk_size, "fragmentCount": manifest.fragment_count, "recipients": recipients,
            "fragments": [{"index": fragment.index, "digest": hashlib.sha256(fragment.nonce + fragment.ciphertext).hexdigest(),
                "sha256": fragment.sha256, "size": len(fragment.nonce + fragment.ciphertext)} for fragment in fragments]},
    }, get_key_manager().private_key_bytes, verification_method=settings.DID_VERIFICATION_METHOD, proof_purpose="assertionMethod", created=datetime.now(timezone.utc))
    signed = ClipEvidenceManifest.model_validate(signed).model_dump(mode="json", by_alias=True)
    for fragment in fragments:
        path = fragment_path(evidence_id, fragment.index)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(fragment.nonce + fragment.ciphertext)
    session.add(ClipEvidenceRecord(evidence_id=evidence_id, publisher_did=settings.DID_WEB_ID, manifest_json=signed, expires_at=expires))
    await session.commit()
    return {"manifest": signed, "evidenceReference": {
        "evidenceId": evidence_id, "publisherDid": settings.DID_WEB_ID,
        "uri": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/evidence/{evidence_id}", "integrity": digest_json(signed),
    }}


@router.post("/fragments")
async def receive_fragment(message: ClipServiceMessage, session: AsyncSession = Depends(get_db)) -> dict:
    if not is_storage_opt_in(settings.ENCRYPTED_STORAGE_OPT_IN):
        raise HTTPException(status_code=403, detail="Encrypted storage is not enabled")
    if message.actor_did not in {value.strip() for value in settings.CLIP_TRUSTED_PUBLISHERS.split(",")}:
        raise HTTPException(status_code=403, detail="Evidence publisher is not trusted by this operator")
    try:
        document = await authenticate_service_message(message, "fragment")
        manifest = ClipEvidenceManifest.model_validate(message.payload["manifest"]).model_dump(mode="json", by_alias=True)
        if manifest["publisherDid"] != message.actor_did or not verify_clip_message_proof(manifest, document):
            raise ValueError("Invalid evidence publisher proof")
        evidence_id, index = manifest["evidenceId"], int(message.payload["index"])
        content = base64.b64decode(message.payload["content"], validate=True)
        item = check_fragment(manifest, index, content)
        expires = datetime.fromisoformat(manifest["manifest"]["expiresAt"].replace("Z", "+00:00"))
        if expires.tzinfo is None or expires <= datetime.now(timezone.utc):
            raise ValueError("Evidence retention expired")
    except (ValueError, KeyError, httpx.HTTPError) as error:
        raise HTTPException(status_code=401, detail=str(error)) from error
    path = fragment_path(evidence_id, index)
    root = Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted"
    used = sum(file.stat().st_size for file in root.rglob("*.bin")) if root.exists() else 0
    additional = len(content) - (path.stat().st_size if path.exists() else 0)
    if settings.ENCRYPTED_STORAGE_CAPACITY_BYTES and used + additional > settings.ENCRYPTED_STORAGE_CAPACITY_BYTES:
        raise HTTPException(status_code=507, detail="Encrypted storage capacity exceeded")
    existing = await session.get(ClipEvidenceRecord, evidence_id)
    if existing is not None and existing.manifest_json != manifest:
        raise HTTPException(status_code=409, detail="Evidence manifest cannot be rewritten")
    if existing is None:
        session.add(ClipEvidenceRecord(evidence_id=evidence_id, publisher_did=message.actor_did, manifest_json=manifest, expires_at=expires))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    receipt = sign_service_message("fragmentReceipt", message.actor_did, {
        "evidenceId": evidence_id, "index": index, "digest": item["digest"], "expiresAt": manifest["manifest"]["expiresAt"],
    })
    row = await session.get(ClipFragmentReceipt, (evidence_id, index, settings.DID_WEB_ID))
    if row is None:
        session.add(ClipFragmentReceipt(evidence_id=evidence_id, fragment_index=index, peer_did=settings.DID_WEB_ID, receipt_json=receipt))
    else:
        row.receipt_json = receipt
    await session.commit()
    return receipt


@router.post("/fragments/read")
async def read_replica_fragment(message: ClipServiceMessage, session: AsyncSession = Depends(get_db)) -> dict:
    try:
        await authenticate_service_message(message, "fragmentRead")
        record = await evidence_record(message.payload["evidenceId"], session)
        if message.actor_did != record.publisher_did:
            raise ValueError("Only the evidence publisher may request repair fragments")
        index = int(message.payload["index"])
        content = fragment_path(record.evidence_id, index).read_bytes()
        check_fragment(record.manifest_json, index, content)
    except (ValueError, KeyError, OSError, httpx.HTTPError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    return sign_service_message("fragment", message.actor_did, {"manifest": record.manifest_json, "index": index, "content": base64.b64encode(content).decode("ascii")})


@router.post("/{evidence_id}/replicate")
async def replicate_evidence(evidence_id: str, body: PeerRequest, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    record = await evidence_record(evidence_id, session)
    if record.publisher_did != settings.DID_WEB_ID:
        raise HTTPException(status_code=403, detail="Only the publisher may place evidence replicas")
    policy = await session.get(ClipProjectPolicy, (settings.DID_WEB_ID, record.manifest_json["target"]["datasetId"]))
    if not can_read_project(policy, actor_did=body.peer_did):
        raise HTTPException(status_code=403, detail="Evidence placement requires project access")
    receipts = []
    try:
        document = await resolve_did_web_document(body.peer_did)
        endpoint = await resolve_clip_service_endpoint(document, expected_did=body.peer_did, service_type="ClipEvidenceService", fragment="clip-evidence")
        for item in record.manifest_json["manifest"]["fragments"]:
            content = fragment_path(evidence_id, item["index"]).read_bytes()
            check_fragment(record.manifest_json, item["index"], content)
            response = await request_json("POST", endpoint, body=sign_service_message("fragment", body.peer_did, {"manifest": record.manifest_json, "index": item["index"], "content": base64.b64encode(content).decode("ascii")}))
            message = ClipServiceMessage.model_validate(response)
            await authenticate_service_message(message, "fragmentReceipt")
            if message.actor_did != body.peer_did or message.payload != {"evidenceId": evidence_id, "index": item["index"], "digest": item["digest"], "expiresAt": record.manifest_json["manifest"]["expiresAt"]}:
                raise ValueError("Fragment receipt does not match the placement")
            row = await session.get(ClipFragmentReceipt, (evidence_id, item["index"], body.peer_did))
            if row is None:
                session.add(ClipFragmentReceipt(evidence_id=evidence_id, fragment_index=item["index"], peer_did=body.peer_did, receipt_json=response))
            else:
                row.receipt_json = response
            receipts.append(response)
            await session.commit()
    except (ValueError, KeyError, OSError, httpx.HTTPError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    return {"receipts": receipts}


@router.post("/{evidence_id}/repair")
async def repair_evidence(evidence_id: str, body: PeerRequest, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    record = await evidence_record(evidence_id, session)
    if record.publisher_did != settings.DID_WEB_ID:
        raise HTTPException(status_code=403, detail="Only the publisher may repair evidence")
    repaired = []
    try:
        document = await resolve_did_web_document(body.peer_did)
        endpoint = await resolve_clip_service_endpoint(document, expected_did=body.peer_did, service_type="ClipEvidenceService", fragment="clip-evidence")
        for item in record.manifest_json["manifest"]["fragments"]:
            index = item["index"]
            path = fragment_path(evidence_id, index)
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest() == item["digest"]:
                continue
            response = await request_json("POST", f"{endpoint}/read", body=sign_service_message("fragmentRead", body.peer_did, {"evidenceId": evidence_id, "index": index}))
            message = ClipServiceMessage.model_validate(response)
            await authenticate_service_message(message, "fragment")
            if message.actor_did != body.peer_did or message.payload["manifest"] != record.manifest_json or message.payload["index"] != index:
                raise ValueError("Repair response does not match the pinned evidence")
            content = base64.b64decode(message.payload["content"], validate=True)
            check_fragment(record.manifest_json, index, content)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
            repaired.append(index)
    except (ValueError, KeyError, OSError, httpx.HTTPError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    return {"repaired": repaired}


@router.post("/retention/sweep")
async def sweep_retention(session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    rows = (await session.execute(select(ClipEvidenceRecord).where(ClipEvidenceRecord.expires_at <= datetime.now(timezone.utc)))).scalars().all()
    for row in rows:
        shutil.rmtree(fragment_path(row.evidence_id, 0).parent, ignore_errors=True)
        receipts = (await session.execute(select(ClipFragmentReceipt).where(ClipFragmentReceipt.evidence_id == row.evidence_id))).scalars().all()
        for receipt in receipts:
            await session.delete(receipt)
        await session.delete(row)
    await session.commit()
    return {"deleted": len(rows)}


@router.get("/{evidence_id}")
async def get_manifest(evidence_id: str, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> dict:
    return (await evidence_record(evidence_id, session)).manifest_json


@router.get("/{evidence_id}/fragments/{index}")
async def get_fragment(evidence_id: str, index: int, session: AsyncSession = Depends(get_db), _: None = Depends(require_api_key)) -> Response:
    record = await evidence_record(evidence_id, session)
    try:
        content = fragment_path(evidence_id, index).read_bytes()
        check_fragment(record.manifest_json, index, content)
    except (OSError, ValueError) as error:
        raise HTTPException(status_code=424, detail="Evidence fragment is missing or corrupt; repair required") from error
    return Response(content, media_type="application/octet-stream")