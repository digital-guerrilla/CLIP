"""Content-addressed document upload, retrieval, and peer replication."""

import asyncio
import base64
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from fastapi.responses import FileResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.content_crypto import (
    EncryptedFragment,
    EncryptedManifest,
    decode_key,
    decrypt_fragments,
    encode_key,
    encrypt_fragments,
    combine_key,
    split_key,
)
from ..core.crypto import NodeKeyManager
from ..core.crypto import canonicalize
from ..core.models import DocumentRef
from ..db.database import get_db
from ..db.orm_models import Asset
from ..dependencies import get_key_manager, require_api_key, valid_api_key
from ..state import is_storage_opt_in
from .assets import orm_to_record, replace_asset_subject, replication_endpoints

router = APIRouter(prefix="/v3/documents", tags=["documents"])


def _paths(sha256: str) -> tuple[Path, Path]:
    root = Path(settings.DOCUMENT_STORAGE_DIR)
    return root / sha256, root / f"{sha256}.json"


def _encrypted_paths(sha256: str) -> tuple[Path, Path]:
    root = Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted" / sha256
    return root / "manifest.json", root


def _store(content: bytes, sha256: str, name: str, media_type: str) -> None:
    content_path, metadata_path = _paths(sha256)
    content_path.parent.mkdir(parents=True, exist_ok=True)
    content_path.write_bytes(content)
    metadata_path.write_text(
        json.dumps({"name": name, "media_type": media_type}, ensure_ascii=True),
        encoding="utf-8",
    )


async def _read_upload(request: Request) -> bytes:
    declared_size = int(request.headers.get("content-length", "0") or 0)
    if declared_size > settings.MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the configured size limit")
    content = await request.body()
    if not content:
        raise HTTPException(status_code=422, detail="Document is empty")
    if len(content) > settings.MAX_DOCUMENT_BYTES:
        raise HTTPException(status_code=413, detail="Document exceeds the configured size limit")
    return content


@router.post("/upload/{authority}/{record_uuid}", response_model=DocumentRef, status_code=201)
async def upload_document(
    authority: str,
    record_uuid: str,
    request: Request,
    file_name: str = Header(..., alias="x-file-name"),
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> DocumentRef:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    row = (await db.execute(select(Asset).where(Asset.id == record_id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Record not found: {record_id}")
    if not row.is_authoritative or authority != key_manager.public_key_multibase:
        raise HTTPException(status_code=403, detail="This node is not authoritative for the record")

    content = await _read_upload(request)
    sha256 = hashlib.sha256(content).hexdigest()
    media_type = request.headers.get("content-type") or "application/octet-stream"
    name = Path(file_name).name or "document"
    _store(content, sha256, name, media_type)

    document = DocumentRef(
        url=f"{settings.NODE_API_BASE.rstrip('/')}/v3/documents/{sha256}",
        media_type=media_type,
        sha256=sha256,
        name=name,
    )
    record = orm_to_record(row)
    if not any(item.sha256 == sha256 for item in record.subject.documents):
        subject = record.subject.model_copy(
            update={"documents": [*record.subject.documents, document]}
        )
        await replace_asset_subject(
            row=row,
            subject=subject,
            controller=record.controller,
            db=db,
            key_manager=key_manager,
        )
    asyncio.create_task(_push_document_to_peers(content, document, record.availability))
    return document


@router.post("/encrypted-upload/{authority}/{record_uuid}")
async def upload_encrypted_document(
    authority: str,
    record_uuid: str,
    request: Request,
    file_name: str = Header(..., alias="x-file-name"),
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> dict:
    record_id = f"clip://{settings.NODE_DOMAIN}/{authority}/{record_uuid}"
    row = (await db.execute(select(Asset).where(Asset.id == record_id))).scalar_one_or_none()
    if row is None or not row.is_authoritative or authority != key_manager.public_key_multibase:
        raise HTTPException(status_code=403, detail="This node is not authoritative for the record")
    content = await _read_upload(request)
    chunk_size = int(request.headers.get("x-chunk-size", str(1024 * 1024)))
    key, manifest, fragments = encrypt_fragments(content, chunk_size)
    retention_seconds = int(request.headers.get(
        "x-retention-seconds",
        str(settings.ENCRYPTED_STORAGE_DEFAULT_RETENTION_SECONDS),
    ))
    if retention_seconds < 0:
        raise HTTPException(status_code=422, detail="Retention must not be negative")
    expires_at = (
        datetime.now(timezone.utc) + timedelta(seconds=retention_seconds)
        if retention_seconds else None
    )
    share_count = int(request.headers.get("x-key-share-count", "1"))
    threshold = int(request.headers.get("x-key-threshold", "1"))
    if share_count == 1 and threshold == 1:
        key_shares = [key]
    else:
        try:
            key_shares = split_key(key, share_count, threshold)
        except ValueError as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
    manifest_path, fragment_root = _encrypted_paths(manifest.content_sha256)
    fragment_root.mkdir(parents=True, exist_ok=True)
    for fragment in fragments:
        (fragment_root / f"{fragment.index}.bin").write_bytes(fragment.nonce + fragment.ciphertext)
    manifest_path.write_text(json.dumps({
        "content_sha256": manifest.content_sha256,
        "encrypted_content_sha256": manifest.encrypted_content_sha256,
        "chunk_size": manifest.chunk_size,
        "fragment_count": manifest.fragment_count,
        "key_share_count": share_count,
        "key_threshold": threshold,
        "expires_at": expires_at.isoformat() if expires_at else None,
        "fragments": list(manifest.fragments),
        "name": Path(file_name).name or "document",
        "media_type": request.headers.get("content-type") or "application/octet-stream",
    }, ensure_ascii=True), encoding="utf-8")

    record = orm_to_record(row)
    document = DocumentRef(
        url=f"{settings.NODE_API_BASE.rstrip('/')}/v3/documents/encrypted/{manifest.content_sha256}",
        media_type=request.headers.get("content-type") or "application/octet-stream",
        sha256=manifest.content_sha256,
        name=Path(file_name).name or "document",
    )
    if not any(item.sha256 == document.sha256 for item in record.subject.documents):
        subject = record.subject.model_copy(update={"documents": [*record.subject.documents, document]})
        await replace_asset_subject(
            row=row,
            subject=subject,
            controller=record.controller,
            db=db,
            key_manager=key_manager,
        )
    asyncio.create_task(_push_encrypted_fragments(
        manifest=manifest,
        fragments=fragments,
        key_shares=key_shares,
        key_manager=key_manager,
        origin_routing_host=settings.NODE_DOMAIN,
    ))
    return {
        "document": document.model_dump(mode="json"),
        "manifest": json.loads(manifest_path.read_text(encoding="utf-8")),
        "encryption_key": encode_key(key),
        "key_shares": [encode_key(share) for share in key_shares],
        "key_delivery": "prototype_one_time_response; use recipient key wrapping in production",
    }


@router.post("/encrypted-replica/{sha256}/{fragment_index}", status_code=202)
async def receive_encrypted_fragment(
    sha256: str,
    fragment_index: int,
    request: Request,
) -> dict:
    if not is_storage_opt_in(settings.ENCRYPTED_STORAGE_OPT_IN):
        raise HTTPException(status_code=403, detail="This node has not opted into encrypted storage")
    origin_routing_host = request.headers.get("x-origin-routing-host")
    origin_authority = request.headers.get("x-origin-authority")
    signature = request.headers.get("x-fragment-signature")
    fragment_sha256 = request.headers.get("x-fragment-sha256")
    encoded_manifest = request.headers.get("x-encrypted-manifest")
    if not all((origin_routing_host, origin_authority, signature, fragment_sha256, encoded_manifest)):
        raise HTTPException(status_code=400, detail="Fragment provenance headers are required")
    content = await request.body()
    if settings.ENCRYPTED_STORAGE_CAPACITY_BYTES:
        encrypted_root = Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted"
        used = sum(path.stat().st_size for path in encrypted_root.rglob("*") if path.is_file()) if encrypted_root.exists() else 0
        if used + len(content) > settings.ENCRYPTED_STORAGE_CAPACITY_BYTES:
            raise HTTPException(status_code=507, detail="Encrypted storage capacity exceeded")
    if hashlib.sha256(content[24:]).hexdigest() != fragment_sha256:
        raise HTTPException(status_code=400, detail="Fragment digest does not match content")
    try:
        manifest = json.loads(base64.urlsafe_b64decode(encoded_manifest + "=" * (-len(encoded_manifest) % 4)))
    except (ValueError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid encrypted manifest") from error
    if manifest.get("content_sha256") != sha256 or fragment_index not in [int(item["index"]) for item in manifest.get("fragments", [])]:
        raise HTTPException(status_code=400, detail="Fragment does not belong to manifest")
    if manifest.get("expires_at") and datetime.fromisoformat(manifest["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Encrypted document retention has expired")
    payload = {"sha256": sha256, "fragment_index": fragment_index, "fragment_sha256": fragment_sha256}
    try:
        from ..federation.resolver import fetch_well_known
        descriptor = await fetch_well_known(origin_routing_host, settings.FEDERATION_TIMEOUT)
        method = next(item for item in descriptor.verification_methods if "record" in item.purposes)
        verified = descriptor.authority == origin_authority and NodeKeyManager.verify(
            payload, signature, method.public_key_base64
        )
    except (ValueError, StopIteration):
        verified = False
    if not verified:
        raise HTTPException(status_code=400, detail="Fragment provenance verification failed")
    _, fragment_root = _encrypted_paths(sha256)
    fragment_root.mkdir(parents=True, exist_ok=True)
    (fragment_root / f"{fragment_index}.bin").write_bytes(content)
    (fragment_root / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=True), encoding="utf-8")
    share = request.headers.get("x-key-share")
    if share:
        (fragment_root / f"key-share-{fragment_index}.txt").write_text(share, encoding="ascii")
    storage_receipt = {
        "sha256": sha256,
        "fragment_index": fragment_index,
        "fragment_sha256": fragment_sha256,
        "stored_by": settings.NODE_DOMAIN,
        "stored_at": datetime.now(timezone.utc).isoformat(),
    }
    return {
        "status": "accepted",
        "sha256": sha256,
        "fragment_index": fragment_index,
        "storage_receipt": {
            **storage_receipt,
            "authority": get_key_manager().public_key_multibase,
            "signature": get_key_manager().sign_record(storage_receipt),
        },
    }


@router.get("/encrypted/{sha256}")
async def get_encrypted_document(
    sha256: str,
    x_document_key: str | None = Header(default=None),
    x_document_key_shares: str | None = Header(default=None),
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> Response:
    if not valid_api_key(x_api_key):
        raise HTTPException(status_code=403, detail="Encrypted document access requires authorization")
    manifest_path, fragment_root = _encrypted_paths(sha256)
    if not manifest_path.is_file():
        raise HTTPException(status_code=404, detail="Encrypted document is not stored on this node")
    if not x_document_key and not x_document_key_shares:
        raise HTTPException(status_code=401, detail="Document key or key shares are required")
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if metadata.get("expires_at") and datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(timezone.utc):
        raise HTTPException(status_code=410, detail="Encrypted document retention has expired")
    fragments = []
    for item in metadata["fragments"]:
        payload = (fragment_root / f"{item['index']}.bin").read_bytes()
        fragments.append(EncryptedFragment(
            index=int(item["index"]),
            nonce=payload[:24],
            ciphertext=payload[24:],
            sha256=str(item["sha256"]),
        ))
    manifest = EncryptedManifest(
        content_sha256=metadata["content_sha256"],
        encrypted_content_sha256=metadata["encrypted_content_sha256"],
        chunk_size=int(metadata["chunk_size"]),
        fragment_count=int(metadata["fragment_count"]),
        fragments=tuple(metadata["fragments"]),
    )
    try:
        if x_document_key:
            key = decode_key(x_document_key)
        else:
            shares = [decode_key(value.strip()) for value in x_document_key_shares.split(",") if value.strip()]
            key = combine_key(shares, int(metadata.get("key_threshold", 1)))
        content = decrypt_fragments(key, manifest, fragments)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return Response(content=content, media_type=metadata.get("media_type", "application/octet-stream"))


@router.post("/encrypted-cleanup")
async def cleanup_expired_encrypted_documents(
    _: None = Depends(require_api_key),
) -> dict:
    import shutil

    root = Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted"
    removed = 0
    if root.exists():
        for manifest_path in root.glob("*/manifest.json"):
            metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
            if metadata.get("expires_at") and datetime.fromisoformat(metadata["expires_at"]) <= datetime.now(timezone.utc):
                shutil.rmtree(manifest_path.parent)
                removed += 1
    return {"removed": removed}


@router.post("/encrypted-revoke/{sha256}", status_code=202)
async def revoke_encrypted_document(
    sha256: str,
    request: Request,
) -> dict:
    origin_routing_host = request.headers.get("x-origin-routing-host")
    origin_authority = request.headers.get("x-origin-authority")
    signature = request.headers.get("x-revocation-signature")
    reason = request.headers.get("x-revocation-reason", "unspecified")
    if not all((origin_routing_host, origin_authority, signature)):
        raise HTTPException(status_code=400, detail="Revocation provenance headers are required")
    payload = {"sha256": sha256, "action": "revoke", "reason": reason}
    try:
        from ..federation.resolver import fetch_well_known
        descriptor = await fetch_well_known(origin_routing_host, settings.FEDERATION_TIMEOUT)
        method = next(item for item in descriptor.verification_methods if "record" in item.purposes)
        verified = descriptor.authority == origin_authority and NodeKeyManager.verify(
            payload, signature, method.public_key_base64
        )
    except (ValueError, StopIteration):
        verified = False
    if not verified:
        raise HTTPException(status_code=400, detail="Revocation provenance verification failed")
    _, fragment_root = _encrypted_paths(sha256)
    if fragment_root.exists():
        import shutil
        shutil.rmtree(fragment_root)
    return {"status": "revoked", "sha256": sha256, "reason": reason}


@router.post("/replica/{sha256}", status_code=202)
async def receive_document_replica(
    sha256: str,
    request: Request,
    file_name: str = Header("document", alias="x-file-name"),
) -> dict:
    if len(sha256) != 64 or any(char not in "0123456789abcdef" for char in sha256):
        raise HTTPException(status_code=422, detail="Invalid SHA-256 digest")
    content = await _read_upload(request)
    if hashlib.sha256(content).hexdigest() != sha256:
        raise HTTPException(status_code=400, detail="Document digest does not match content")
    media_type = request.headers.get("content-type") or "application/octet-stream"
    _store(content, sha256, Path(file_name).name or "document", media_type)
    return {"status": "accepted", "sha256": sha256}


@router.get("/{sha256}")
async def get_document(
    sha256: str,
    x_api_key: str | None = Header(default=None),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    content_path, metadata_path = _paths(sha256)
    if not content_path.is_file():
        raise HTTPException(status_code=404, detail="Document is not stored on this node")
    rows = (await db.execute(select(Asset))).scalars()
    linked_records = [
        orm_to_record(row)
        for row in rows
        if any(item.sha256 == sha256 for item in orm_to_record(row).subject.documents)
    ]
    if linked_records and all(
        record.availability.visibility == "restricted" for record in linked_records
    ) and not valid_api_key(x_api_key):
        raise HTTPException(status_code=403, detail="Document access is restricted")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8")) if metadata_path.is_file() else {}
    return FileResponse(
        content_path,
        media_type=metadata.get("media_type", "application/octet-stream"),
        filename=metadata.get("name", "document"),
        content_disposition_type="inline",
    )


async def _push_document_to_peers(content: bytes, document: DocumentRef, availability) -> None:
    try:
        import httpx
        from ..federation.gossip import get_all_peers

        peers = [peer for peer in await get_all_peers() if peer.status == "alive"]
        endpoints = replication_endpoints(availability, peers)
        headers = {
            "content-type": document.media_type,
            "x-file-name": document.name or "document",
        }
        async with httpx.AsyncClient(timeout=settings.FEDERATION_TIMEOUT) as client:
            for endpoint in endpoints:
                try:
                    await client.post(
                        f"{endpoint}/v3/documents/replica/{document.sha256}",
                        content=content,
                        headers=headers,
                    )
                except Exception:
                    continue
    except Exception:
        return


async def _push_encrypted_fragments(
    *,
    manifest: EncryptedManifest,
    fragments: list[EncryptedFragment],
    key_shares: list[bytes],
    key_manager: NodeKeyManager,
    origin_routing_host: str,
) -> None:
    try:
        import httpx
        from ..federation.gossip import get_all_peers

        peers = [peer for peer in await get_all_peers() if peer.status == "alive"]
        manifest_path, _ = _encrypted_paths(manifest.content_sha256)
        manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
        encoded_manifest = base64.urlsafe_b64encode(
            json.dumps(manifest_data, separators=(",", ":"), ensure_ascii=True).encode()
        ).decode().rstrip("=")
        async with httpx.AsyncClient(timeout=settings.FEDERATION_TIMEOUT) as client:
            storage_peers = []
            for peer in peers:
                try:
                    info = await client.get(f"{peer.endpoint.rstrip('/')}/v3/node/info")
                    if info.is_success and info.json().get("encrypted_storage_opt_in"):
                        storage_peers.append(peer)
                except Exception:
                    continue
            for index, fragment in enumerate(fragments):
                if not storage_peers:
                    break
                peer = storage_peers[index % len(storage_peers)]
                payload = {
                    "sha256": manifest.content_sha256,
                    "fragment_index": fragment.index,
                    "fragment_sha256": fragment.sha256,
                }
                headers = {
                    "x-origin-routing-host": origin_routing_host,
                    "x-origin-authority": key_manager.public_key_multibase,
                    "x-fragment-signature": key_manager.sign_record(payload),
                    "x-fragment-sha256": fragment.sha256,
                    "x-encrypted-manifest": encoded_manifest,
                    "content-type": "application/octet-stream",
                }
                if key_shares:
                    headers["x-key-share"] = encode_key(key_shares[index % len(key_shares)])
                await client.post(
                    f"{peer.endpoint.rstrip('/')}/v3/documents/encrypted-replica/{manifest.content_sha256}/{fragment.index}",
                    content=fragment.nonce + fragment.ciphertext,
                    headers=headers,
                )
    except Exception:
        return
