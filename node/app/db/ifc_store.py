"""Durable authority-local sequencing for accepted IFCX transactions."""

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
import hashlib
from uuid import UUID

import rfc8785
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.ifc_graph import IfcComponentAddress, apply_ifc_component_change, apply_ifc_graph_operations
from ..core.ifcx_models import (
    IfcxFile,
    ensure_clip_component_deletion_schema,
    ifcx_schema_digest,
)
from ..core.ifc_protocol import IfcAuthorityReceipt
from .orm_models import (
    IfcAcceptedTransaction,
    IfcAuthoritySequence,
    IfcDatasetRecord,
    IfcDatasetTrustPolicy,
    IfcProposalRecord,
    ClipProjectPolicy,
    IfcReceiptRecord,
)


class IfcSequenceConflict(ValueError):
    pass


class DuplicateIfcTransaction(ValueError):
    pass


class DuplicateIfcProposal(ValueError):
    pass


class DuplicateIfcDataset(ValueError):
    pass


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


async def register_ifc_dataset(
    session: AsyncSession,
    *,
    authority_did: str,
    dataset_id: str,
    file: IfcxFile,
    trusted_proposers: list[str] | None = None,
    project: ClipProjectPolicy | None = None,
) -> str:
    """Register one native IFCX dataset and return its schema-set digest."""
    if file.header.id != dataset_id:
        raise ValueError("Dataset id must match the IFCX header id")
    file = ensure_clip_component_deletion_schema(file)
    if await session.get(IfcDatasetRecord, (authority_did, dataset_id)) is not None:
        raise DuplicateIfcDataset("Dataset is already registered")

    from ..core.ifcx_models import validate_ifcx_attributes

    validate_ifcx_attributes(file)
    digest = ifcx_schema_digest(file)
    if project is not None:
        if (project.authority_did, project.dataset_id) != (authority_did, dataset_id):
            raise ValueError("Project policy must identify the registered dataset")
        session.add(project)
    session.add(IfcDatasetRecord(
        authority_did=authority_did,
        dataset_id=dataset_id,
        schema_digest=digest,
        file_json=file.model_dump(mode="json", by_alias=True),
        updated_at=_utc_now(),
    ))
    session.add(IfcDatasetTrustPolicy(
        authority_did=authority_did,
        dataset_id=dataset_id,
        trusted_proposers=list(trusted_proposers or []),
        updated_at=_utc_now(),
    ))
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise DuplicateIfcDataset("Dataset is already registered") from error
    return digest


async def set_ifc_trusted_proposers(
    session: AsyncSession,
    *,
    authority_did: str,
    dataset_id: str,
    trusted_proposers: list[str],
) -> list[str]:
    if await session.get(IfcDatasetRecord, (authority_did, dataset_id)) is None:
        raise KeyError(f'Unknown IFCX dataset "{dataset_id}"')
    policy = await session.get(IfcDatasetTrustPolicy, (authority_did, dataset_id))
    if policy is None:
        policy = IfcDatasetTrustPolicy(
            authority_did=authority_did,
            dataset_id=dataset_id,
            trusted_proposers=[],
            updated_at=_utc_now(),
        )
        session.add(policy)
    policy.trusted_proposers = list(trusted_proposers)
    policy.updated_at = _utc_now()
    await session.commit()
    return list(policy.trusted_proposers)


async def load_ifc_dataset(
    session: AsyncSession,
    *,
    authority_did: str,
    dataset_id: str,
) -> IfcxFile:
    record = await session.get(IfcDatasetRecord, (authority_did, dataset_id))
    if record is None:
        raise KeyError(f'Unknown IFCX dataset "{dataset_id}"')
    return IfcxFile.model_validate(record.file_json)


async def store_ifc_proposal(
    session: AsyncSession,
    proposal,
) -> str:
    """Store a verified signed proposal as pending and return its JCS digest."""
    proposal_document = proposal.model_dump(mode="json", by_alias=True)
    proposal_id = str(proposal.transaction_id)
    if await session.get(IfcProposalRecord, proposal_id) is not None:
        raise DuplicateIfcProposal("Proposal ID has already been recorded")

    proposal_digest = hashlib.sha256(rfc8785.dumps(proposal_document)).hexdigest()
    now = _utc_now()
    session.add(IfcProposalRecord(
        proposal_id=proposal_id,
        authority_did=proposal.target.authority_did,
        dataset_id=proposal.target.dataset_id,
        proposer_did=proposal.actor_did,
        proposal_digest=proposal_digest,
        proposal_json=proposal_document,
        status="pending",
        created_at=proposal.created,
        updated_at=now,
    ))
    try:
        await session.commit()
    except IntegrityError as error:
        await session.rollback()
        raise DuplicateIfcProposal("Proposal ID has already been recorded") from error
    return proposal_digest


async def append_accepted_transaction(
    session: AsyncSession,
    *,
    authority_did: str,
    dataset_id: str,
    expected_sequence: int,
    transaction: Mapping[str, object],
    create_receipt: Callable[[int, str], IfcAuthorityReceipt],
    proposal_id: UUID | str | None = None,
    resolved_layers: Sequence[IfcxFile] | None = None,
) -> IfcAuthorityReceipt:
    """Atomically append one verified transaction and its signed receipt.

    Signature verification and authorization must happen before calling this
    function. The database sequence is local to this authority; it is not a
    network-wide ordering mechanism.
    """
    try:
        transaction_id = str(UUID(str(transaction.get("transactionId", ""))))
    except ValueError as error:
        raise ValueError("Transaction must contain a valid transactionId") from error
    if expected_sequence < 0:
        raise ValueError("Expected sequence cannot be negative")
    if transaction.get("actorDid") is None:
        raise ValueError("Transaction must contain actorDid")

    try:
        existing = await session.get(IfcAcceptedTransaction, transaction_id)
        if existing is not None:
            raise DuplicateIfcTransaction("Transaction ID has already been committed")
        if transaction.get("expectedSequence") != expected_sequence:
            raise ValueError("Transaction expectedSequence does not match the commit precondition")

        state = (
            await session.execute(
                select(IfcAuthoritySequence)
                .where(IfcAuthoritySequence.authority_did == authority_did)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if state is None:
            state = IfcAuthoritySequence(authority_did=authority_did, sequence=0)
            session.add(state)
            await session.flush()

        if state.sequence != expected_sequence:
            raise IfcSequenceConflict(
                f"Expected authority sequence {expected_sequence}, current sequence is {state.sequence}"
            )

        proposal_record = None
        transaction_document = dict(transaction)
        if proposal_id is not None:
            proposal_id = str(UUID(str(proposal_id)))
            proposal_record = (
                await session.execute(
                    select(IfcProposalRecord)
                    .where(IfcProposalRecord.proposal_id == proposal_id)
                    .with_for_update()
                )
            ).scalar_one_or_none()
            if proposal_record is None:
                raise KeyError(f'Unknown IFCX proposal "{proposal_id}"')
            if proposal_record.status != "pending":
                raise DuplicateIfcProposal("Proposal has already received an owner decision")
            if (
                proposal_record.authority_did != authority_did
                or proposal_record.dataset_id != dataset_id
                or transaction.get("decision") != "accept"
                or str(transaction.get("proposalId")) != proposal_id
                or transaction.get("proposalDigest") != proposal_record.proposal_digest
                or transaction.get("actorDid") != authority_did
            ):
                raise ValueError("Owner decision does not match the pending proposal")
            dataset_record = await session.get(
                IfcDatasetRecord,
                (authority_did, dataset_id),
                with_for_update=True,
            )
            if dataset_record is None:
                raise KeyError(f'Unknown IFCX dataset "{dataset_id}"')
            if proposal_record.proposal_json.get("schemaDigest") != dataset_record.schema_digest:
                raise ValueError("Proposal schemaDigest does not match the registered dataset")

            current_file = IfcxFile.model_validate(dataset_record.file_json)
            target = proposal_record.proposal_json["target"]
            if "operations" in proposal_record.proposal_json:
                updated_file = apply_ifc_graph_operations(current_file, authority_did=authority_did, dataset_id=dataset_id,
                    operations=proposal_record.proposal_json["operations"], layers=resolved_layers)
                transaction_kind = "graphProposalAcceptance"
            else:
                change = proposal_record.proposal_json["change"]
                updated_file = apply_ifc_component_change(
                    current_file, IfcComponentAddress.model_validate(target), action=change["action"],
                    value=change.get("value"), layers=resolved_layers,
                )
                transaction_kind = "componentProposalAcceptance"
            dataset_record.file_json = updated_file.model_dump(mode="json", by_alias=True)
            dataset_record.updated_at = _utc_now()
            transaction_document = {
                "@context": transaction.get("@context"),
                "transactionId": transaction_id,
                "kind": transaction_kind,
                "proposal": proposal_record.proposal_json,
                "decision": dict(transaction),
            }

        next_sequence = state.sequence + 1
        transaction_digest = hashlib.sha256(rfc8785.dumps(transaction_document)).hexdigest()
        receipt = create_receipt(next_sequence, transaction_digest)
        if (
            receipt.authority_did != authority_did
            or str(receipt.transaction_id) != transaction_id
            or not receipt.accepted
            or receipt.sequence != next_sequence
            or receipt.transaction_digest != transaction_digest
        ):
            raise ValueError("Receipt does not match the accepted transaction")

        created_at = receipt.created
        session.add(IfcAcceptedTransaction(
            transaction_id=transaction_id,
            authority_did=authority_did,
            dataset_id=dataset_id,
            sequence=next_sequence,
            transaction_digest=transaction_digest,
            transaction_json=transaction_document,
            created_at=created_at,
        ))
        session.add(IfcReceiptRecord(
            receipt_id=str(receipt.receipt_id),
            transaction_id=transaction_id,
            authority_did=authority_did,
            sequence=next_sequence,
            transaction_digest=transaction_digest,
            receipt_json=receipt.model_dump(mode="json", by_alias=True),
            created_at=created_at,
        ))
        if proposal_record is not None:
            proposal_record.status = "accepted"
            proposal_record.decision_json = dict(transaction)
            proposal_record.updated_at = created_at
        state.sequence = next_sequence
        await session.commit()
        return receipt
    except IntegrityError as error:
        await session.rollback()
        raise IfcSequenceConflict(
            "A concurrent transaction changed the authority sequence; retry from the latest sequence"
        ) from error
    except Exception:
        await session.rollback()
        raise


async def reject_ifc_proposal(
    session: AsyncSession,
    *,
    authority_did: str,
    dataset_id: str,
    proposal_id: UUID | str,
    decision: Mapping[str, object],
    create_receipt: Callable[[int, str], IfcAuthorityReceipt],
) -> IfcAuthorityReceipt:
    """Record an owner rejection without advancing the accepted-change sequence."""
    normalized_proposal_id = str(UUID(str(proposal_id)))
    try:
        proposal = (
            await session.execute(
                select(IfcProposalRecord)
                .where(IfcProposalRecord.proposal_id == normalized_proposal_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if proposal is None:
            raise KeyError(f'Unknown IFCX proposal "{normalized_proposal_id}"')
        if proposal.status != "pending":
            raise DuplicateIfcProposal("Proposal has already received an owner decision")
        if (
            proposal.authority_did != authority_did
            or proposal.dataset_id != dataset_id
            or decision.get("decision") != "reject"
            or str(decision.get("proposalId")) != normalized_proposal_id
            or decision.get("proposalDigest") != proposal.proposal_digest
            or decision.get("actorDid") != authority_did
        ):
            raise ValueError("Owner decision does not match the pending proposal")

        sequence_state = await session.get(IfcAuthoritySequence, authority_did)
        current_sequence = sequence_state.sequence if sequence_state is not None else 0
        if decision.get("expectedSequence") != current_sequence:
            raise IfcSequenceConflict(
                f"Expected authority sequence {decision.get('expectedSequence')}, "
                f"current sequence is {current_sequence}"
            )

        decision_document = {
            "@context": decision.get("@context"),
            "transactionId": str(decision.get("transactionId")),
            "kind": "componentProposalRejection",
            "proposal": proposal.proposal_json,
            "decision": dict(decision),
        }
        decision_digest = hashlib.sha256(rfc8785.dumps(decision_document)).hexdigest()
        receipt = create_receipt(current_sequence, decision_digest)
        if (
            receipt.authority_did != authority_did
            or str(receipt.transaction_id) != str(decision.get("transactionId"))
            or receipt.accepted
            or receipt.sequence != current_sequence
            or receipt.transaction_digest != decision_digest
        ):
            raise ValueError("Rejection receipt does not match the owner decision")

        now = _utc_now()
        proposal.status = "rejected"
        proposal.decision_json = dict(decision)
        proposal.decision_receipt_json = receipt.model_dump(mode="json", by_alias=True)
        proposal.updated_at = now
        await session.commit()
        return receipt
    except Exception:
        await session.rollback()
        raise