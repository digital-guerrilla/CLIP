"""Cross-authority relationship proposal and acceptance workflow."""

import asyncio
import hashlib
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..core.crypto import NodeKeyManager, canonical_sha256, canonicalize
from ..core.guid import parse_clip
from ..core.models import (
    AssetRelationship,
    Proof,
    RelationshipProposalRequest,
    RelationshipState,
)
from ..db.database import get_db
from ..db.orm_models import Asset, AssetHistory
from ..dependencies import get_key_manager, require_api_key
from ..federation.resolver import fetch_well_known
from .assets import _push_to_peers, _signed_record, _utc, orm_to_record, record_to_storage

router = APIRouter(prefix="/v3/relationships", tags=["relationships"])


def _unsigned_relationship(relationship: AssetRelationship) -> dict:
    return {
        key: value
        for key, value in relationship.model_dump(mode="json").items()
        if key != "proofs"
    }


@router.post("/proposals", response_model=AssetRelationship, status_code=201)
async def propose_relationship(
    body: RelationshipProposalRequest,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> AssetRelationship:
    target = parse_clip(body.target)
    if target.authority_key_fingerprint != key_manager.public_key_multibase:
        raise HTTPException(status_code=403, detail="This node is not authoritative for the target")
    target_row = (await db.execute(select(Asset).where(Asset.id == body.target))).scalar_one_or_none()
    if target_row is None or not target_row.is_authoritative:
        raise HTTPException(status_code=404, detail="Target record is not authoritative on this node")

    now = datetime.now(timezone.utc)
    controller = orm_to_record(target_row).controller
    target_integrity = body.target_integrity
    if body.relation_type.value == "defines_type":
        target_integrity = target_integrity.model_copy(update={
            "mode": "snapshot",
            "version": target_row.version,
            "sha256": canonical_sha256(target_row.record_json),
        })
    provisional = AssetRelationship(
        relationship_id=str(uuid.uuid4()),
        source=body.source,
        target=body.target,
        role=body.role,
        relation_type=body.relation_type,
        asserted_by=key_manager.public_key_multibase,
        asserted_at=now,
        effective_from=body.effective_from,
        effective_to=body.effective_to,
        state=RelationshipState.PROPOSED,
        target_integrity=target_integrity,
        claims=body.claims,
        references=body.references,
        evidence=body.evidence,
        proofs=[Proof(
            verification_method=f"{controller}#clip-record-signing",
            created=now,
            proof_purpose="relationship-assertion",
            proof_value="unsigned-placeholder",
        )],
    )
    signature = key_manager.sign_record(_unsigned_relationship(provisional))
    document = provisional.model_dump(mode="json")
    document["proofs"][0]["proof_value"] = signature
    return AssetRelationship.model_validate(document)


@router.post("/accept", response_model=AssetRelationship)
async def accept_relationship(
    proposal: AssetRelationship,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> AssetRelationship:
    return await _accept_one(proposal, db, key_manager)


class AcceptBatchRequest(BaseModel):
    # Cascading acceptance: e.g. a contractor's installation proposal plus every
    # nested component proposal it covers, accepted by the owner in one call.
    proposals: list[AssetRelationship] = Field(min_length=1)


class AcceptBatchError(BaseModel):
    relationship_id: str
    detail: str


class AcceptBatchResponse(BaseModel):
    accepted: list[AssetRelationship]
    errors: list[AcceptBatchError]


@router.post("/accept-batch", response_model=AcceptBatchResponse)
async def accept_relationships_batch(
    body: AcceptBatchRequest,
    db: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    _: None = Depends(require_api_key),
) -> AcceptBatchResponse:
    accepted: list[AssetRelationship] = []
    errors: list[AcceptBatchError] = []
    for proposal in body.proposals:
        try:
            accepted.append(await _accept_one(proposal, db, key_manager))
        except HTTPException as exc:
            errors.append(AcceptBatchError(relationship_id=proposal.relationship_id, detail=str(exc.detail)))
    return AcceptBatchResponse(accepted=accepted, errors=errors)


async def _accept_one(
    proposal: AssetRelationship,
    db: AsyncSession,
    key_manager: NodeKeyManager,
) -> AssetRelationship:
    source = parse_clip(proposal.source)
    target = parse_clip(proposal.target)
    if source.routing_host.lower() != settings.NODE_DOMAIN.lower():
        raise HTTPException(status_code=403, detail="This node is not the root routing authority")
    if source.authority_key_fingerprint != key_manager.public_key_multibase:
        raise HTTPException(status_code=403, detail="This node is not the root signing authority")
    if proposal.state != RelationshipState.PROPOSED or proposal.accepted_by is not None:
        raise HTTPException(status_code=422, detail="Only an unaccepted proposal can be accepted")
    if len(proposal.proofs) != 1 or proposal.proofs[0].proof_purpose != "relationship-assertion":
        raise HTTPException(status_code=422, detail="Proposal must contain one assertion proof")
    if proposal.asserted_by != target.authority_key_fingerprint:
        raise HTTPException(status_code=422, detail="The target authority must assert the relationship")

    descriptor = await fetch_well_known(target.routing_host)
    methods = {method.id: method for method in descriptor.verification_methods}
    assertion = proposal.proofs[0]
    method = methods.get(assertion.verification_method)
    if (
        descriptor.authority != proposal.asserted_by
        or method is None
        or "relationship-assertion" not in method.purposes
        or not NodeKeyManager.verify(
            _unsigned_relationship(proposal),
            assertion.proof_value,
            method.public_key_base64,
        )
    ):
        raise HTTPException(status_code=400, detail="Relationship assertion proof verification failed")

    root_row = (await db.execute(select(Asset).where(Asset.id == proposal.source))).scalar_one_or_none()
    if root_row is None or not root_row.is_authoritative:
        raise HTTPException(status_code=404, detail="Root record is not authoritative on this node")
    root = orm_to_record(root_row)
    if any(item.relationship_id == proposal.relationship_id for item in root.relationships):
        raise HTTPException(status_code=409, detail="Relationship has already been accepted")

    accepted_document = proposal.model_dump(mode="json")
    accepted_document["accepted_by"] = key_manager.public_key_multibase
    accepted_document["state"] = RelationshipState.ACCEPTED.value
    accepted = AssetRelationship.model_validate(accepted_document)
    assertion_digest = hashlib.sha256(canonicalize(assertion.model_dump(mode="json"))).hexdigest()
    acceptance_value = key_manager.sign_record({
        "relationship": _unsigned_relationship(accepted),
        "assertion_proof_sha256": assertion_digest,
    })
    accepted_document = accepted.model_dump(mode="json")
    accepted_document["proofs"].append(Proof(
        verification_method=f"{root.controller}#clip-record-signing",
        created=datetime.now(timezone.utc),
        proof_purpose="relationship-acceptance",
        proof_value=acceptance_value,
    ).model_dump(mode="json"))
    accepted = AssetRelationship.model_validate(accepted_document)

    db.add(AssetHistory(
        asset_id=root_row.id,
        version=root_row.version,
        record_json=root_row.record_json,
        raw_payload=root_row.raw_payload,
        updated_at=root_row.updated_at,
    ))
    updated_at = datetime.now(timezone.utc)
    updated = _signed_record(
        record_id=root.id,
        record_kind=root.record_kind.value,
        subject=root.subject,
        relationships=[*root.relationships, accepted],
        availability=root.availability,
        controller=root.controller,
        created_at=_utc(root.created_at),
        updated_at=updated_at,
        version=root.version + 1,
        key_manager=key_manager,
    )
    root_row.record_json, root_row.raw_payload = record_to_storage(updated)
    root_row.updated_at = updated_at
    root_row.version = updated.version
    root_row.verified_at = updated_at
    await db.commit()
    asyncio.create_task(_push_to_peers(updated))
    return accepted