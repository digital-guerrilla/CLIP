"""Local did:web controller document for IFCX transaction and gossip keys."""

from fastapi import APIRouter, Depends, HTTPException

from ..config import settings
from ..core.crypto import NodeKeyManager
from ..dependencies import get_key_manager
import nacl.signing
from ..core.data_integrity import base58btc_encode

router = APIRouter(tags=["did"])


@router.get("/.well-known/did.json")
async def did_document(
    key_manager: NodeKeyManager = Depends(get_key_manager),
) -> dict:
    did = settings.DID_WEB_ID
    verification_method = settings.DID_VERIFICATION_METHOD
    if not did.startswith("did:web:") or not verification_method.startswith(f"{did}#"):
        raise HTTPException(status_code=503, detail="Local did:web identity is not configured")

    document = {
        "@context": [
            "https://www.w3.org/ns/did/v1",
            "https://w3id.org/security/multikey/v1",
        ],
        "id": did,
        "verificationMethod": [{
            "id": verification_method,
            "type": "Multikey",
            "controller": did,
            "publicKeyMultibase": key_manager.public_key_multibase,
        }],
        "authentication": [verification_method],
        "assertionMethod": [verification_method],
        "capabilityInvocation": [verification_method],
    }
    document["service"] = [{
        "id": f"{did}#clip-replication", "type": "ClipReplicationService",
        "serviceEndpoint": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/replication/receive",
    }, {
        "id": f"{did}#clip-evidence", "type": "ClipEvidenceService",
        "serviceEndpoint": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/evidence/fragments",
    }, {
        "id": f"{did}#clip-assets", "type": "ClipAssetService",
        "serviceEndpoint": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/projects/read",
    }, {
        "id": f"{did}#clip-project-invites", "type": "ClipProjectInviteService",
        "serviceEndpoint": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/projects/invites/receive",
    }, {
        "id": f"{did}#clip-supply-chain", "type": "ClipSupplyChainService",
        "serviceEndpoint": f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/supply-chain/receive",
    }]
    document["revokedVerificationMethods"] = settings.DID_REVOKED_METHODS
    agreement_id = f"{did}#evidence-key"
    agreement_key = nacl.signing.SigningKey(key_manager.private_key_bytes).to_curve25519_private_key().public_key
    document["verificationMethod"].append({
        "id": agreement_id, "type": "Multikey", "controller": did,
        "publicKeyMultibase": base58btc_encode(bytes.fromhex("ec01") + bytes(agreement_key)),
    })
    document["keyAgreement"] = [agreement_id]
    for previous in settings.DID_PREVIOUS_KEYS:
        if previous.get("id", "").startswith(f"{did}#") and previous.get("id") not in settings.DID_REVOKED_METHODS:
            document["verificationMethod"].append(previous)
            for purpose in ("authentication", "assertionMethod", "capabilityInvocation"):
                document[purpose].append(previous["id"])
    if settings.CLIP_GOSSIP_ENABLED:
        endpoint = f"{settings.NODE_API_BASE.rstrip('/')}/clip/v1/network/gossip/sync"
        document["service"].append({
            "id": f"{did}#clip-gossip",
            "type": "ClipGossipService",
            "serviceEndpoint": endpoint,
        })
    return document