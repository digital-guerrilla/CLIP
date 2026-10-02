"""Validated IFC4.3 and COBie conversion before authority dataset registration."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..config import settings
from ..dependencies import require_api_key
from ..imports.ifcx import map_cobie, map_ifc43, construction_schemas

router = APIRouter(prefix="/ifc/v1/imports", tags=["imports"])


class ConstructionImport(BaseModel):
    dataset_id: str = Field(alias="datasetId", min_length=1)
    content: str = Field(max_length=25 * 1024 * 1024)
    type_content: str = Field(default="", alias="typeContent", max_length=25 * 1024 * 1024)


@router.get("/schemas")
async def get_construction_schemas() -> dict:
    return construction_schemas()


@router.post("/cobie")
async def import_cobie(body: ConstructionImport, _: None = Depends(require_api_key)) -> dict:
    try:
        file = await run_in_threadpool(map_cobie, body.content, body.type_content, dataset_id=body.dataset_id, author=settings.DID_WEB_ID)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return file.model_dump(mode="json", by_alias=True)


@router.post("/ifc43")
async def import_ifc43(body: ConstructionImport, _: None = Depends(require_api_key)) -> dict:
    try:
        file = await run_in_threadpool(map_ifc43, body.content, dataset_id=body.dataset_id, author=settings.DID_WEB_ID)
    except (ValueError, RuntimeError) as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return file.model_dump(mode="json", by_alias=True)