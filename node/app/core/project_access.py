"""Project permissions inherited by every record in an authority-owned dataset."""

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.orm_models import ClipProjectPolicy


async def project_policy(session: AsyncSession, authority_did: str, dataset_id: str) -> ClipProjectPolicy | None:
    return await session.get(ClipProjectPolicy, (authority_did, dataset_id))


def can_read_project(policy: ClipProjectPolicy | None, *, local: bool = False, actor_did: str | None = None) -> bool:
    return (
        local
        or policy is None
        or policy.visibility == "public"
        or actor_did == policy.authority_did
        or actor_did in policy.members
    )


async def require_dataset_read(
    session: AsyncSession, authority_did: str, dataset_id: str, *, local: bool = False, actor_did: str | None = None,
) -> None:
    policy = await project_policy(session, authority_did, dataset_id)
    if not can_read_project(policy, local=local, actor_did=actor_did):
        raise HTTPException(status_code=404, detail="Dataset is not available")