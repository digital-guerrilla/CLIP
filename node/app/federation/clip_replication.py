"""Authenticated, idempotent replication of owner-signed IFCX proof bundles."""

from datetime import datetime, timezone
import hashlib
from uuid import uuid4

import rfc8785

from ..config import settings
from ..core.data_integrity import add_data_integrity_proof
from ..core.did import resolve_did_web_document, verify_clip_message_proof
from ..core.ifc_protocol import IfcPublicationEnvelope
from ..core.clip_protocol import ClipServiceMessage
from ..core.ifc_protocol import IfcGraphProposalTransaction, IfcProposalTransaction, IfcDecisionTransaction
from ..dependencies import get_key_manager


def digest_json(value: dict) -> str:
    return hashlib.sha256(rfc8785.dumps(value)).hexdigest()


def sign_service_message(kind: str, audience_did: str, payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    signed = add_data_integrity_proof(
        {
            "@context": ["https://w3id.org/security/data-integrity/v2", {"@vocab": "urn:clip:protocol:"}],
            "messageId": str(uuid4()), "actorDid": settings.DID_WEB_ID,
            "audienceDid": audience_did, "kind": kind, "payload": payload,
            "created": now.isoformat().replace("+00:00", "Z"),
        },
        get_key_manager().private_key_bytes,
        verification_method=settings.DID_VERIFICATION_METHOD,
        proof_purpose="authentication", created=now,
    )
    return ClipServiceMessage.model_validate(signed).model_dump(mode="json", by_alias=True)


async def authenticate_service_message(message: ClipServiceMessage, kind: str) -> dict:
    if message.kind != kind or message.audience_did != settings.DID_WEB_ID:
        raise ValueError("Service message kind or audience does not match this endpoint")
    if message.created.tzinfo is None:
        raise ValueError("Service message must have a timezone")
    if message.proof.created != message.created:
        raise ValueError("Service proof creation must match the message creation time")
    age = (datetime.now(timezone.utc) - message.created).total_seconds()
    if not -30 <= age <= 300:
        raise ValueError("Service message is expired or from the future")
    document = await resolve_did_web_document(message.actor_did)
    if not verify_clip_message_proof(message.model_dump(mode="json", by_alias=True), document):
        raise ValueError("Service message authentication failed")
    return document


async def verify_replication_bundle(bundle: dict, authority_did: str, document: dict) -> tuple[str, int]:
    publication = IfcPublicationEnvelope.model_validate(bundle["publication"])
    if publication.publisher_did != authority_did:
        raise ValueError("Only the publishing authority may replicate a dataset")
    if not verify_clip_message_proof(bundle["publication"], document):
        raise ValueError("Invalid replicated publication proof")
    transactions = bundle["transactions"]
    if not isinstance(transactions, list) or len(transactions) > 10000:
        raise ValueError("Invalid or oversized transaction history")
    for sequence, entry in enumerate(transactions, start=1):
        transaction, receipt = entry["transaction"], entry["receipt"]
        if not isinstance(transaction, dict) or not isinstance(receipt, dict):
            raise ValueError("Replicated history entries must contain transaction and receipt objects")
        if (
            receipt.get("authorityDid") != authority_did or receipt.get("sequence") != sequence
            or receipt.get("accepted") is not True or receipt.get("transactionDigest") != digest_json(transaction)
            or not verify_clip_message_proof(receipt, document)
        ):
            raise ValueError("Invalid receipt or non-contiguous replicated sequence")
        proposal, decision = transaction["proposal"], transaction["decision"]
        if transaction.get("kind") == "graphProposalAcceptance":
            IfcGraphProposalTransaction.model_validate(proposal)
        elif transaction.get("kind") == "componentProposalAcceptance":
            IfcProposalTransaction.model_validate(proposal)
        else:
            raise ValueError("Unknown replicated transaction kind")
        IfcDecisionTransaction.model_validate(decision)
        proposer_document = await resolve_did_web_document(proposal["actorDid"])
        if (
            proposal["target"]["authorityDid"] != authority_did
            or proposal["expectedSequence"] != sequence - 1 or decision["expectedSequence"] != sequence - 1
            or decision["actorDid"] != authority_did or decision["decision"] != "accept"
            or decision["proposalId"] != proposal["transactionId"] or decision["proposalDigest"] != digest_json(proposal)
            or receipt["transactionId"] != decision["transactionId"] or transaction["transactionId"] != decision["transactionId"]
            or not verify_clip_message_proof(proposal, proposer_document)
            or not verify_clip_message_proof(decision, document)
        ):
            raise ValueError("Invalid replicated proposal or owner decision")
    return publication.file.header.id, len(transactions)