"""IFCX authority operations console."""

import os

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

router = APIRouter(tags=["ui"])

_STATIC = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "static"))


def _f(name: str) -> str:
    return os.path.join(_STATIC, name)


@router.get("/ui/static/{name}", include_in_schema=False)
async def ui_asset(name: str):
    assets = {"dashboard.css": "text/css", "dashboard.js": "text/javascript", "d3.min.js": "text/javascript", "lucide.min.js": "text/javascript"}
    if name not in assets:
        raise HTTPException(status_code=404, detail="Unknown interface asset")
    return FileResponse(_f(name), media_type=assets[name])


@router.get("/ui", include_in_schema=False)
async def ui_root():
    return FileResponse(_f("dashboard.html"), media_type="text/html")


@router.get("/ui/manufacturer", include_in_schema=False)
async def manufacturer_ui():
    return FileResponse(_f("dashboard.html"), media_type="text/html")


@router.get("/ui/client", include_in_schema=False)
async def client_ui():
    return FileResponse(_f("dashboard.html"), media_type="text/html")
