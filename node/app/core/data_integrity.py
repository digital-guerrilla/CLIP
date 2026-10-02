"""W3C Data Integrity proofs using the eddsa-jcs-2022 cryptosuite."""

from collections.abc import Mapping
from datetime import datetime, timezone
import hashlib
from typing import Any, Literal

import nacl.exceptions
import nacl.signing
import rfc8785


_BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class DataIntegrityError(ValueError):
    pass


def add_data_integrity_proof(
    document: Mapping[str, Any],
    signing_key: bytes | nacl.signing.SigningKey,
    *,
    verification_method: str,
    proof_purpose: Literal["assertionMethod", "capabilityInvocation", "authentication"],
    created: datetime,
) -> dict[str, Any]:
    """Return a copy of a JSON document secured with eddsa-jcs-2022."""
    if "proof" in document:
        raise DataIntegrityError("Document already contains a proof")
    if created.tzinfo is None or created.utcoffset() is None:
        raise DataIntegrityError("Proof creation time must include a timezone")
    if not verification_method:
        raise DataIntegrityError("Verification method is required")

    unsecured = dict(document)
    proof_options: dict[str, Any] = {
        "type": "DataIntegrityProof",
        "cryptosuite": "eddsa-jcs-2022",
        "created": created.astimezone(timezone.utc).isoformat().replace("+00:00", "Z"),
        "verificationMethod": verification_method,
        "proofPurpose": proof_purpose,
    }
    if "@context" in unsecured:
        proof_options["@context"] = unsecured["@context"]

    proof_hash = hashlib.sha256(rfc8785.dumps(proof_options)).digest()
    document_hash = hashlib.sha256(rfc8785.dumps(unsecured)).digest()
    hash_data = proof_hash + document_hash

    key = (
        signing_key
        if isinstance(signing_key, nacl.signing.SigningKey)
        else nacl.signing.SigningKey(signing_key)
    )
    proof = {
        **proof_options,
        "proofValue": base58btc_encode(key.sign(hash_data).signature),
    }
    return {**unsecured, "proof": proof}


def verify_data_integrity_proof(
    secured_document: Mapping[str, Any],
    public_key: bytes | nacl.signing.VerifyKey,
) -> bool:
    """Verify a single Data Integrity proof with a DID-resolved public key."""
    proof = secured_document.get("proof")
    if not isinstance(proof, dict):
        return False
    if (
        proof.get("type") != "DataIntegrityProof"
        or proof.get("cryptosuite") != "eddsa-jcs-2022"
        or not isinstance(proof.get("verificationMethod"), str)
        or not isinstance(proof.get("proofPurpose"), str)
        or not isinstance(proof.get("proofValue"), str)
    ):
        return False

    unsecured = {key: value for key, value in secured_document.items() if key != "proof"}
    proof_options = {key: value for key, value in proof.items() if key != "proofValue"}
    document_context = unsecured.get("@context")
    proof_context = proof_options.get("@context")
    if document_context is not None:
        if proof_context is None:
            return False
        document_contexts = document_context if isinstance(document_context, list) else [document_context]
        proof_contexts = proof_context if isinstance(proof_context, list) else [proof_context]
        if document_contexts[:len(proof_contexts)] != proof_contexts:
            return False
        unsecured["@context"] = proof_context
    elif proof_context is not None:
        return False

    try:
        signature = base58btc_decode(proof["proofValue"])
        if len(signature) != 64:
            return False
        proof_hash = hashlib.sha256(rfc8785.dumps(proof_options)).digest()
        document_hash = hashlib.sha256(rfc8785.dumps(unsecured)).digest()
        hash_data = proof_hash + document_hash
        verify_key = (
            public_key
            if isinstance(public_key, nacl.signing.VerifyKey)
            else nacl.signing.VerifyKey(public_key)
        )
        verify_key.verify(hash_data, signature)
        return True
    except (ValueError, TypeError, nacl.exceptions.BadSignatureError):
        return False


def base58btc_encode(value: bytes) -> str:
    number = int.from_bytes(value, "big")
    encoded = ""
    while number:
        number, remainder = divmod(number, 58)
        encoded = _BASE58_ALPHABET[remainder] + encoded
    leading_zeroes = len(value) - len(value.lstrip(b"\0"))
    return "z" + (_BASE58_ALPHABET[0] * leading_zeroes) + encoded


def base58btc_decode(value: str) -> bytes:
    if not value.startswith("z"):
        raise ValueError("Expected a base58-btc multibase value")
    encoded = value[1:]
    if not encoded:
        raise ValueError("Base58-btc value cannot be empty")

    number = 0
    for character in encoded:
        try:
            digit = _BASE58_ALPHABET.index(character)
        except ValueError as error:
            raise ValueError("Invalid base58-btc character") from error
        number = number * 58 + digit

    decoded = number.to_bytes((number.bit_length() + 7) // 8, "big") if number else b""
    leading_zeroes = len(encoded) - len(encoded.lstrip(_BASE58_ALPHABET[0]))
    return (b"\0" * leading_zeroes) + decoded