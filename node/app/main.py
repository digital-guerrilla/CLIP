"""
CLIP Node — FastAPI application entry point.

Start with:
  uvicorn app.main:app --reload --port 8000
"""

import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import JSONResponse

from .api import (
    assets,
    bulk_assets,
    bulk_relationships,
    discovery,
    document_store,
    federation,
    gossip as gossip_api,
    lifecycle,
    node_info,
    relationships,
    query,
    replication,
    resolve,
    ui as ui_router,
)
from .config import settings
from .db.database import close_db, init_db
from .dependencies import get_key_manager
from .federation import gossip as gossip_engine
from .state import is_offline

# Always reachable even while the demo kill-switch is engaged, so a node can be brought back online.
OFFLINE_EXEMPT_PATH = "/v3/node/offline"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    del _app
    # Initialise database (creates tables if they don't exist)
    await init_db(settings.DATABASE_URL)

    # Pre-load / create the node keypair so any errors surface at startup
    km = get_key_manager()
    print(f"[clip] Node ID:    {settings.NODE_DOMAIN}")
    print(f"[clip] API base:   {settings.NODE_API_BASE}")
    print(f"[clip] Public key: {km.public_key_b64}")
    print(f"[clip] Role:       {settings.NODE_ROLE}")

    # Bootstrap gossip membership from seed peers
    await gossip_engine.bootstrap_peers()

    # Start background gossip loop (no-op for producer-role nodes)
    gossip_task = asyncio.create_task(gossip_engine.gossip_loop())

    yield

    # Graceful shutdown
    gossip_task.cancel()
    try:
        await gossip_task
    except asyncio.CancelledError:
        pass
    await close_db()


app = FastAPI(
    title="CLIP Node",
    description=(
        "Distributed Asset Identification Node — "
        "federated, cryptographically-signed product/asset tracking. "
        "This API exposes the authoritative record, federation, proof verification, "
        "and document workflows used by the CLIP v3 protocol."
    ),
    version="3.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "discovery", "description": "Signed authority descriptor and protocol discovery endpoints."},
        {"name": "node", "description": "Node identity, access checks, storage status, and demo operational controls."},
        {"name": "records", "description": "Authoritative record creation, listing, querying, and version history."},
        {"name": "query", "description": "Authorized asset lookup and filtered record queries."},
        {"name": "relationships", "description": "Signed relationship proposals, acceptance, and lifecycle assertions."},
        {"name": "imports", "description": "Bulk COBie/CSV/JSON asset import jobs and status tracking."},
        {"name": "resolution", "description": "Verified graph resolution and root-object dependency traversal."},
        {"name": "federation", "description": "Peer synchronization and incoming replicated record verification."},
        {"name": "documents", "description": "Document upload, retrieval, and encrypted fragment handling."},
        {"name": "gossip", "description": "Peer membership and gossip digest exchange for distributed discovery."},
        {"name": "replication", "description": "Replication job visibility and retry operations."},
        {"name": "ui", "description": "Web console assets for the local operations dashboard."},
    ],
)


def custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema

    openapi_schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
        tags=app.openapi_tags,
    )
    openapi_schema.setdefault("components", {})
    openapi_schema["components"].setdefault("securitySchemes", {})
    openapi_schema["components"]["securitySchemes"]["x_api_key"] = {
        "type": "apiKey",
        "in": "header",
        "name": "x-api-key",
        "description": "Local node API key required for writes and restricted view access.",
    }
    openapi_schema["security"] = [{"x_api_key": []}]
    app.openapi_schema = openapi_schema
    return app.openapi_schema


app.openapi = custom_openapi

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "PUT"],
    allow_headers=["*"],
)


@app.middleware("http")
async def offline_kill_switch(request: Request, call_next):
    if is_offline() and request.url.path != OFFLINE_EXEMPT_PATH:
        return JSONResponse(status_code=503, content={"detail": "Node is offline (simulated)"})
    return await call_next(request)


# /.well-known/clip/server  (no prefix)
app.include_router(discovery.router)

app.include_router(assets.router)
app.include_router(bulk_assets.router)
app.include_router(document_store.router)
app.include_router(resolve.router)
app.include_router(federation.router)
app.include_router(gossip_api.router)
app.include_router(node_info.router)
app.include_router(relationships.router)
app.include_router(bulk_relationships.router)
app.include_router(query.router)
app.include_router(lifecycle.router)
app.include_router(replication.router)
app.include_router(ui_router.router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "CLIP Node",
        "node_id": settings.NODE_DOMAIN,
        "protocol_version": "3.0",
        "docs": "/docs",
        "well_known": "/.well-known/clip/server",
        "ui": "/ui",
    }
