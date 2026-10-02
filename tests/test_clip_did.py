import unittest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import nacl.signing

from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.did import (
    DidVerificationError,
    did_web_document_url,
    resolve_clip_gossip_endpoint,
    resolve_ed25519_verification_key,
    verify_clip_message_proof,
)


class DidVerificationTest(unittest.IsolatedAsyncioTestCase):
    def test_did_web_url_mapping_and_path_rejection(self) -> None:
        self.assertEqual(
            did_web_document_url("did:web:example.com"),
            "https://example.com/.well-known/did.json",
        )
        self.assertEqual(
            did_web_document_url("did:web:example.com%3A8443:org:clip"),
            "https://example.com:8443/org/clip/did.json",
        )
        with self.assertRaisesRegex(DidVerificationError, "path"):
            did_web_document_url("did:web:example.com:..:private")
        with self.assertRaises(DidVerificationError):
            did_web_document_url("did:key:z6MkExample")
        self.assertEqual(
            did_web_document_url(
                "did:web:127.0.0.1%3A8201",
                allow_http_loopback=True,
            ),
            "http://127.0.0.1:8201/.well-known/did.json",
        )
        self.assertEqual(
            did_web_document_url(
                "did:web:manufacturer.example",
                allow_http_loopback=True,
            ),
            "https://manufacturer.example/.well-known/did.json",
        )

    async def test_gossip_endpoint_requires_did_service_and_public_https(self) -> None:
        document = {
            "id": self.did,
            "service": [{
                "id": f"{self.did}#clip-gossip",
                "type": "ClipGossipService",
                "serviceEndpoint": "https://gossip.example/clip/v1/network/gossip/sync",
            }],
        }
        with patch("node.app.core.did.validate_public_host", new=AsyncMock()):
            endpoint = await resolve_clip_gossip_endpoint(document, expected_did=self.did)
        self.assertEqual(endpoint, "https://gossip.example/clip/v1/network/gossip/sync")

        document["service"][0]["serviceEndpoint"] = "http://gossip.example/sync"
        with patch("node.app.core.did.validate_public_host", new=AsyncMock()):
            with self.assertRaisesRegex(DidVerificationError, "HTTPS"):
                await resolve_clip_gossip_endpoint(document, expected_did=self.did)

    def setUp(self) -> None:
        self.did = "did:web:supplier.example"
        self.verification_method = f"{self.did}#assertion-key"
        self.signing_key = nacl.signing.SigningKey(bytes(range(32)))
        self.document = {
            "id": self.did,
            "verificationMethod": [{
                "id": self.verification_method,
                "type": "Multikey",
                "controller": self.did,
                "publicKeyMultibase": NodeKeyManager(
                    bytes(self.signing_key)
                ).public_key_multibase,
            }],
            "assertionMethod": [self.verification_method],
            "capabilityInvocation": [],
        }

    def test_resolves_key_only_for_declared_proof_purpose(self) -> None:
        key = resolve_ed25519_verification_key(
            self.document,
            expected_did=self.did,
            verification_method=self.verification_method,
            proof_purpose="assertionMethod",
        )

        self.assertEqual(bytes(key), bytes(self.signing_key.verify_key))
        with self.assertRaisesRegex(DidVerificationError, "not authorized"):
            resolve_ed25519_verification_key(
                self.document,
                expected_did=self.did,
                verification_method=self.verification_method,
                proof_purpose="capabilityInvocation",
            )

    def test_rejects_foreign_controller_and_malformed_multikey(self) -> None:
        with self.assertRaisesRegex(DidVerificationError, "does not match"):
            resolve_ed25519_verification_key(
                self.document,
                expected_did="did:web:other.example",
                verification_method=self.verification_method,
                proof_purpose="assertionMethod",
            )

    def test_transaction_proof_verification_checks_did_relationship_and_payload(self) -> None:
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "transactionId": "304d4513-caa6-4aa0-84a7-4ecbb1c1ad1d",
            "actorDid": self.did,
            "target": {
                "authorityDid": "did:web:owner.example",
                "datasetId": "building.ifcx",
                "entityPath": "building/door-1",
                "componentSchemaId": "ifc::name",
            },
            "change": {"action": "set", "value": "Fire door"},
            "expectedSequence": 0,
            "schemaDigest": "a" * 64,
            "created": "2026-10-01T00:00:00Z",
        }
        signed = add_data_integrity_proof(
            unsigned,
            self.signing_key,
            verification_method=self.verification_method,
            proof_purpose="assertionMethod",
            created=datetime(2026, 10, 1, tzinfo=timezone.utc),
        )

        self.assertTrue(verify_clip_message_proof(signed, self.document))
        altered = {**signed, "schemaDigest": "b" * 64}
        self.assertFalse(verify_clip_message_proof(altered, self.document))

        malformed = {
            **self.document,
            "verificationMethod": [{
                **self.document["verificationMethod"][0],
                "publicKeyMultibase": "z12345",
            }],
        }
        with self.assertRaisesRegex(DidVerificationError, "Expected an Ed25519 Multikey"):
            resolve_ed25519_verification_key(
                malformed,
                expected_did=self.did,
                verification_method=self.verification_method,
                proof_purpose="assertionMethod",
            )


if __name__ == "__main__":
    unittest.main()