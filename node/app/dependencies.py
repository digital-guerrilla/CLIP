"""
FastAPI dependency providers.

- Key manager: singleton Ed25519 keypair for this node.
- API key guard: protects write endpoints.
"""

import secrets

from fastapi import Header, HTTPException

from .config import settings
from .core.crypto import NodeKeyManager

_key_manager: NodeKeyManager | None = None


def get_key_manager() -> NodeKeyManager:
    """
    FastAPI dependency — returns the node's singleton key manager.
    Loaded/created once on first call; subsequent calls return the same instance.
    """
    global _key_manager
    if _key_manager is None:
        _key_manager = NodeKeyManager.load_or_create(settings.PRIVATE_KEY_FILE)
    return _key_manager


def require_api_key(x_api_key: str = Header(default=None)) -> None:
    """
    FastAPI dependency — raises 401 unless the configured key is valid or
    unauthenticated demo access is explicitly enabled.
    Use as: `_: None = Depends(require_api_key)`
    """
    if not valid_api_key(x_api_key):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def valid_api_key(api_key: str | None) -> bool:
    return settings.CLIP_DEMO_OPEN_ACCESS or (
        bool(api_key) and secrets.compare_digest(api_key, settings.API_KEY)
    )
