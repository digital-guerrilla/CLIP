"""CLIP v3 node information endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..core.models import NodeInfo
from ..dependencies import get_key_manager, require_api_key
from ..state import is_offline, is_storage_opt_in, set_offline, set_storage_opt_in
from pathlib import Path

router = APIRouter(prefix="/v3/node", tags=["node"])


@router.get("/info", response_model=NodeInfo)
async def get_node_info(
    key_manager: NodeKeyManager = Depends(get_key_manager),
) -> NodeInfo:
    return NodeInfo(
        routing_host=settings.NODE_DOMAIN,
        authority=key_manager.public_key_multibase,
        role=settings.NODE_ROLE,
        encrypted_storage_opt_in=is_storage_opt_in(settings.ENCRYPTED_STORAGE_OPT_IN),
        encrypted_storage_capacity_bytes=settings.ENCRYPTED_STORAGE_CAPACITY_BYTES,
    )


@router.get("/access")
async def check_node_access(_: None = Depends(require_api_key)) -> dict:
    return {"authorized": True, "routing_host": settings.NODE_DOMAIN, "role": settings.NODE_ROLE}


@router.get("/storage")
async def get_storage_status(_: None = Depends(require_api_key)) -> dict:
    root = Path(settings.DOCUMENT_STORAGE_DIR) / "encrypted"
    files = [path for path in root.rglob("*") if path.is_file()] if root.exists() else []
    used = sum(path.stat().st_size for path in files)
    fragments = sum(path.name.endswith(".bin") for path in files)
    capacity = settings.ENCRYPTED_STORAGE_CAPACITY_BYTES
    return {
        "opted_in": is_storage_opt_in(settings.ENCRYPTED_STORAGE_OPT_IN),
        "capacity_bytes": capacity,
        "used_bytes": used,
        "available_bytes": max(capacity - used, 0) if capacity else None,
        "fragment_count": fragments,
    }


class StorageOptInRequest(BaseModel):
    opt_in: bool


@router.post("/storage/opt-in")
async def toggle_storage_opt_in(body: StorageOptInRequest, _: None = Depends(require_api_key)) -> dict:
    """Demo privacy control: let a node opt in/out of holding encrypted document fragments for peers."""
    set_storage_opt_in(body.opt_in)
    return {"opted_in": is_storage_opt_in(settings.ENCRYPTED_STORAGE_OPT_IN)}


class OfflineToggleRequest(BaseModel):
    offline: bool


@router.get("/offline")
async def get_offline_state() -> dict:
    return {"offline": is_offline()}


@router.post("/offline")
async def toggle_offline(body: OfflineToggleRequest, _: None = Depends(require_api_key)) -> dict:
    """Demo kill-switch: simulate this node being unreachable to peers and clients."""
    set_offline(body.offline)
    return {"offline": is_offline()}
