import base64
import json
import hashlib
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
import nacl.public
import nacl.signing

from node.app import dependencies
from node.app.api import clip_evidence
from node.app.config import settings
from node.app.core.content_crypto import EncryptedFragment, EncryptedManifest, decrypt_fragments
from node.app.core.did import verify_clip_message_proof
from node.app.core.egress import allowed_addresses, decode_json, EgressError, PublicNetworkBackend
from node.app.db.database import AsyncSessionLocal
from node.app.db.orm_models import ClipEvidenceRecord, ClipProjectInvite, IfcDatasetTrustPolicy
from node.app.federation.clip_replication import sign_service_message
from node.app.main import app
from node.app.imports.ifcx import map_cobie, SOURCE_SCHEMA
from node.app.imports.ifcx import map_ifc43
from node.app.db.migrations import REVISIONS, upgrade
from sqlalchemy import create_engine, text, update
import httpx
from client.clip_client import CLIPClient
from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import verify_data_integrity_proof
from node.app.core.clip_protocol import ClipServiceMessage
from node.app.federation.clip_replication import authenticate_service_message


class IfcxOperationsTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.directory.name)
        values = {
            "DATABASE_URL": f"sqlite+aiosqlite:///{(self.root / 'node.db').as_posix()}",
            "PRIVATE_KEY_FILE": str(self.root / "node.key"),
            "DOCUMENT_STORAGE_DIR": str(self.root / "documents"),
            "DID_WEB_ID": "did:web:owner.example",
            "DID_VERIFICATION_METHOD": "did:web:owner.example#authority-key",
            "NODE_API_BASE": "https://owner.example", "API_KEY": "test-key",
            "CLIP_GOSSIP_ENABLED": False, "CLIP_GOSSIP_SEEDS": "",
            "DID_PREVIOUS_KEYS": [], "DID_REVOKED_METHODS": [],
            "CLIP_TRUSTED_PUBLISHERS": "did:web:owner.example",
        }
        self.original_keys = dependencies._key_manager
        dependencies._key_manager = None
        for name, value in values.items():
            modifier = patch.object(settings, name, value)
            modifier.start()
            self.addCleanup(modifier.stop)
        self.client = TestClient(app)
        self.client.__enter__()
        self.addCleanup(self.cleanup)
        self.headers = {"x-api-key": "test-key"}
        self.document = self.client.get("/.well-known/did.json").json()
        self.file = {
            "header": {"id": "urn:owner:test", "ifcxVersion": "ifcx_alpha", "dataVersion": "1.0.0", "author": "owner", "timestamp": "2026-10-01T00:00:00Z"},
            "imports": [], "schemas": {clip_evidence.EVIDENCE_SCHEMA: {"value": {"dataType": "Object", "objectRestrictions": {"values": {
                "evidenceId": {"dataType": "String"}, "publisherDid": {"dataType": "String"}, "uri": {"dataType": "String"}, "integrity": {"dataType": "String"},
            }}}}}, "data": [{"path": "door-1"}],
        }
        response = self.client.post("/ifc/v1/datasets", json={"file": self.file, "trustedProposers": []}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)

    def cleanup(self):
        self.client.__exit__(None, None, None)
        dependencies._key_manager = self.original_keys
        self.directory.cleanup()

    def upload(self):
        with patch.object(clip_evidence, "resolve_did_web_document", AsyncMock(return_value=self.document)):
            response = self.client.post("/clip/v1/evidence/upload", headers=self.headers, json={
                "target": {"authorityDid": settings.DID_WEB_ID, "datasetId": "urn:owner:test", "entityPath": "door-1", "componentSchemaId": clip_evidence.EVIDENCE_SCHEMA},
                "name": "inspection.txt", "content": base64.b64encode(b"inspection evidence" * 100).decode(),
                "recipients": [settings.DID_WEB_ID], "retentionSeconds": 3600, "chunkSize": 1024,
            })
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_ifcx_only_routes(self):
        self.assertFalse(any(route.path.startswith("/v3") for route in app.routes))
        self.assertEqual(self.client.get("/v3/records").status_code, 404)
        self.assertEqual(self.client.get("/clip/v1/node/info").json()["did"], settings.DID_WEB_ID)
        self.assertEqual(len(self.client.get("/ifc/v1/datasets").json()["items"]), 1)
        self.assertIn("door-1", self.client.get("/ifc/v1/datasets/urn:owner:test/graph").json()["entities"])

    def test_demo_open_access_allows_operator_requests_without_a_key(self):
        with patch.object(settings, "CLIP_DEMO_OPEN_ACCESS", True):
            response = self.client.post(
                "/clip/v1/projects",
                json={"name": "Open demo project"},
            )
            datasets = self.client.get("/ifc/v1/datasets")
            info = self.client.get("/clip/v1/node/info")
            self.assertTrue(dependencies.valid_api_key(None))
        self.assertEqual(response.status_code, 201, response.text)
        self.assertIn(response.json()["projectId"], {
            item["datasetId"] for item in datasets.json()["items"]
        })
        self.assertTrue(info.json()["demoOpenAccess"])

    def test_operator_access_still_requires_a_key_outside_demo_mode(self):
        with patch.object(settings, "CLIP_DEMO_OPEN_ACCESS", False):
            self.assertFalse(dependencies.valid_api_key(None))
            self.assertTrue(dependencies.valid_api_key("test-key"))

    def test_request_limits_and_ambiguous_json(self):
        response = self.client.post("/ifc/v1/datasets", content='{"file":{},"file":{}}', headers={**self.headers, "content-type": "application/json"})
        self.assertEqual(response.status_code, 400)
        with patch.object(settings, "CLIP_MAX_SERVICE_BYTES", 8):
            self.assertEqual(self.client.post("/ifc/v1/datasets", content="0123456789", headers=self.headers).status_code, 413)

    def test_project_creation_is_private_and_all_dataset_reads_are_guarded(self):
        response = self.client.post("/clip/v1/projects", json={"name": "Owner facility"}, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        project = response.json()
        self.assertEqual(project["visibility"], "private")
        project_id = project["projectId"]
        self.assertNotIn(project_id, [item["datasetId"] for item in self.client.get("/ifc/v1/datasets").json()["items"]])
        for suffix in ("graph", "publication", "history", "components?entity_path=project&component_schema_id=ifc::name"):
            path = f"/ifc/v1/datasets/{project_id}/{suffix}"
            self.assertEqual(self.client.get(path).status_code, 404, suffix)
            self.assertEqual(self.client.get(path, headers=self.headers).status_code, 200, suffix)
        self.assertEqual(self.client.get("/clip/v1/projects").status_code, 401)

    def test_project_membership_updates_are_revision_checked_and_contributors_only(self):
        project = self.client.post("/clip/v1/projects", json={"name": "Delivery"}, headers=self.headers).json()
        path = f"/clip/v1/projects/{project['projectId']}/permissions"
        body = {"visibility": "private", "expectedRevision": 1, "members": {"did:web:client.example": "viewer", "did:web:supplier.example": "contributor"}}
        response = self.client.put(path, headers=self.headers, json=body)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["revision"], 2)
        self.assertEqual(self.client.put(path, headers=self.headers, json=body).status_code, 409)
        self.assertEqual(self.client.put(path, headers=self.headers, json={**body, "expectedRevision": 2, "members": {"invalid": "viewer"}}).status_code, 422)
        public = self.client.put(path, headers=self.headers, json={"visibility": "public", "expectedRevision": 2, "members": {}})
        self.assertEqual(public.status_code, 200)
        self.assertEqual(self.client.get(f"/ifc/v1/datasets/{project['projectId']}/graph").status_code, 200)

    def test_private_projects_block_unrelated_full_authority_replication(self):
        self.client.post("/clip/v1/projects", json={"name": "Unrelated confidential project"}, headers=self.headers)
        response = self.client.post("/clip/v1/replication/push", headers=self.headers, json={"datasetId": "urn:owner:test", "peerDid": "did:web:relay.example"})
        self.assertEqual(response.status_code, 403, response.text)

    def project_invitation(self, role="viewer"):
        project = self.client.post("/clip/v1/projects", headers=self.headers, json={"name": "Invited project"}).json()
        response = self.client.post(f"/clip/v1/projects/{project['projectId']}/invites", headers=self.headers, json={"role": role})
        self.assertEqual(response.status_code, 201, response.text)
        invite = response.json()
        encoded = invite["code"].split(".", 1)[1]
        content = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        return project, invite, content

    def signed_partner_message(self, kind, payload, actor="did:web:partner.example"):
        partner_key = NodeKeyManager(bytes(range(32)))
        with patch.object(settings, "DID_WEB_ID", actor), patch.object(settings, "DID_VERIFICATION_METHOD", actor + "#authority-key"), patch.object(dependencies, "_key_manager", partner_key):
            document = self.client.get("/.well-known/did.json").json()
            message = sign_service_message(kind, "did:web:owner.example", payload)
        return message, document

    def submit_invite(self, content, actor="did:web:partner.example"):
        message, document = self.signed_partner_message("projectJoin", {"inviteId": content["inviteId"],
            "tokenDigest": hashlib.sha256(content["token"].encode()).hexdigest()}, actor)
        body = {"token": content["token"], "message": message}
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            response = self.client.post("/clip/v1/projects/invites/receive", json=body)
        return response, body, document

    def test_invite_join_requires_owner_acceptance_and_preserves_signed_proofs(self):
        project, invite, content = self.project_invitation("contributor")
        project_id = project["projectId"]
        path = f"/clip/v1/projects/{project_id}/join-requests"
        self.assertEqual(self.client.get(path).status_code, 401)
        response, body, document = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(verify_clip_message_proof(response.json(), self.document))
        request_id = response.json()["payload"]["joinRequestId"]
        projects = self.client.get("/clip/v1/projects", headers=self.headers).json()["items"]
        self.assertEqual(projects[0]["members"], {})
        self.assertEqual(projects[0]["pendingJoinRequests"], 1)
        read, _ = self.signed_partner_message("projectRead", {"projectId": project_id})
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            self.assertEqual(self.client.post("/clip/v1/projects/read", json=read).status_code, 404)
            retry = self.client.post("/clip/v1/projects/invites/receive", json=body)
            self.assertEqual(retry.json()["payload"]["joinRequestId"], request_id)
        rows = self.client.get(path, headers=self.headers).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertTrue(verify_clip_message_proof(rows[0]["request"], document))
        self.assertNotIn(content["token"], json.dumps(rows))
        decision_path = f"/clip/v1/projects/join-requests/{request_id}/decision"
        self.assertEqual(self.client.post(decision_path, json={"decision": "accept", "expectedRevision": 1}).status_code, 401)
        with patch("node.app.api.clip_projects.resolve_did_web_document", AsyncMock(return_value=document)):
            accepted = self.client.post(decision_path, headers=self.headers, json={"decision": "accept", "expectedRevision": 1})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertTrue(verify_clip_message_proof(accepted.json(), self.document))
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {"did:web:partner.example": "contributor"})
        async def trusted_proposers():
            async with AsyncSessionLocal() as session:
                trust = await session.get(IfcDatasetTrustPolicy, (settings.DID_WEB_ID, project_id))
                return trust.trusted_proposers
        self.assertEqual(self.client.portal.call(trusted_proposers), ["did:web:partner.example"])
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            self.assertEqual(self.client.post("/clip/v1/projects/read", json=read).status_code, 200)
        self.assertEqual(self.client.post(decision_path, headers=self.headers, json={"decision": "accept", "expectedRevision": 2}).status_code, 409)
        self.assertNotIn("code", self.client.get(f"/clip/v1/projects/{project_id}/invites", headers=self.headers).json()["items"][0])

    def test_invite_rejection_revocation_and_single_use_do_not_grant_membership(self):
        project, invite, content = self.project_invitation()
        first, _, _ = self.submit_invite(content)
        self.assertEqual(first.status_code, 200, first.text)
        second, _, _ = self.submit_invite(content, "did:web:other.example")
        self.assertEqual(second.status_code, 409)
        request_id = first.json()["payload"]["joinRequestId"]
        rejected = self.client.post(f"/clip/v1/projects/join-requests/{request_id}/decision", headers=self.headers,
            json={"decision": "reject", "expectedRevision": 1})
        self.assertEqual(rejected.status_code, 200, rejected.text)
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {})
        self.assertEqual(self.submit_invite(content)[0].status_code, 410)
        response = self.client.post(f"/clip/v1/projects/{project['projectId']}/invites", headers=self.headers, json={})
        revoked = response.json()
        encoded = revoked["code"].split(".", 1)[1]
        revoked_content = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(self.client.post(f"/clip/v1/projects/invites/{revoked['inviteId']}/revoke", headers=self.headers).status_code, 200)
        self.assertEqual(self.submit_invite(revoked_content)[0].status_code, 410)

    def test_invite_tokens_are_signature_bound_and_decisions_revision_checked(self):
        project, invite, content = self.project_invitation()
        response, body, document = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            self.assertEqual(self.client.post("/clip/v1/projects/invites/receive", json={**body, "token": "A" * 43}).status_code, 403)
            tampered = deepcopy(body)
            tampered["message"]["actorDid"] = "did:web:other.example"
            self.assertEqual(self.client.post("/clip/v1/projects/invites/receive", json=tampered).status_code, 401)
        self.client.put(f"/clip/v1/projects/{project['projectId']}/permissions", headers=self.headers,
            json={"visibility": "private", "members": {}, "expectedRevision": 1})
        decision_path = f"/clip/v1/projects/join-requests/{response.json()['payload']['joinRequestId']}/decision"
        stale = self.client.post(decision_path, headers=self.headers, json={"decision": "accept", "expectedRevision": 1})
        self.assertEqual(stale.status_code, 409)
        self.assertEqual(self.client.get(f"/clip/v1/projects/{project['projectId']}/join-requests", headers=self.headers).json()["items"][0]["status"], "pending")
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {})
        self.assertEqual(self.client.post("/clip/v1/projects/invites/redeem", headers=self.headers, json={"code": "invalid"}).status_code, 422)
        self.assertEqual(self.client.post("/clip/v1/projects/invites/redeem", headers=self.headers, json={"code": invite["code"]}).status_code, 409)

    def test_invite_expiry_and_key_revocation_block_acceptance(self):
        project, invite, content = self.project_invitation()
        response, _, document = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        decision_path = f"/clip/v1/projects/join-requests/{response.json()['payload']['joinRequestId']}/decision"
        revoked_document = {**document, "revokedVerificationMethods": [document["authentication"][0]]}
        with patch("node.app.api.clip_projects.resolve_did_web_document", AsyncMock(return_value=revoked_document)):
            self.assertEqual(self.client.post(decision_path, headers=self.headers, json={"decision": "accept", "expectedRevision": 1}).status_code, 403)
        async def expire_invite():
            async with AsyncSessionLocal() as session:
                await session.execute(update(ClipProjectInvite).where(ClipProjectInvite.invite_id == invite["inviteId"])
                    .values(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)))
                await session.commit()
        self.client.portal.call(expire_invite)
        self.assertEqual(self.submit_invite(content)[0].status_code, 410)
        self.assertEqual(self.client.post(decision_path, headers=self.headers, json={"decision": "accept", "expectedRevision": 1}).status_code, 410)
        self.assertEqual(self.client.post(decision_path, headers=self.headers, json={"decision": "reject", "expectedRevision": 1}).status_code, 200)
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {})

    def test_concurrent_invite_redemption_has_only_one_pending_request(self):
        project, invite, content = self.project_invitation()
        bodies = []
        documents = {}
        for actor in ("did:web:first.example", "did:web:second.example"):
            message, document = self.signed_partner_message("projectJoin", {"inviteId": content["inviteId"],
                "tokenDigest": hashlib.sha256(content["token"].encode()).hexdigest()}, actor)
            documents[actor] = document
            bodies.append({"token": content["token"], "message": message})
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(side_effect=lambda did: documents[did])):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(self.client.post, "/clip/v1/projects/invites/receive", json=body) for body in bodies]
                responses = [future.result() for future in futures]
        self.assertEqual(sorted(response.status_code for response in responses), [200, 409])
        rows = self.client.get(f"/clip/v1/projects/{project['projectId']}/join-requests", headers=self.headers).json()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {})

    def test_revoked_pending_invite_cannot_be_accepted(self):
        project, invite, content = self.project_invitation()
        response, _, _ = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.client.post(f"/clip/v1/projects/invites/{invite['inviteId']}/revoke", headers=self.headers).status_code, 200)
        path = f"/clip/v1/projects/join-requests/{response.json()['payload']['joinRequestId']}/decision"
        self.assertEqual(self.client.post(path, headers=self.headers, json={"decision": "accept", "expectedRevision": 1}).status_code, 409)
        self.assertEqual(self.client.post(path, headers=self.headers, json={"decision": "reject", "expectedRevision": 1}).status_code, 200)
        self.assertEqual(self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]["members"], {})

    def test_viewer_invite_cannot_request_contributor_permissions(self):
        project, invite, content = self.project_invitation()
        message, document = self.signed_partner_message("projectJoin", {"inviteId": content["inviteId"],
            "tokenDigest": hashlib.sha256(content["token"].encode()).hexdigest(), "role": "contributor"})
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            response = self.client.post("/clip/v1/projects/invites/receive", json={"token": content["token"], "message": message})
        self.assertEqual(response.status_code, 403)
        response, _, document = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        with patch("node.app.api.clip_projects.resolve_did_web_document", AsyncMock(return_value=document)):
            accepted = self.client.post(f"/clip/v1/projects/join-requests/{response.json()['payload']['joinRequestId']}/decision",
                headers=self.headers, json={"decision": "accept", "expectedRevision": 1})
        self.assertEqual(accepted.status_code, 200, accepted.text)
        async def trusted_proposers():
            async with AsyncSessionLocal() as session:
                trust = await session.get(IfcDatasetTrustPolicy, (settings.DID_WEB_ID, project["projectId"]))
                return trust.trusted_proposers
        self.assertEqual(self.client.portal.call(trusted_proposers), [])

    def test_expiry_during_acceptance_rolls_back_membership(self):
        project, invite, content = self.project_invitation()
        response, _, document = self.submit_invite(content)
        self.assertEqual(response.status_code, 200, response.text)
        async def expire_while_verifying(_):
            async with AsyncSessionLocal() as session:
                await session.execute(update(ClipProjectInvite).where(ClipProjectInvite.invite_id == invite["inviteId"])
                    .values(expires_at=datetime.now(timezone.utc)))
                await session.commit()
            return document
        with patch("node.app.api.clip_projects.resolve_did_web_document", AsyncMock(side_effect=expire_while_verifying)):
            accepted = self.client.post(f"/clip/v1/projects/join-requests/{response.json()['payload']['joinRequestId']}/decision",
                headers=self.headers, json={"decision": "accept", "expectedRevision": 1})
        self.assertEqual(accepted.status_code, 409, accepted.text)
        policy = self.client.get("/clip/v1/projects", headers=self.headers).json()["items"][0]
        self.assertEqual(policy["members"], {})
        self.assertEqual(policy["revision"], 1)

    def test_guided_project_creation_preserves_ifc_hierarchy_and_signed_history(self):
        project_id = self.client.post("/clip/v1/projects", json={"name": "Facility"}, headers=self.headers).json()["projectId"]
        path = f"/ifc/v1/projects/{project_id}/entities"
        response = self.client.post(path, headers=self.headers, json={"name": "Main building", "template": "building", "parentPath": "project"})
        self.assertEqual(response.status_code, 201, response.text)
        building = response.json()
        self.assertEqual(building["ifcClass"], "IfcBuilding")
        self.assertEqual(building["receipt"]["sequence"], 1)
        self.assertTrue(verify_clip_message_proof(building["receipt"], self.document))
        invalid = self.client.post(path, headers=self.headers, json={"name": "Invalid", "template": "door", "parentPath": "project"})
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(self.client.post(path, headers=self.headers, json={"name": "Invalid", "template": "unsupported"}).status_code, 422)
        space = self.client.post(path, headers=self.headers, json={"name": "Room", "template": "space", "parentPath": building["entityPath"]})
        self.assertEqual(space.status_code, 201, space.text)
        graph = self.client.get(f"/ifc/v1/datasets/{project_id}/graph", headers=self.headers).json()
        self.assertIn(building["entityPath"], graph["entities"]["project"]["children"].values())
        self.assertIn(space.json()["entityPath"], graph["entities"][building["entityPath"]]["children"].values())
        history = self.client.get(f"/ifc/v1/datasets/{project_id}/history", headers=self.headers).json()["items"]
        self.assertEqual(len(history), 2)
        for entry in history:
            self.assertTrue(verify_clip_message_proof(entry["transaction"]["proposal"], self.document))
            self.assertTrue(verify_clip_message_proof(entry["transaction"]["decision"], self.document))

    def test_partner_reads_inherit_membership_and_revocation(self):
        project_id = self.client.post("/clip/v1/projects", json={"name": "Delivery"}, headers=self.headers).json()["projectId"]
        permission_path = f"/clip/v1/projects/{project_id}/permissions"
        partner = "did:web:client.example"
        self.client.put(permission_path, headers=self.headers, json={"visibility": "private", "expectedRevision": 1, "members": {partner: "viewer"}})
        with patch.object(settings, "DID_WEB_ID", partner), patch.object(settings, "DID_VERIFICATION_METHOD", partner + "#authority-key"):
            document = self.client.get("/.well-known/did.json").json()
            request = sign_service_message("projectRead", "did:web:owner.example", {"projectId": project_id})
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=document)):
            response = self.client.post("/clip/v1/projects/read", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(verify_clip_message_proof(response.json(), self.document))
            self.assertNotIn("members", response.json()["payload"]["project"])
            self.client.post(f"/ifc/v1/projects/{project_id}/entities", headers=self.headers, json={"template": "building", "name": "New building", "parentPath": "project"})
            self.assertEqual(len(self.client.post("/clip/v1/projects/read", json=request).json()["payload"]["graph"]["entities"]), 2)
            self.client.put(permission_path, headers=self.headers, json={"visibility": "private", "expectedRevision": 2, "members": {}})
            self.assertEqual(self.client.post("/clip/v1/projects/read", json=request).status_code, 404)

    def test_public_product_library_is_separate_from_private_projects(self):
        library = self.client.post("/clip/v1/projects", headers=self.headers, json={"name": "Manufacturer products", "kind": "product-library", "visibility": "public"}).json()
        path = f"/ifc/v1/projects/{library['projectId']}/entities"
        product = self.client.post(path, headers=self.headers, json={"name": "Door X", "template": "door-type"})
        self.assertEqual(product.status_code, 201, product.text)
        graph = self.client.get(f"/ifc/v1/datasets/{library['projectId']}/graph").json()
        self.assertIn("library", graph["entities"])
        self.assertNotIn("project", graph["entities"])
        self.assertEqual(self.client.post(path, headers=self.headers, json={"name": "Installed door", "template": "door"}).status_code, 422)

    def test_project_evidence_recipients_are_inherited(self):
        project_id = self.client.post("/clip/v1/projects", json={"name": "Evidence project"}, headers=self.headers).json()["projectId"]
        target = {"authorityDid": settings.DID_WEB_ID, "datasetId": project_id, "entityPath": "project", "componentSchemaId": clip_evidence.EVIDENCE_SCHEMA}
        body = {"target": target, "name": "record.txt", "content": base64.b64encode(b"project evidence").decode(), "retentionSeconds": 3600}
        with patch.object(clip_evidence, "resolve_did_web_document", AsyncMock(return_value=self.document)):
            response = self.client.post("/clip/v1/evidence/upload", headers=self.headers, json=body)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual([item["did"] for item in response.json()["manifest"]["manifest"]["recipients"]], [settings.DID_WEB_ID])
        response = self.client.post("/clip/v1/evidence/upload", headers=self.headers, json={**body, "recipients": ["did:web:outsider.example"]})
        self.assertEqual(response.status_code, 403)

    def test_visual_dashboard_assets_are_local_and_allowlisted(self):
        response = self.client.get("/ui")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="network"', response.text)
        self.assertIn('id="asset-tree"', response.text)
        for name, content_type in {
            "dashboard.css": "text/css", "dashboard.js": "text/javascript",
            "d3.min.js": "text/javascript", "lucide.min.js": "text/javascript",
        }.items():
            asset = self.client.get(f"/ui/static/{name}")
            self.assertEqual(asset.status_code, 200, name)
            self.assertTrue(asset.headers["content-type"].startswith(content_type))
        self.assertEqual(self.client.get("/ui/static/config.py").status_code, 404)

    def test_cobie_mapping_preserves_properties_and_type_inheritance(self):
        file = map_cobie("Name,TypeName,SerialNumber\nDoor-1,FireDoor,SN-1\n", "Name,Manufacturer\nFireDoor,Northstar\n", dataset_id="urn:cobie:test", author=settings.DID_WEB_ID)
        nodes = {node.path: node for node in file.data}
        self.assertEqual(nodes["components/Door-1"].inherits, {"type": "types/FireDoor"})
        self.assertEqual(nodes["components/Door-1"].attributes[SOURCE_SCHEMA]["properties"]["SerialNumber"], "SN-1")
        with self.assertRaises(ValueError):
            map_cobie("Name,TypeName\nDoor-1,Missing\n", "", dataset_id="urn:cobie:test", author=settings.DID_WEB_ID)

    def test_ifc43_mapping_uses_ifcopenshell(self):
        import ifcopenshell
        import ifcopenshell.guid

        model = ifcopenshell.file(schema="IFC4X3_ADD2")
        guid = ifcopenshell.guid.new()
        model.create_entity("IfcDoor", GlobalId=guid, Name="Door-43")
        file = map_ifc43(model.to_string(), dataset_id="urn:ifc:test", author=settings.DID_WEB_ID)
        self.assertEqual(file.data[0].attributes["ifc::name"], "Door-43")
        self.assertEqual(file.data[0].attributes[SOURCE_SCHEMA]["class"], "IfcDoor")
    def test_recipient_key_delivery_and_fragment_integrity(self):
        result = self.upload()
        signed = result["manifest"]
        self.assertTrue(verify_clip_message_proof(signed, self.document))
        self.assertNotIn("encryption_key", result)
        manifest = signed["manifest"]
        private = nacl.signing.SigningKey(dependencies.get_key_manager().private_key_bytes).to_curve25519_private_key()
        key = nacl.public.SealedBox(private).decrypt(base64.b64decode(manifest["recipients"][0]["wrappedKey"]))
        fragments = []
        for item in manifest["fragments"]:
            response = self.client.get(f"/clip/v1/evidence/{signed['evidenceId']}/fragments/{item['index']}", headers=self.headers)
            self.assertEqual(response.status_code, 200)
            fragments.append(EncryptedFragment(item["index"], response.content[:24], response.content[24:], item["sha256"]))
        decrypted = decrypt_fragments(key, EncryptedManifest(manifest["contentSha256"], manifest["encryptedContentSha256"], manifest["chunkSize"], manifest["fragmentCount"], tuple(manifest["fragments"])), fragments)
        self.assertEqual(decrypted, b"inspection evidence" * 100)
        clip_evidence.fragment_path(signed["evidenceId"], 0).write_bytes(b"corrupt")
        self.assertEqual(self.client.get(f"/clip/v1/evidence/{signed['evidenceId']}/fragments/0", headers=self.headers).status_code, 424)

    def test_retention_expiry_and_sweep(self):
        result = self.upload()
        evidence_id = result["manifest"]["evidenceId"]

        async def expire():
            async with AsyncSessionLocal() as session:
                row = await session.get(ClipEvidenceRecord, evidence_id)
                row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                await session.commit()

        self.client.portal.call(expire)
        self.assertEqual(self.client.get(f"/clip/v1/evidence/{evidence_id}", headers=self.headers).status_code, 410)
        response = self.client.post("/clip/v1/evidence/retention/sweep", headers=self.headers)
        self.assertEqual(response.json(), {"deleted": 1})
        self.assertFalse(clip_evidence.fragment_path(evidence_id, 0).exists())

    def test_replication_acknowledgement_and_tamper_rejection(self):
        publication = self.client.get("/ifc/v1/datasets/urn:owner:test/publication").json()
        owner_did = settings.DID_WEB_ID
        message = sign_service_message("replication", "did:web:relay.example", {"publication": publication, "transactions": []})
        settings.DID_WEB_ID = "did:web:relay.example"
        settings.DID_VERIFICATION_METHOD = "did:web:relay.example#authority-key"
        relay_document = self.client.get("/.well-known/did.json").json()
        with patch("node.app.federation.clip_replication.resolve_did_web_document", AsyncMock(return_value=self.document)):
            response = self.client.post("/clip/v1/replication/receive", json=message)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(verify_clip_message_proof(response.json(), relay_document))
            self.assertEqual(response.json()["audienceDid"], owner_did)
            self.assertEqual(self.client.post("/clip/v1/replication/receive", json=message).status_code, 200)
            tampered = deepcopy(message)
            tampered["payload"]["publication"]["file"]["header"]["author"] = "attacker"
            self.assertEqual(self.client.post("/clip/v1/replication/receive", json=tampered).status_code, 401)

    def test_revocation_rejects_existing_proofs(self):
        publication = self.client.get("/ifc/v1/datasets/urn:owner:test/publication").json()
        revoked = deepcopy(self.document)
        revoked["revokedVerificationMethods"] = [settings.DID_VERIFICATION_METHOD]
        self.assertFalse(verify_clip_message_proof(publication, revoked))


class MigrationTest(unittest.TestCase):
    def test_migrations_are_idempotent_and_reject_future_versions(self):
        engine = create_engine("sqlite://")
        with engine.begin() as connection:
            upgrade(connection)
            upgrade(connection)
            self.assertEqual(connection.execute(text("SELECT COUNT(*) FROM clip_schema_revisions")).scalar(), len(REVISIONS))
            connection.execute(text("INSERT INTO clip_schema_revisions VALUES (99, 'future')"))
            with self.assertRaises(RuntimeError):
                upgrade(connection)
        engine.dispose()


class EgressPolicyTest(unittest.IsolatedAsyncioTestCase):
    async def test_public_connection_uses_validated_literal_address(self):
        backend = PublicNetworkBackend()
        backend.backend = AsyncMock()
        with patch("node.app.core.egress.allowed_addresses", AsyncMock(return_value=["93.184.216.34"])):
            await backend.connect_tcp("example.org", 443)
        self.assertEqual(backend.backend.connect_tcp.call_args.args, ("93.184.216.34", 443))

    async def test_private_address_is_rejected(self):
        with patch.object(settings, "CLIP_ALLOW_HTTP_LOOPBACK", False):
            with self.assertRaises(EgressError):
                await allowed_addresses("127.0.0.1", 443)

    async def test_duplicate_json_members_are_rejected(self):
        with self.assertRaises(ValueError):
            decode_json(b'{"id":"owner","id":"attacker"}')
        with self.assertRaises(ValueError):
            decode_json(b'{"value":NaN}')

    async def test_service_proof_timestamp_cannot_be_backdated(self):
        with patch.object(settings, "DID_WEB_ID", "did:web:receiver.example"):
            vector = json.loads((Path(__file__).parent / "vectors" / "ifcx-proof.json").read_text())
            message = {**vector["document"], "proof": {**vector["proofOptions"], "proofValue": vector["proofValue"]}}
            message["created"] = datetime.now(timezone.utc).isoformat()
            typed = ClipServiceMessage.model_validate(message)
            with self.assertRaisesRegex(ValueError, "proof creation"):
                await authenticate_service_message(typed, "replicationAcknowledgement")


class ClipSdkTest(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_uses_ifc_graph_route_and_forwards_local_authorization(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"items": []})

        async with CLIPClient("https://owner.example", "local-key") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            self.assertEqual(await client.list_datasets(), [])
        self.assertEqual(calls[0].url.path, "/ifc/v1/datasets")
        self.assertEqual(calls[0].headers["x-api-key"], "local-key")

    async def test_sdk_normalizes_proposals_before_signing(self):
        key = NodeKeyManager(bytes(range(32)))
        proposal = CLIPClient.sign_transaction({
            "actorDid": "did:web:contractor.example", "created": "2026-10-01T00:00:00+00:00",
            "target": {"authorityDid": "did:web:owner.example", "datasetId": "urn:test", "entityPath": "door", "componentSchemaId": "ifc::name"},
            "change": {"action": "set", "value": "Door"}, "schemaDigest": "0" * 64, "expectedSequence": 0,
        }, key, "did:web:contractor.example#authority-key")
        self.assertEqual(proposal["created"], "2026-10-01T00:00:00Z")
        self.assertTrue(verify_data_integrity_proof(proposal, base64.b64decode(key.public_key_b64)))