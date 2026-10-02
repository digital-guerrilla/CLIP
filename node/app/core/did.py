"""CLIP DID key selection and typed network/IFC graph proof verification."""

from collections.abc import Mapping, Sequence
import asyncio
import ipaddress
import socket
from typing import Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
import nacl.signing
from pydantic import ValidationError

from .data_integrity import base58btc_decode
from .egress import public_http_client, decode_json


class DidVerificationError(ValueError):
    pass


def is_loopback_host(host: str) -> bool:
    if host.casefold() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def did_web_document_url(
    did: str,
    *,
    allow_http_loopback: bool | None = None,
) -> str:
    if not did.startswith("did:web:"):
        raise DidVerificationError("Only did:web identifiers are supported")
    method_id = did[len("did:web:"):]
    if not method_id or any(character in method_id for character in "?#@"):
        raise DidVerificationError("Invalid did:web method-specific identifier")

    parts = method_id.split(":")
    host = unquote(parts[0])
    if not host or "/" in host or "\\" in host:
        raise DidVerificationError("Invalid did:web hostname")
    parsed = urlsplit(f"https://{host}")
    if parsed.hostname is None or parsed.username is not None or parsed.password is not None:
        raise DidVerificationError("Invalid did:web hostname")
    try:
        port = parsed.port
    except ValueError as error:
        raise DidVerificationError("Invalid did:web port") from error
    if port is not None and not 1 <= port <= 65535:
        raise DidVerificationError("Invalid did:web port")
    if allow_http_loopback is None:
        from ..config import settings

        allow_http_loopback = settings.CLIP_ALLOW_HTTP_LOOPBACK
    scheme = "https"
    if allow_http_loopback and is_loopback_host(parsed.hostname):
        scheme = "http"

    path_parts = []
    for part in parts[1:]:
        decoded = unquote(part)
        if not decoded or decoded in {".", ".."} or "/" in decoded or "\\" in decoded:
            raise DidVerificationError("Invalid did:web path")
        path_parts.append(decoded)
    if not path_parts:
        return f"{scheme}://{parsed.netloc}/.well-known/did.json"
    return f"{scheme}://{parsed.netloc}/{'/'.join(path_parts)}/did.json"


async def resolve_clip_gossip_endpoint(
    document: Mapping[str, Any],
    *,
    expected_did: str,
    allow_http_loopback: bool | None = None,
) -> str:
    """Resolve the DID's explicit CLIP gossip service endpoint."""
    if document.get("id") != expected_did:
        raise DidVerificationError("DID document id does not match the expected peer DID")
    if allow_http_loopback is None:
        from ..config import settings

        allow_http_loopback = settings.CLIP_ALLOW_HTTP_LOOPBACK
    services = document.get("service", [])
    if isinstance(services, Mapping):
        services = [services]
    if not isinstance(services, Sequence) or isinstance(services, (str, bytes)):
        raise DidVerificationError("DID service must be a list or object")

    service_id = f"{expected_did}#clip-gossip"
    service = next(
        (
            item
            for item in services
            if isinstance(item, Mapping)
            and item.get("id") == service_id
            and "ClipGossipService" in (
                item.get("type") if isinstance(item.get("type"), list)
                else [item.get("type")]
            )
        ),
        None,
    )
    if service is None:
        raise DidVerificationError("DID document has no ClipGossipService")
    endpoint = service.get("serviceEndpoint")
    if not isinstance(endpoint, str):
        raise DidVerificationError("ClipGossipService must have a string serviceEndpoint")

    parsed = urlsplit(endpoint)
    loopback_exception = allow_http_loopback and is_loopback_host(parsed.hostname or "")
    if (
        (parsed.scheme != "https" and not (parsed.scheme == "http" and loopback_exception))
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise DidVerificationError("ClipGossipService endpoint must be a clean HTTPS URL")
    if not loopback_exception:
        try:
            await validate_public_host(parsed.hostname, parsed.port or 443)
        except ValueError as error:
            raise DidVerificationError("ClipGossipService must resolve to a public host") from error
    return endpoint.rstrip("/")


async def resolve_clip_service_endpoint(document: Mapping[str, Any], *, expected_did: str, service_type: str, fragment: str) -> str:
    services = document.get("service", [])
    if isinstance(services, Mapping):
        services = [services]
    if not isinstance(services, list):
        raise DidVerificationError("DID service must be a list or object")
    service = next((item for item in services if isinstance(item, Mapping) and item.get("id") == f"{expected_did}#{fragment}" and service_type in (item.get("type") if isinstance(item.get("type"), list) else [item.get("type")])), None)
    if service is None:
        raise DidVerificationError(f"DID document has no {service_type}")
    adapted = {**document, "service": [{**service, "id": f"{expected_did}#clip-gossip", "type": "ClipGossipService"}]}
    return await resolve_clip_gossip_endpoint(adapted, expected_did=expected_did)


async def validate_public_host(host: str, port: int) -> None:
    try:
        ip = ipaddress.ip_address(host)
        addresses = [ip]
    except ValueError:
        try:
            results = await asyncio.get_running_loop().getaddrinfo(
                host,
                port,
                type=socket.SOCK_STREAM,
            )
        except OSError as error:
            raise DidVerificationError("Unable to resolve did:web hostname") from error
        addresses = [ipaddress.ip_address(result[4][0]) for result in results]

    if not addresses or any(not address.is_global for address in addresses):
        raise DidVerificationError("did:web hostname must resolve only to public IP addresses")


async def resolve_did_web_document(
    did: str,
    *,
    allow_http_loopback: bool | None = None,
) -> dict[str, Any]:
    """Fetch a public did:web document without following redirects."""
    document_url = did_web_document_url(did, allow_http_loopback=allow_http_loopback)
    parsed = urlsplit(document_url)
    loopback_exception = parsed.scheme == "http" and is_loopback_host(parsed.hostname or "")
    if not loopback_exception:
        await validate_public_host(parsed.hostname or "", parsed.port or 443)
    timeout = httpx.Timeout(5.0, connect=3.0)
    content = bytearray()
    async with public_http_client(timeout) as client:
        async with client.stream("GET", document_url, headers={"Accept": "application/did+json, application/json"}) as response:
            if response.status_code != 200:
                raise DidVerificationError(f"did:web endpoint returned HTTP {response.status_code}")
            async for chunk in response.aiter_bytes():
                content.extend(chunk)
                if len(content) > 1_000_000:
                    raise DidVerificationError("DID document exceeds the maximum size")
    try:
        document = decode_json(bytes(content))
    except ValueError as error:
        raise DidVerificationError("did:web endpoint did not return valid JSON") from error
    if not isinstance(document, dict) or document.get("id") != did:
        raise DidVerificationError("DID document id does not match the requested DID")
    return document


def resolve_ed25519_verification_key(
    document: Mapping[str, Any],
    *,
    expected_did: str,
    verification_method: str,
    proof_purpose: Literal[
        "assertionMethod",
        "capabilityInvocation",
        "authentication",
    ],
) -> nacl.signing.VerifyKey:
    """Resolve a DID document key only when its relationship authorizes this proof purpose."""
    if document.get("id") != expected_did:
        raise DidVerificationError("DID document id does not match the expected DID")
    if not verification_method.startswith(f"{expected_did}#"):
        raise DidVerificationError("Verification method is not controlled by the expected DID")

    methods = document.get("verificationMethod", [])
    if isinstance(methods, Mapping):
        methods = [methods]
    if not isinstance(methods, Sequence) or isinstance(methods, (str, bytes)):
        raise DidVerificationError("DID verificationMethod must be a list or object")

    method = next(
        (
            item
            for item in methods
            if isinstance(item, Mapping) and item.get("id") == verification_method
        ),
        None,
    )
    if method is None:
        raise DidVerificationError("Verification method is not present in the DID document")
    if method.get("revoked") or verification_method in document.get("revokedVerificationMethods", []):
        raise DidVerificationError("Verification method has been revoked")
    if method.get("controller") != expected_did or method.get("type") != "Multikey":
        raise DidVerificationError("Verification method must be a controlled Ed25519 Multikey")

    relationship = document.get(proof_purpose, [])
    if isinstance(relationship, (str, Mapping)):
        relationship = [relationship]
    if not isinstance(relationship, Sequence):
        raise DidVerificationError(f"DID {proof_purpose} relationship must be a list")
    relationship_ids = {
        item.get("id") if isinstance(item, Mapping) else item
        for item in relationship
    }
    if verification_method not in relationship_ids:
        raise DidVerificationError(
            f"Verification method is not authorized for {proof_purpose}"
        )

    public_key_multibase = method.get("publicKeyMultibase")
    if not isinstance(public_key_multibase, str):
        raise DidVerificationError("Multikey must contain publicKeyMultibase")
    try:
        multikey = base58btc_decode(public_key_multibase)
    except ValueError as error:
        raise DidVerificationError("Invalid publicKeyMultibase") from error
    if len(multikey) != 34 or multikey[:2] != bytes.fromhex("ed01"):
        raise DidVerificationError("Expected an Ed25519 Multikey (0xed01 + 32-byte key)")
    try:
        return nacl.signing.VerifyKey(multikey[2:])
    except ValueError as error:
        raise DidVerificationError("Invalid Ed25519 public key") from error


def verify_clip_message_proof(
    transaction: Mapping[str, Any],
    did_document: Mapping[str, Any],
) -> bool:
    """Validate a typed CLIP message or IFC graph transaction against a DID document."""
    from .data_integrity import verify_data_integrity_proof
    from .ifc_protocol import (
        IfcAuthorityReceipt,
        IfcDecisionTransaction,
        IfcProposalTransaction,
        IfcGraphProposalTransaction,
        IfcPublicationEnvelope,
    )
    from .clip_protocol import ClipServiceMessage, ClipEvidenceManifest
    from .clip_network import ClipPeerDigest

    try:
        if "messageId" in transaction:
            typed_transaction = ClipServiceMessage.model_validate(transaction)
            actor_did = typed_transaction.actor_did
        elif "evidenceId" in transaction:
            typed_transaction = ClipEvidenceManifest.model_validate(transaction)
            actor_did = typed_transaction.publisher_did
        elif "decision" in transaction:
            typed_transaction = IfcDecisionTransaction.model_validate(transaction)
            actor_did = typed_transaction.actor_did
        elif "operations" in transaction:
            typed_transaction = IfcGraphProposalTransaction.model_validate(transaction)
            actor_did = typed_transaction.actor_did
        elif "target" in transaction:
            typed_transaction = IfcProposalTransaction.model_validate(transaction)
            actor_did = typed_transaction.actor_did
        elif "receiptId" in transaction:
            typed_transaction = IfcAuthorityReceipt.model_validate(transaction)
            actor_did = typed_transaction.authority_did
        elif "publisherDid" in transaction:
            typed_transaction = IfcPublicationEnvelope.model_validate(transaction)
            actor_did = typed_transaction.publisher_did
        elif "fromDid" in transaction:
            typed_transaction = ClipPeerDigest.model_validate(transaction)
            actor_did = typed_transaction.from_did
        else:
            return False

        proof = typed_transaction.proof
        from datetime import datetime

        if proof.created.tzinfo is None:
            return False
        methods = did_document.get("verificationMethod", [])
        if isinstance(methods, Mapping):
            methods = [methods]
        if not isinstance(methods, Sequence) or isinstance(methods, (str, bytes)):
            return False
        method = next((item for item in methods if isinstance(item, Mapping) and item.get("id") == proof.verification_method), {})
        for field, lower_bound in (("validFrom", True), ("validUntil", False)):
            if method.get(field):
                bound = datetime.fromisoformat(method[field].replace("Z", "+00:00"))
                if bound.tzinfo is None or (proof.created < bound if lower_bound else proof.created >= bound):
                    return False
        verification_key = resolve_ed25519_verification_key(
            did_document,
            expected_did=actor_did,
            verification_method=proof.verification_method,
            proof_purpose=proof.proof_purpose,
        )
        return verify_data_integrity_proof(transaction, verification_key)
    except (DidVerificationError, ValidationError, ValueError):
        return False