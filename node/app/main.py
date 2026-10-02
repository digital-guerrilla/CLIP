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
    did as did_api,
    clip_network as clip_network_api,
    ifc_transactions,
    clip_replication,
    clip_evidence,
    ifc_imports,
    clip_projects,
    clip_supply_chain,
    node_info,
    ui as ui_router,
)
from .config import settings
from .db.database import close_db, init_db
from .dependencies import get_key_manager
from .federation import clip_gossip as clip_gossip_engine
from .state import is_offline
from .core.egress import decode_json

# Always reachable even while the demo kill-switch is engaged, so a node can be brought back online.
OFFLINE_EXEMPT_PATH = "/clip/v1/node/offline"


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

    gossip_task = None
    if settings.CLIP_GOSSIP_ENABLED:
        await clip_gossip_engine.bootstrap_did_peers()
        gossip_task = asyncio.create_task(clip_gossip_engine.clip_gossip_loop(km))

    yield

    # Graceful shutdown
    if gossip_task is not None:
        gossip_task.cancel()
        try:
            await gossip_task
        except asyncio.CancelledError:
            pass
    await close_db()


app = FastAPI(
    title="CLIP Node",
    description=(
        "IFCX composition, authority-governed transactions, DID-authenticated "
        "federation and encrypted construction evidence. No v3 compatibility."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_tags=[
        {"name": "did", "description": "DID controller documents and service endpoints."},
        {"name": "node", "description": "Node identity, access checks, storage status, and demo operational controls."},
        {"name": "imports", "description": "IFC4.3 and COBie conversion into native IFCX datasets."},
        {"name": "projects", "description": "Project setup and inherited organisation permissions."},
        {"name": "supply-chain", "description": "Authority-owned product sourcing, catalogue revisions, evidence and scoped signed handover."},
        {"name": "documents", "description": "Document upload, retrieval, and encrypted fragment handling."},
        {"name": "replication", "description": "Replication job visibility and retry operations."},
        {"name": "ifc", "description": "IFC graph transaction proposals and authority decisions using IFCX serialization."},
        {"name": "clip-gossip", "description": "DID-authenticated peer membership separate from the IFCX asset graph."},
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
    if request.method in {"POST", "PUT", "PATCH"}:
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > settings.CLIP_MAX_SERVICE_BYTES:
                return JSONResponse(status_code=413, content={"detail": "Request exceeds the configured byte limit"})
        request._body = bytes(content)
        if request.headers.get("content-type", "").split(";")[0] == "application/json":
            try:
                decode_json(request._body)
            except (ValueError, UnicodeDecodeError):
                return JSONResponse(status_code=400, content={"detail": "Invalid JSON or duplicate members"})
    return await call_next(request)


app.include_router(did_api.router)

app.include_router(clip_network_api.router)
app.include_router(node_info.router)
app.include_router(ifc_transactions.router)
app.include_router(clip_replication.router)
app.include_router(clip_evidence.router)
app.include_router(ifc_imports.router)
app.include_router(clip_projects.router)
app.include_router(clip_projects.graph_router)
app.include_router(clip_supply_chain.router)
app.include_router(ui_router.router)


@app.get("/", include_in_schema=False)
async def root():
    return {
        "name": "CLIP Node",
        "node_id": settings.NODE_DOMAIN,
        "protocol_version": "clip/v1",
        "graph_api": "/ifc/v1",
        "docs": "/docs",
        "well_known": "/.well-known/did.json",
        "ui": "/ui",
    }
