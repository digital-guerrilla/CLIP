import asyncio
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

import nacl.signing

from node.app.api.clip_network import PeerDigestRequest, sync_peer_digest
from node.app.config import settings
from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.did import verify_clip_message_proof
from node.app.core.clip_network import ClipPeerDigest
from node.app.db.database import close_db, init_db
from node.app.db.orm_models import ClipGossipGeneration
from node.app.federation.clip_gossip import (
    ClipGossipError,
    _next_generation,
    _upsert_unknown_peer,
    accept_signed_peer_digest,
    build_signed_peer_digest,
    get_clip_peers,
)


def _did_document(did: str, key: nacl.signing.SigningKey) -> dict:
    method = f"{did}#gossip-key"
    return {
        "id": did,
        "verificationMethod": [{
            "id": method,
            "type": "Multikey",
            "controller": did,
            "publicKeyMultibase": NodeKeyManager(bytes(key)).public_key_multibase,
        }],
        "authentication": [method],
    }


class ClipGossipTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.previous_did = settings.DID_WEB_ID
        self.previous_method = settings.DID_VERIFICATION_METHOD
        settings.DID_WEB_ID = "did:web:local.example"
        settings.DID_VERIFICATION_METHOD = "did:web:local.example#gossip-key"
        await init_db("sqlite+aiosqlite:///:memory:")

    async def asyncTearDown(self) -> None:
        await close_db()
        settings.DID_WEB_ID = self.previous_did
        settings.DID_VERIFICATION_METHOD = self.previous_method

    async def test_local_generation_persists_and_peer_direct_contact_recovers_alive(self) -> None:
        local_key = nacl.signing.SigningKey(bytes(range(32)))
        remote_did = "did:web:peer.example"
        remote_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
        remote_method = f"{remote_did}#gossip-key"
        remote_document = _did_document(remote_did, remote_key)

        first = await build_signed_peer_digest(NodeKeyManager(bytes(local_key)))
        second = await build_signed_peer_digest(NodeKeyManager(bytes(local_key)))
        self.assertEqual(second.generation, first.generation + 1)
        self.assertTrue(verify_clip_message_proof(
            first.model_dump(mode="json", by_alias=True),
            _did_document(settings.DID_WEB_ID, local_key),
        ))

        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "fromDid": remote_did,
            "generation": 7,
            "knownDids": [],
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        signed = add_data_integrity_proof(
            unsigned,
            remote_key,
            verification_method=remote_method,
            proof_purpose="authentication",
            created=datetime.now(timezone.utc),
        )
        digest = ClipPeerDigest.model_validate(signed)
        await accept_signed_peer_digest(digest, remote_document)
        peers = await get_clip_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].did, remote_did)
        self.assertEqual(peers[0].status, "alive")
        self.assertEqual(peers[0].generation, 7)

    async def test_concurrent_generations_are_unique_and_persist_after_reopen(self) -> None:
        await close_db()
        with tempfile.TemporaryDirectory() as directory:
            database_url = "sqlite+aiosqlite:///" + (Path(directory) / "gossip.db").as_posix()
            await init_db(database_url)
            try:
                for start in (1, 25):
                    results = await asyncio.gather(
                        *(_next_generation(settings.DID_WEB_ID) for _ in range(24)),
                        return_exceptions=True,
                    )
                    self.assertTrue(all(isinstance(result, int) for result in results), repr(results))
                    self.assertEqual(sorted(results), list(range(start, start + 24)))
                await close_db()
                await init_db(database_url)
                self.assertEqual(await _next_generation(settings.DID_WEB_ID), 49)
            finally:
                await close_db()

    async def test_tampered_peer_digest_is_not_registered(self) -> None:
        remote_did = "did:web:peer.example"
        remote_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
        remote_method = f"{remote_did}#gossip-key"
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "fromDid": remote_did,
            "generation": 7,
            "knownDids": [],
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        signed = add_data_integrity_proof(
            unsigned,
            remote_key,
            verification_method=remote_method,
            proof_purpose="authentication",
            created=datetime.now(timezone.utc),
        )
        signed["generation"] = 8
        digest = ClipPeerDigest.model_validate(signed)

        with self.assertRaisesRegex(ClipGossipError, "authentication failed"):
            await accept_signed_peer_digest(digest, _did_document(remote_did, remote_key))
        self.assertEqual(await get_clip_peers(), [])

    async def test_replayed_peer_generation_cannot_refresh_liveness(self) -> None:
        remote_did = "did:web:peer.example"
        remote_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
        remote_method = f"{remote_did}#gossip-key"
        remote_document = _did_document(remote_did, remote_key)
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "fromDid": remote_did,
            "generation": 4,
            "knownDids": [],
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        digest = ClipPeerDigest.model_validate(add_data_integrity_proof(
            unsigned,
            remote_key,
            verification_method=remote_method,
            proof_purpose="authentication",
            created=datetime.now(timezone.utc),
        ))
        self.assertTrue(await accept_signed_peer_digest(digest, remote_document))
        peers_before_replay = await get_clip_peers()

        self.assertFalse(await accept_signed_peer_digest(digest, remote_document))
        peers_after_replay = await get_clip_peers()
        self.assertEqual(peers_after_replay, peers_before_replay)

    async def test_concurrent_bootstrap_and_direct_contact_upserts_are_idempotent(self) -> None:
        remote_did = "did:web:peer.example"
        remote_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
        remote_document = _did_document(remote_did, remote_key)
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "fromDid": remote_did,
            "generation": 7,
            "knownDids": [],
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        digest = ClipPeerDigest.model_validate(add_data_integrity_proof(
            unsigned,
            remote_key,
            verification_method=f"{remote_did}#gossip-key",
            proof_purpose="authentication",
            created=datetime.now(timezone.utc),
        ))

        results = await asyncio.gather(
            _upsert_unknown_peer(remote_did, discovered_from="seed"),
            accept_signed_peer_digest(digest, remote_document),
            return_exceptions=True,
        )
        self.assertIsNone(results[0])
        self.assertIn(results[1], (True, False))
        peers = await get_clip_peers()
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].did, remote_did)
        self.assertEqual(peers[0].status, "alive")
        self.assertEqual(peers[0].generation, 7)

    async def test_sync_api_marks_peer_alive_only_after_did_authenticated_contact(self) -> None:
        local_key = NodeKeyManager(bytes(range(32)))
        remote_did = "did:web:peer.example"
        remote_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
        remote_method = f"{remote_did}#gossip-key"
        remote_document = {
            **_did_document(remote_did, remote_key),
            "service": [{
                "id": f"{remote_did}#clip-gossip",
                "type": "ClipGossipService",
                "serviceEndpoint": "https://peer.example/clip/v1/network/gossip/sync",
            }],
        }
        unsigned = {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "fromDid": remote_did,
            "generation": 12,
            "knownDids": [],
            "created": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        }
        signed = add_data_integrity_proof(
            unsigned,
            remote_key,
            verification_method=remote_method,
            proof_purpose="authentication",
            created=datetime.now(timezone.utc),
        )

        with (
            patch("node.app.api.clip_network.resolve_did_web_document", new=AsyncMock(return_value=remote_document)),
            patch("node.app.api.clip_network.resolve_clip_gossip_endpoint", new=AsyncMock(return_value=remote_document["service"][0]["serviceEndpoint"])),
        ):
            response = await sync_peer_digest(
                PeerDigestRequest.model_validate({"digest": signed}),
                key_manager=local_key,
            )
            peers_after_contact = await get_clip_peers()
            replay_response = await sync_peer_digest(
                PeerDigestRequest.model_validate({"digest": signed}),
                key_manager=local_key,
            )

        self.assertEqual(response.digest.from_did, settings.DID_WEB_ID)
        self.assertGreater(replay_response.digest.generation, response.digest.generation)
        local_document = _did_document(settings.DID_WEB_ID, nacl.signing.SigningKey(local_key.private_key_bytes))
        self.assertTrue(verify_clip_message_proof(
            response.digest.model_dump(mode="json", by_alias=True),
            local_document,
        ))
        peers = await get_clip_peers()
        self.assertEqual(peers, peers_after_contact)
        self.assertEqual(len(peers), 1)
        self.assertEqual(peers[0].did, remote_did)
        self.assertEqual(peers[0].status, "alive")


if __name__ == "__main__":
    unittest.main()