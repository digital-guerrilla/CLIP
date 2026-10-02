"""HTTP endpoints for signed IFCX proposals and authority decisions."""

from datetime import datetime, timezone
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field, field_validator
from typing import Literal
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..config import settings
from ..core.data_integrity import add_data_integrity_proof
from ..core.did import (
    DidVerificationError,
    resolve_did_web_document,
    verify_clip_message_proof,
)
from ..core.crypto import NodeKeyManager
from ..core.ifc_protocol import (
    IfcAuthorityReceipt,
    IfcDecisionTransaction,
    IfcPublicationEnvelope,
    IfcProposalTransaction,
    IfcGraphProposalTransaction,
)
from ..core.ifc_graph import (
    IfcComponentAddress,
    apply_ifc_component_change,
    apply_ifc_graph_operations,
    flatten_ifc_layers,
)
from ..core.ifcx_models import IfcxFile
from ..core.project_access import can_read_project, require_dataset_read
from ..db.database import get_db
from ..db.ifc_store import (
    DuplicateIfcDataset,
    DuplicateIfcProposal,
    DuplicateIfcTransaction,
    IfcSequenceConflict,
    append_accepted_transaction,
    load_ifc_dataset,
    register_ifc_dataset,
    reject_ifc_proposal,
    set_ifc_trusted_proposers,
    store_ifc_proposal,
)
from ..db.orm_models import IfcDatasetRecord, IfcDatasetTrustPolicy, IfcProposalRecord, IfcAcceptedTransaction, IfcReceiptRecord, ClipProjectPolicy
from ..dependencies import get_key_manager, require_api_key, valid_api_key
from ..federation.clip_layers import ClipFederationError, resolve_ifc_layers

router = APIRouter(prefix="/ifc/v1", tags=["ifc"])


class IfcDatasetRegistration(BaseModel):
    file: IfcxFile
    trusted_proposers: list[str] = Field(alias="trustedProposers")

    @field_validator("trusted_proposers")
    @classmethod
    def validate_trusted_proposers(cls, values: list[str]) -> list[str]:
        if len(values) != len(set(values)):
            raise ValueError("trustedProposers cannot contain duplicates")
        if any(not value.startswith("did:") for value in values):
            raise ValueError("Every trusted proposer must be a DID")
        return values


class TrustedProposersUpdate(BaseModel):
    trusted_proposers: list[str] = Field(alias="trustedProposers")

    @field_validator("trusted_proposers")
    @classmethod
    def validate_trusted_proposers(cls, values: list[str]) -> list[str]:
        return IfcDatasetRegistration.validate_trusted_proposers(values)


def _local_authority() -> tuple[str, str]:
    authority_did = settings.DID_WEB_ID
    verification_method = settings.DID_VERIFICATION_METHOD
    if not authority_did.startswith("did:web:"):
        raise HTTPException(status_code=503, detail="Local did:web identity is not configured")
    if not verification_method.startswith(f"{authority_did}#"):
        raise HTTPException(
            status_code=503,
            detail="Local DID verification method is not configured",
        )
    return authority_did, verification_method


@router.get("/datasets")
async def list_datasets(session: AsyncSession = Depends(get_db), x_api_key: str | None = Header(default=None)) -> dict:
    authority_did, _ = _local_authority()
    rows = (await session.execute(select(IfcDatasetRecord).where(IfcDatasetRecord.authority_did == authority_did).order_by(IfcDatasetRecord.dataset_id))).scalars().all()
    policies = {item.dataset_id: item for item in (await session.execute(select(ClipProjectPolicy).where(ClipProjectPolicy.authority_did == authority_did))).scalars()}
    return {"items": [{"datasetId": row.dataset_id, "authorityDid": row.authority_did, "schemaDigest": row.schema_digest, "updatedAt": row.updated_at.isoformat(),
        "projectName": policies[row.dataset_id].name if row.dataset_id in policies else None,
        "visibility": policies[row.dataset_id].visibility if row.dataset_id in policies else "legacy-public"}
        for row in rows if can_read_project(policies.get(row.dataset_id), local=valid_api_key(x_api_key))]}


@router.get("/datasets/{dataset_id}/graph")
async def resolve_dataset_graph(dataset_id: str, session: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None), product_view: Literal["current", "pinned"] = "current",
    refresh_products: bool = False) -> dict:
    authority_did, _ = _local_authority()
    await require_dataset_read(session, authority_did, dataset_id, local=valid_api_key(x_api_key))
    try:
        file = await load_ifc_dataset(session, authority_did=authority_did, dataset_id=dataset_id)
        resolved = await resolve_ifc_layers(file)
        graph = flatten_ifc_layers(resolved.layers, authority_did=authority_did, dataset_id=dataset_id)
        if refresh_products and not valid_api_key(x_api_key):
            raise HTTPException(403, "Refreshing manufacturer publications requires the local operator key")
        from ..core.ifc_product_resolver import project_products
        products = await project_products(session, graph, refresh=refresh_products, current=product_view == "current")
        if refresh_products:
            await session.commit()
        return {
            **graph.model_dump(mode="json", by_alias=True),
            "effectiveComponents": {path: graph.effective_components(path) for path in graph.entities},
            "sources": list(resolved.sources),
            "productResolution": products,
        }
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except (ClipFederationError, ValueError) as error:
        raise HTTPException(status_code=424, detail=str(error)) from error


@router.get("/datasets/{dataset_id}/history")
async def dataset_history(dataset_id: str, session: AsyncSession = Depends(get_db), x_api_key: str | None = Header(default=None)) -> dict:
    authority_did, _ = _local_authority()
    await require_dataset_read(session, authority_did, dataset_id, local=valid_api_key(x_api_key))
    entries = (await session.execute(select(IfcAcceptedTransaction, IfcReceiptRecord).join(IfcReceiptRecord, IfcReceiptRecord.transaction_id == IfcAcceptedTransaction.transaction_id).where(IfcAcceptedTransaction.authority_did == authority_did, IfcAcceptedTransaction.dataset_id == dataset_id).order_by(IfcAcceptedTransaction.sequence))).all()
    return {"items": [{"transaction": transaction.transaction_json, "receipt": receipt.receipt_json} for transaction, receipt in entries]}


@router.post("/datasets", status_code=201)
async def register_dataset(
    body: IfcDatasetRegistration,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> dict:
    authority_did, _ = _local_authority()
    try:
        await resolve_ifc_layers(body.file)
        digest = await register_ifc_dataset(
            session,
            authority_did=authority_did,
            dataset_id=body.file.header.id,
            file=body.file,
            trusted_proposers=body.trusted_proposers,
        )
    except DuplicateIfcDataset as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except ClipFederationError as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    return {
        "datasetId": body.file.header.id,
        "schemaDigest": digest,
        "trustedProposers": body.trusted_proposers,
    }


@router.put("/datasets/{dataset_id}/trusted-proposers")
async def update_trusted_proposers(
    dataset_id: str,
    body: TrustedProposersUpdate,
    session: AsyncSession = Depends(get_db),
    _: None = Depends(require_api_key),
) -> dict:
    authority_did, _ = _local_authority()
    if await session.get(ClipProjectPolicy, (authority_did, dataset_id)) is not None:
        raise HTTPException(status_code=409, detail="Manage project contributors through project permissions")
    try:
        trusted_proposers = await set_ifc_trusted_proposers(
            session,
            authority_did=authority_did,
            dataset_id=dataset_id,
            trusted_proposers=body.trusted_proposers,
        )
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    return {"datasetId": dataset_id, "trustedProposers": trusted_proposers}


@router.get("/datasets/{dataset_id}/publication")
async def publish_dataset(
    dataset_id: str,
    session: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
    x_api_key: str | None = Header(default=None),
) -> dict:
    authority_did, verification_method = _local_authority()
    await require_dataset_read(session, authority_did, dataset_id, local=isinstance(x_api_key, str) and valid_api_key(x_api_key))
    dataset_record = await session.get(IfcDatasetRecord, (authority_did, dataset_id))
    if dataset_record is None:
        raise HTTPException(status_code=404, detail=f'Unknown IFCX dataset "{dataset_id}"')
    file = IfcxFile.model_validate(dataset_record.file_json)
    document = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "publisherDid": authority_did,
        "file": file.model_dump(mode="json", by_alias=True),
    }
    created = dataset_record.updated_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    publication = add_data_integrity_proof(
        document,
        key_manager.private_key_bytes,
        verification_method=verification_method,
        proof_purpose="assertionMethod",
        created=created,
    )
    return IfcPublicationEnvelope.model_validate(publication).model_dump(
        mode="json",
        by_alias=True,
    )


async def _verified_did_document(did: str, transaction: dict) -> dict:
    try:
        if did == settings.DID_WEB_ID:
            from .did import did_document
            document = await did_document(get_key_manager())
        else:
            document = await resolve_did_web_document(did)
    except (DidVerificationError, httpx.HTTPError, OSError) as error:
        raise HTTPException(status_code=401, detail="Unable to resolve transaction actor DID") from error
    if not verify_clip_message_proof(transaction, document):
        raise HTTPException(status_code=401, detail="Transaction proof verification failed")
    return document


def _receipt_factory(
    *,
    authority_did: str,
    verification_method: str,
    did_document: dict,
    key_manager: NodeKeyManager,
    transaction_id,
    accepted: bool,
):
    def create(sequence: int, transaction_digest: str) -> IfcAuthorityReceipt:
        created = datetime.now(timezone.utc)
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "receiptId": str(uuid4()),
            "authorityDid": authority_did,
            "transactionId": str(transaction_id),
            "accepted": accepted,
            "sequence": sequence,
            "transactionDigest": transaction_digest,
            "created": created.isoformat().replace("+00:00", "Z"),
        }
        signed = add_data_integrity_proof(
            unsigned,
            key_manager.private_key_bytes,
            verification_method=verification_method,
            proof_purpose="assertionMethod",
            created=created,
        )
        if not verify_clip_message_proof(signed, did_document):
            raise ValueError("Configured receipt key does not match the local DID verification method")
        return IfcAuthorityReceipt.model_validate(signed)

    return create


@router.post("/proposals", status_code=201)
async def submit_proposal(
    proposal: IfcProposalTransaction | IfcGraphProposalTransaction,
    session: AsyncSession = Depends(get_db),
) -> dict:
    authority_did, _ = _local_authority()
    if proposal.target.authority_did != authority_did:
        raise HTTPException(status_code=403, detail="Proposal target belongs to another authority")
    await _verified_did_document(proposal.actor_did, proposal.model_dump(mode="json", by_alias=True))
    dataset_record = await session.get(
        IfcDatasetRecord,
        (authority_did, proposal.target.dataset_id),
    )
    if dataset_record is None:
        raise HTTPException(status_code=404, detail="IFCX dataset is not registered")
    if proposal.schema_digest != dataset_record.schema_digest:
        raise HTTPException(status_code=409, detail="Proposal schemaDigest is stale")
    policy = await session.get(IfcDatasetTrustPolicy, (authority_did, proposal.target.dataset_id))
    project = await session.get(ClipProjectPolicy, (authority_did, proposal.target.dataset_id))
    own_project_edit = project is not None and proposal.actor_did == authority_did
    if not own_project_edit and (policy is None or proposal.actor_did not in policy.trusted_proposers):
        raise HTTPException(status_code=403, detail="Proposer DID is not trusted for this dataset")
    current_file = await load_ifc_dataset(
        session,
        authority_did=authority_did,
        dataset_id=proposal.target.dataset_id,
    )
    try:
        resolved_layers = await resolve_ifc_layers(current_file)
    except ClipFederationError as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    graph = flatten_ifc_layers(
        resolved_layers.layers,
        authority_did=authority_did,
        dataset_id=proposal.target.dataset_id,
    )
    if isinstance(proposal, IfcProposalTransaction) and proposal.target.entity_path not in graph.entities:
        raise HTTPException(status_code=404, detail="IFCX entity is not present in the dataset")
    if isinstance(proposal, IfcProposalTransaction) and proposal.target.component_schema_id not in graph.schemas:
        raise HTTPException(status_code=422, detail="Component schema is not present in the dataset")
    try:
        if isinstance(proposal, IfcGraphProposalTransaction):
            apply_ifc_graph_operations(current_file, authority_did=authority_did, dataset_id=proposal.target.dataset_id,
                operations=[item.model_dump(mode="json", by_alias=True) for item in proposal.operations], layers=resolved_layers.layers)
        else:
            apply_ifc_component_change(current_file, proposal.target, action=proposal.change.action,
                value=proposal.change.value, layers=resolved_layers.layers)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error
    try:
        digest = await store_ifc_proposal(session, proposal)
    except DuplicateIfcProposal as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "proposalId": str(proposal.transaction_id),
        "proposalDigest": digest,
        "status": "pending",
    }


@router.get("/datasets/{dataset_id}/components")
async def resolve_component(
    dataset_id: str,
    entity_path: str,
    component_schema_id: str,
    session: AsyncSession = Depends(get_db),
    x_api_key: str | None = Header(default=None),
    product_view: Literal["current", "pinned"] = "current",
) -> dict:
    authority_did, _ = _local_authority()
    await require_dataset_read(session, authority_did, dataset_id, local=valid_api_key(x_api_key))
    try:
        file = await load_ifc_dataset(
            session,
            authority_did=authority_did,
            dataset_id=dataset_id,
        )
        resolved_layers = await resolve_ifc_layers(file)
        graph = flatten_ifc_layers(
            resolved_layers.layers,
            authority_did=authority_did,
            dataset_id=dataset_id,
        )
        from ..core.ifc_product_resolver import project_products
        await project_products(session, graph, current=product_view == "current")
        address = IfcComponentAddress.model_validate({
            "authorityDid": authority_did,
            "datasetId": dataset_id,
            "entityPath": entity_path,
            "componentSchemaId": component_schema_id,
        })
        value = graph.resolve_component(address)
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ClipFederationError as error:
        raise HTTPException(status_code=424, detail=str(error)) from error
    return {
        "authorityDid": authority_did,
        "datasetId": dataset_id,
        "entityPath": entity_path,
        "componentSchemaId": component_schema_id,
        "value": value,
    }


@router.post("/decisions")
async def decide_proposal(
    decision: IfcDecisionTransaction,
    session: AsyncSession = Depends(get_db),
    key_manager: NodeKeyManager = Depends(get_key_manager),
) -> dict:
    authority_did, verification_method = _local_authority()
    if decision.actor_did != authority_did:
        raise HTTPException(status_code=403, detail="Only the local authority can decide this proposal")
    did_document = await _verified_did_document(
        decision.actor_did,
        decision.model_dump(mode="json", by_alias=True),
    )
    proposal_record = await session.get(IfcProposalRecord, str(decision.proposal_id))
    if proposal_record is None:
        raise HTTPException(status_code=404, detail="Proposal not found")

    project = await session.get(ClipProjectPolicy, (authority_did, proposal_record.dataset_id))
    if decision.decision == "accept" and project is not None and proposal_record.proposer_did != authority_did:
        if project.members.get(proposal_record.proposer_did) != "contributor":
            raise HTTPException(status_code=403, detail="Proposer is no longer a project contributor")

    resolved_layers = None
    if decision.decision == "accept":
        try:
            owner_file = await load_ifc_dataset(
                session,
                authority_did=authority_did,
                dataset_id=proposal_record.dataset_id,
            )
            resolved_layers = (await resolve_ifc_layers(owner_file)).layers
        except KeyError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ClipFederationError as error:
            raise HTTPException(status_code=424, detail=str(error)) from error

    create_receipt = _receipt_factory(
        authority_did=authority_did,
        verification_method=verification_method,
        did_document=did_document,
        key_manager=key_manager,
        transaction_id=decision.transaction_id,
        accepted=decision.decision == "accept",
    )
    decision_document = decision.model_dump(mode="json", by_alias=True)
    try:
        if decision.decision == "accept":
            receipt = await append_accepted_transaction(
                session,
                authority_did=authority_did,
                dataset_id=proposal_record.dataset_id,
                expected_sequence=decision.expected_sequence,
                transaction=decision_document,
                create_receipt=create_receipt,
                proposal_id=decision.proposal_id,
                resolved_layers=resolved_layers,
            )
        else:
            receipt = await reject_ifc_proposal(
                session,
                authority_did=authority_did,
                dataset_id=proposal_record.dataset_id,
                proposal_id=decision.proposal_id,
                decision=decision_document,
                create_receipt=create_receipt,
            )
    except IfcSequenceConflict as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except (DuplicateIfcProposal, DuplicateIfcTransaction) as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except KeyError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error

    return receipt.model_dump(mode="json", by_alias=True)