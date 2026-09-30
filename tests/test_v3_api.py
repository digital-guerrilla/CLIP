import base64
import json
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from pathlib import Path

from fastapi.testclient import TestClient

from node.app import dependencies
from node.app.api.assets import replication_endpoints
from node.app.config import settings
from node.app.core.crypto import NodeKeyManager
from node.app.core.guid import parse_clip
from node.app.core.models import WellKnownResponse
from node.app.federation.resolver import _discovery_scheme
from node.app.main import app


class V3ApiTest(unittest.TestCase):
    def test_restricted_replication_uses_explicit_routing_hosts(self) -> None:
        from node.app.core.models import AvailabilityPolicy, GossipPeerState

        settings.NODE_DOMAIN = "127.0.0.1:8999"
        policy = AvailabilityPolicy(
            visibility="restricted",
            allowed_nodes=["127.0.0.1:8998"],
        )
        fingerprint_peer = GossipPeerState(
            node_id="z6MkFingerprint",
            endpoint="http://127.0.0.1:8998",
        )
        self.assertEqual(
            replication_endpoints(policy, [fingerprint_peer]),
            ["http://127.0.0.1:8998"],
        )

    def test_discovery_scheme_requires_explicit_http_opt_in(self) -> None:
        original = settings.ALLOW_INSECURE_HTTP_DISCOVERY
        try:
            settings.ALLOW_INSECURE_HTTP_DISCOVERY = False
            self.assertEqual(_discovery_scheme("authority.example"), "https")
            self.assertEqual(_discovery_scheme("127.0.0.1:8000"), "http")

            settings.ALLOW_INSECURE_HTTP_DISCOVERY = True
            self.assertEqual(_discovery_scheme("node-manufacturer:8000"), "http")
        finally:
            settings.ALLOW_INSECURE_HTTP_DISCOVERY = original

    def test_publish_verify_and_resolve_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / "node.key")
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8999"
            settings.NODE_API_BASE = "http://localhost:8999"
            settings.API_KEY = "test-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                descriptor_response = client.get("/.well-known/clip/server")
                self.assertEqual(descriptor_response.status_code, 200)
                descriptor = descriptor_response.json()

                create_response = client.post(
                    "/v3/records",
                    headers={"x-api-key": "test-key"},
                    json={
                        "record_kind": "type",
                        "subject": {
                            "name": "Fire Door FD60",
                            "manufacturer": "Example Manufacturing",
                            "model_number": "FD60-01",
                        },
                    },
                )
                self.assertEqual(create_response.status_code, 201, create_response.text)
                record = create_response.json()
                self.assertTrue(record["id"].startswith("clip://"))
                parsed = parse_clip(record["id"])

                fetch_response = client.get(
                    f"/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}"
                )
                self.assertEqual(fetch_response.status_code, 200, fetch_response.text)
                method = descriptor["verification_methods"][0]
                self.assertTrue(NodeKeyManager.verify(
                    fetch_response.json(),
                    record["proof"]["proof_value"],
                    method["public_key_base64"],
                ))

                graph_response = client.post(
                    "/v3/resolve-graph",
                    json={"root": record["id"], "depth": 0, "max_nodes": 10},
                )
                self.assertEqual(graph_response.status_code, 200, graph_response.text)
                graph = graph_response.json()
                self.assertTrue(graph["complete"])
                self.assertIn(record["id"], graph["nodes"])

    def test_upload_document_versions_record_and_serves_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / "node.key")
            settings.DOCUMENT_STORAGE_DIR = str(root / "documents")
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8998"
            settings.NODE_API_BASE = "http://localhost:8998"
            settings.API_KEY = "test-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                created = client.post(
                    "/v3/records",
                    headers={"x-api-key": "test-key"},
                    json={"record_kind": "instance", "subject": {"name": "Test Pump"}},
                ).json()
                parsed = parse_clip(created["id"])
                content = b"commissioning evidence"
                upload = client.post(
                    f"/v3/documents/upload/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={
                        "x-api-key": "test-key",
                        "x-file-name": "commissioning.txt",
                        "content-type": "text/plain",
                    },
                    content=content,
                )
                self.assertEqual(upload.status_code, 201, upload.text)
                document = upload.json()

                updated = client.get(
                    f"/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}"
                ).json()
                self.assertEqual(updated["version"], 2)
                self.assertEqual(updated["subject"]["documents"][0]["sha256"], document["sha256"])
                retrieved = client.get(f"/v3/documents/{document['sha256']}")
                self.assertEqual(retrieved.status_code, 200)
                self.assertEqual(retrieved.content, content)

    def test_encrypted_document_stores_fragments_and_reconstructs_with_key(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.DOCUMENT_STORAGE_DIR = str(root / 'documents')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8991"
            settings.NODE_API_BASE = "http://localhost:8991"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                created = client.post(
                    "/v3/records",
                    headers={"x-api-key": "owner-key"},
                    json={"record_kind": "instance", "subject": {"name": "Secure Asset"}},
                ).json()
                parsed = parse_clip(created["id"])
                content = b"confidential inspection certificate" * 100
                upload = client.post(
                    f"/v3/documents/encrypted-upload/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={
                        "x-api-key": "owner-key",
                        "x-file-name": "certificate.txt",
                        "content-type": "text/plain",
                        "x-chunk-size": "1024",
                        "x-key-share-count": "3",
                        "x-key-threshold": "2",
                    },
                    content=content,
                )
                self.assertEqual(upload.status_code, 200, upload.text)
                body = upload.json()
                digest = body["document"]["sha256"]
                fragment = root / "documents" / "encrypted" / digest / "0.bin"
                self.assertTrue(fragment.is_file())
                self.assertNotIn(content[:32], fragment.read_bytes())
                self.assertEqual(
                    client.get(f"/v3/documents/encrypted/{digest}", headers={"x-api-key": "owner-key"}).status_code,
                    401,
                )
                retrieved = client.get(
                    f"/v3/documents/encrypted/{digest}",
                    headers={"x-api-key": "owner-key", "x-document-key": body["encryption_key"]},
                )
                self.assertEqual(retrieved.status_code, 200, retrieved.text)
                self.assertEqual(retrieved.content, content)
                quorum = client.get(
                    f"/v3/documents/encrypted/{digest}",
                    headers={
                        "x-api-key": "owner-key",
                        "x-document-key-shares": ",".join(body["key_shares"][:2]),
                    },
                )
                self.assertEqual(quorum.status_code, 200, quorum.text)
                self.assertEqual(quorum.content, content)

    def test_encrypted_replica_requires_opt_in_and_origin_signature(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.DOCUMENT_STORAGE_DIR = str(root / 'documents')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8990"
            settings.NODE_API_BASE = "http://localhost:8990"
            settings.API_KEY = "owner-key"
            settings.ENCRYPTED_STORAGE_OPT_IN = False
            dependencies._key_manager = None

            with TestClient(app) as client:
                created = client.post(
                    "/v3/records",
                    headers={"x-api-key": "owner-key"},
                    json={"record_kind": "instance", "subject": {"name": "Replica Asset"}},
                ).json()
                parsed = parse_clip(created["id"])
                upload = client.post(
                    f"/v3/documents/encrypted-upload/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={"x-api-key": "owner-key", "x-file-name": "evidence.txt"},
                    content=b"replicated evidence",
                )
                self.assertEqual(upload.status_code, 200, upload.text)
                body = upload.json()
                digest = body["document"]["sha256"]
                fragment_path = root / "documents" / "encrypted" / digest / "0.bin"
                fragment = fragment_path.read_bytes()
                fragment_digest = body["manifest"]["fragments"][0]["sha256"]
                payload = {"sha256": digest, "fragment_index": 0, "fragment_sha256": fragment_digest}
                key_manager = dependencies.get_key_manager()
                signature = key_manager.sign_record(payload)
                manifest = base64.urlsafe_b64encode(
                    json.dumps(body["manifest"], separators=(",", ":")).encode()
                ).decode().rstrip("=")
                headers = {
                    "x-origin-routing-host": settings.NODE_DOMAIN,
                    "x-origin-authority": key_manager.public_key_multibase,
                    "x-fragment-signature": signature,
                    "x-fragment-sha256": fragment_digest,
                    "x-encrypted-manifest": manifest,
                }
                rejected = client.post(
                    f"/v3/documents/encrypted-replica/{digest}/0",
                    headers=headers,
                    content=fragment,
                )
                self.assertEqual(rejected.status_code, 403, rejected.text)

                settings.ENCRYPTED_STORAGE_OPT_IN = True
                storage = client.get("/v3/node/storage", headers={"x-api-key": "owner-key"})
                self.assertEqual(storage.status_code, 200, storage.text)
                self.assertTrue(storage.json()["opted_in"])
                descriptor = WellKnownResponse.model_validate(client.get("/.well-known/clip/server").json())
                with patch(
                    "node.app.federation.resolver.fetch_well_known",
                    new=AsyncMock(return_value=descriptor),
                ):
                    accepted = client.post(
                        f"/v3/documents/encrypted-replica/{digest}/0",
                        headers=headers,
                        content=fragment,
                    )
                self.assertEqual(accepted.status_code, 202, accepted.text)
                receipt = accepted.json()["storage_receipt"]
                method = descriptor.verification_methods[0]
                receipt_payload = {
                    key: value for key, value in receipt.items() if key not in {"authority", "signature"}
                }
                self.assertTrue(NodeKeyManager.verify(
                    receipt_payload,
                    receipt["signature"],
                    method.public_key_base64,
                ))
                revocation_payload = {
                    "sha256": digest,
                    "action": "revoke",
                    "reason": "Evidence withdrawn",
                }
                with patch(
                    "node.app.federation.resolver.fetch_well_known",
                    new=AsyncMock(return_value=descriptor),
                ):
                    revoked = client.post(
                        f"/v3/documents/encrypted-revoke/{digest}",
                        headers={
                            "x-origin-routing-host": settings.NODE_DOMAIN,
                            "x-origin-authority": key_manager.public_key_multibase,
                            "x-revocation-signature": key_manager.sign_record(revocation_payload),
                            "x-revocation-reason": "Evidence withdrawn",
                        },
                    )
                self.assertEqual(revoked.status_code, 202, revoked.text)
                self.assertEqual(
                    client.get(
                        f"/v3/documents/encrypted/{digest}",
                        headers={"x-api-key": "owner-key", "x-document-key": body["encryption_key"]},
                    ).status_code,
                    404,
                )
                settings.ENCRYPTED_STORAGE_OPT_IN = False

    def test_installation_reference_propagates_manufacturer_into_graph(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8996"
            settings.NODE_API_BASE = "http://localhost:8996"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                def create(record_kind: str, name: str) -> dict:
                    response = client.post(
                        "/v3/records",
                        headers={"x-api-key": "owner-key"},
                        json={"record_kind": record_kind, "subject": {"name": name}},
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    return response.json()

                owner = create("instance", "Installed AHU")
                manufacturer = create("type", "AHU-100")
                installation = create("assertion", "Contractor installation")

                proposal = client.post(
                    "/v3/relationships/proposals",
                    headers={"x-api-key": "owner-key"},
                    json={
                        "source": owner["id"],
                        "target": installation["id"],
                        "role": "installer",
                        "relation_type": "installed_by",
                        "references": [manufacturer["id"]],
                    },
                )
                self.assertEqual(proposal.status_code, 201, proposal.text)
                self.assertEqual(proposal.json()["references"], [manufacturer["id"]])

                descriptor = WellKnownResponse.model_validate(
                    client.get("/.well-known/clip/server").json()
                )
                with patch(
                    "node.app.api.relationships.fetch_well_known",
                    new=AsyncMock(return_value=descriptor),
                ):
                    accepted = client.post(
                        "/v3/relationships/accept",
                        headers={"x-api-key": "owner-key"},
                        json=proposal.json(),
                    )
                self.assertEqual(accepted.status_code, 200, accepted.text)

                graph = client.post(
                    "/v3/resolve-graph",
                    json={"root": owner["id"], "depth": 2, "max_nodes": 10},
                )
                self.assertEqual(graph.status_code, 200, graph.text)
                body = graph.json()
                self.assertIn(manufacturer["id"], body["nodes"])
                self.assertEqual(body["references"][0]["target"], manufacturer["id"])
                self.assertEqual(body["references"][0]["provenance"], "relationship_reference")

                relationship_id = accepted.json()["relationship_id"]
                transition = client.post(
                    f"/v3/relationships/{relationship_id}/transition",
                    headers={"x-api-key": "owner-key"},
                    json={"new_state": "disputed", "reason": "Serial number requires review"},
                )
                self.assertEqual(transition.status_code, 200, transition.text)
                self.assertEqual(transition.json()["relationship"]["state"], "disputed")
                self.assertEqual(transition.json()["root_version"], 3)

                illegal = client.post(
                    f"/v3/relationships/{relationship_id}/transition",
                    headers={"x-api-key": "owner-key"},
                    json={"new_state": "proposed", "reason": "Invalid rollback"},
                )
                self.assertEqual(illegal.status_code, 422, illegal.text)

    def test_cobie_import_extracts_clip_and_deduplicates_assets(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8994"
            settings.NODE_API_BASE = "http://localhost:8994"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                manufacturer = client.post(
                    "/v3/records",
                    headers={"x-api-key": "owner-key"},
                    json={
                        "record_kind": "type",
                        "subject": {"name": "AHU-100", "manufacturer": "Global HVAC"},
                    },
                ).json()
                content = (
                    "Name,Manufacturer,ModelNumber,SerialNumber,AssetIdentifier\n"
                    f"North Wing AHU,Global HVAC,AHU-100,AHU-009184,{manufacturer['id']}\n"
                )
                imported = client.post(
                    "/v3/imports/assets",
                    headers={"x-api-key": "owner-key", "x-idempotency-key": "cobie-import-1"},
                    json={"source_format": "csv", "content": content},
                )
                self.assertEqual(imported.status_code, 200, imported.text)
                result = imported.json()
                self.assertEqual(result["created"], 1)
                imported_id = result["rows"][0]["clip"]

                parsed = parse_clip(imported_id)
                record = client.get(
                    f"/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={"x-api-key": "owner-key"},
                ).json()
                self.assertEqual(record["subject"]["linked_clips"], [manufacturer["id"]])

                status = client.get(
                    f"/v3/imports/{result['import_id']}",
                    headers={"x-api-key": "owner-key"},
                )
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["status"], "complete")

                repeated = client.post(
                    "/v3/imports/assets",
                    headers={"x-api-key": "owner-key", "x-idempotency-key": "cobie-import-1"},
                    json={"source_format": "csv", "content": content},
                )
                self.assertEqual(repeated.status_code, 200, repeated.text)
                self.assertEqual(repeated.json()["import_id"], result["import_id"])

                existing = client.post(
                    "/v3/imports/assets",
                    headers={"x-api-key": "owner-key"},
                    json={"source_format": "csv", "content": content},
                )
                self.assertEqual(existing.status_code, 200, existing.text)
                self.assertEqual(existing.json()["created"], 0)
                self.assertEqual(existing.json()["existing"], 1)

    def test_query_and_bulk_relationship_proposals_return_scoped_results(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8993"
            settings.NODE_API_BASE = "http://localhost:8993"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                def create(record_kind: str, subject: dict) -> dict:
                    response = client.post(
                        "/v3/records",
                        headers={"x-api-key": "owner-key"},
                        json={"record_kind": record_kind, "subject": subject},
                    )
                    self.assertEqual(response.status_code, 201, response.text)
                    return response.json()

                owner = create("instance", {
                    "name": "North Wing AHU",
                    "serial_number": "AHU-009184",
                    "site": {"building": "North Wing"},
                })
                installation = create("assertion", {"name": "Installation"})
                invalid = (
                    f"clip://localhost:8993/{parse_clip(installation['id']).authority_key_fingerprint}/"
                    "00000000-0000-4000-8000-000000000001"
                )

                query = client.get(
                    "/v3/records/query",
                    params={"building": "North Wing", "serial_number": "AHU-009184"},
                )
                self.assertEqual(query.status_code, 200, query.text)
                self.assertEqual(query.json()["total"], 1)
                self.assertEqual(query.json()["items"][0]["id"], owner["id"])

                batch = client.post(
                    "/v3/relationships/bulk-proposals",
                    headers={"x-api-key": "owner-key"},
                    json={"proposals": [
                        {
                            "source": owner["id"],
                            "target": installation["id"],
                            "role": "installer",
                            "relation_type": "installed_by",
                        },
                        {
                            "source": owner["id"],
                            "target": invalid,
                            "role": "installer",
                            "relation_type": "installed_by",
                        },
                    ]},
                )
                self.assertEqual(batch.status_code, 201, batch.text)
                self.assertEqual(batch.json()["proposed"], 1)
                self.assertEqual(batch.json()["failed"], 1)

    def test_replication_status_and_retry_are_available_to_owner(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / 'node.key')
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8992"
            settings.NODE_API_BASE = "http://localhost:8992"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                created = client.post(
                    "/v3/records",
                    headers={"x-api-key": "owner-key"},
                    json={"record_kind": "instance", "subject": {"name": "Relay Test Asset"}},
                ).json()
                parsed = parse_clip(created["id"])
                path = f"/v3/replication/status/{parsed.authority_key_fingerprint}/{parsed.record_uuid}"
                status = client.get(path, headers={"x-api-key": "owner-key"})
                self.assertEqual(status.status_code, 200, status.text)
                self.assertEqual(status.json()["record_id"], created["id"])

                retry = client.post(
                    f"/v3/replication/retry/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={"x-api-key": "owner-key"},
                )
                self.assertEqual(retry.status_code, 202, retry.text)

    def test_restricted_record_requires_local_authorization(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / "node.key")
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = "localhost:8997"
            settings.NODE_API_BASE = "http://localhost:8997"
            settings.API_KEY = "owner-key"
            dependencies._key_manager = None

            with TestClient(app) as client:
                created = client.post(
                    "/v3/records",
                    headers={"x-api-key": "owner-key"},
                    json={
                        "record_kind": "instance",
                        "subject": {"name": "Private Installed Asset"},
                        "availability": {
                            "visibility": "restricted",
                            "allowed_nodes": ["localhost:8995"],
                        },
                    },
                ).json()
                parsed = parse_clip(created["id"])
                path = f"/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}"

                anonymous_catalog = client.get("/v3/records").json()
                self.assertEqual(anonymous_catalog["total"], 0)
                self.assertEqual(client.get(path).status_code, 403)

                authorized_catalog = client.get(
                    "/v3/records", headers={"x-api-key": "owner-key"}
                ).json()
                self.assertEqual(authorized_catalog["total"], 1)
                self.assertEqual(
                    client.get(path, headers={"x-api-key": "owner-key"}).status_code,
                    200,
                )
                public_graph = client.post(
                    "/v3/resolve-graph",
                    json={"root": created["id"], "depth": 0, "view": "public"},
                )
                self.assertEqual(public_graph.status_code, 403)
                confidential_graph = client.post(
                    "/v3/resolve-graph",
                    headers={"x-api-key": "owner-key"},
                    json={"root": created["id"], "depth": 0, "view": "confidential"},
                )
                self.assertEqual(confidential_graph.status_code, 200, confidential_graph.text)

                document = client.post(
                    f"/v3/documents/upload/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    headers={
                        "x-api-key": "owner-key",
                        "x-file-name": "private.txt",
                        "content-type": "text/plain",
                    },
                    content=b"private inspection evidence",
                ).json()
                document_path = f"/v3/documents/{document['sha256']}"
                self.assertEqual(client.get(document_path).status_code, 403)
                self.assertEqual(
                    client.get(document_path, headers={"x-api-key": "owner-key"}).status_code,
                    200,
                )

                updated = client.put(
                    path,
                    headers={"x-api-key": "owner-key"},
                    json={
                        "subject": created["subject"],
                        "relationships": created["relationships"],
                        "availability": {
                            **created["availability"],
                            "visibility": "public",
                            "allowed_nodes": [],
                        },
                    },
                )
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["availability"]["visibility"], "public")
                self.assertEqual(client.get(path).status_code, 200)


if __name__ == "__main__":
    unittest.main()