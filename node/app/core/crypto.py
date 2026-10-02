"""RFC 8785 / Ed25519 cryptographic primitives for CLIP authorities."""

import base64
import hashlib
import os
from typing import Any

import nacl.exceptions
import nacl.signing
import rfc8785


_BASE58_ALPHABET = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


class NodeKeyManager:
    """
    Manages an Ed25519 signing keypair for a CLIP node.

    Usage:
        km = NodeKeyManager.load_or_create("./node_key.bin")
        sig = km.sign_record(record_dict)
        ok  = NodeKeyManager.verify(record_dict, sig, other_node_pubkey_b64)
    """

    def __init__(self, private_key_bytes: bytes | None = None):
        if private_key_bytes is not None:
            self._signing_key = nacl.signing.SigningKey(private_key_bytes)
        else:
            self._signing_key = nacl.signing.SigningKey.generate()
        self._verify_key = self._signing_key.verify_key

    # ------------------------------------------------------------------
    # Key Properties
    # ------------------------------------------------------------------

    @property
    def public_key_b64(self) -> str:
        """Base64-encoded (standard) 32-byte Ed25519 public key."""
        return base64.b64encode(bytes(self._verify_key)).decode()

    @property
    def public_key_multibase(self) -> str:
        """Ed25519 public key as a did:key-compatible multibase fingerprint."""
        return "z" + _base58btc_encode(b"\xed\x01" + bytes(self._verify_key))

    @property
    def private_key_bytes(self) -> bytes:
        """Raw 32-byte private key (seed). Keep this secret."""
        return bytes(self._signing_key)

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    def sign_record(self, record: dict[str, Any]) -> str:
        """
        Sign a record dict and return a base64url-encoded Ed25519 proof value.

        The top-level proof is excluded so it can travel with the signed data.
        """
        payload = canonicalize(record)
        signed = self._signing_key.sign(payload)
        return base64.urlsafe_b64encode(signed.signature).decode().rstrip("=")

    # ------------------------------------------------------------------
    # Verification (static — doesn't need the private key)
    # ------------------------------------------------------------------

    @staticmethod
    def verify(record: dict[str, Any], signature_b64: str, public_key_b64: str) -> bool:
        """
        Verify an Ed25519 signature over a record dict.

        Args:
            record:         The record document (may include its proof).
            signature_b64:  Base64url-encoded 64-byte signature.
            public_key_b64: Base64-encoded 32-byte Ed25519 public key.

        Returns:
            True if the signature is valid, False otherwise.
        """
        try:
            pub_key_bytes = base64.b64decode(public_key_b64)
            verify_key = nacl.signing.VerifyKey(pub_key_bytes)
            payload = canonicalize(record)
            padding = "=" * (-len(signature_b64) % 4)
            sig_bytes = base64.urlsafe_b64decode(signature_b64 + padding)
            verify_key.verify(payload, sig_bytes)
            return True
        except (nacl.exceptions.BadSignatureError, Exception):
            return False

    # ------------------------------------------------------------------
    # Key Persistence
    # ------------------------------------------------------------------

    @classmethod
    def load_or_create(cls, key_file: str) -> "NodeKeyManager":
        """
        Load a node keypair from a file, or generate a new one if the
        file does not exist. The generated key is saved to the file.
        """
        if os.path.exists(key_file):
            with open(key_file, "rb") as f:
                return cls(f.read())

        km = cls()
        os.makedirs(os.path.dirname(os.path.abspath(key_file)), exist_ok=True)
        with open(key_file, "wb") as f:
            f.write(km.private_key_bytes)
        print(f"[clip] Generated new Ed25519 keypair -> {key_file}")
        print(f"[clip] Public key: {km.public_key_b64}")
        return km


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def canonicalize(document: dict[str, Any]) -> bytes:
    """Return RFC 8785 canonical bytes, excluding only a top-level proof."""
    unsigned = {key: value for key, value in document.items() if key != "proof"}
    return rfc8785.dumps(unsigned)


def canonical_sha256(document: dict[str, Any]) -> str:
    """Return the digest used by snapshot relationship integrity policies."""
    return hashlib.sha256(canonicalize(document)).hexdigest()


def _base58btc_encode(value: bytes) -> str:
    leading_zeroes = len(value) - len(value.lstrip(b"\0"))
    number = int.from_bytes(value, "big")
    encoded = bytearray()
    while number:
        number, remainder = divmod(number, 58)
        encoded.append(_BASE58_ALPHABET[remainder])
    encoded.extend(_BASE58_ALPHABET[0] for _ in range(leading_zeroes))
    encoded.reverse()
    return encoded.decode()


def sha256_hex(data: str) -> str:
    """SHA-256 hash of a UTF-8 string, returned as hex string."""
    return hashlib.sha256(data.encode()).hexdigest()
