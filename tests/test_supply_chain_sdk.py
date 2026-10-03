"""SDK contract checks for guided organisation workflows."""

import base64
import json
import unittest

import httpx

from client.clip_client import CLIPClient
from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import verify_data_integrity_proof
from node.app.core.ifc_protocol import IfcGraphProposalTransaction


class SupplyChainSdkTest(unittest.IsolatedAsyncioTestCase):
    async def test_graph_product_views_and_explicit_publication_refresh(self):
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path == "/.well-known/did.json":
                return httpx.Response(200, json={"id": "did:web:local.example"})
            if request.url.path.endswith("/components"):
                return httpx.Response(200, json={"value": {"rating": 10}})
            return httpx.Response(200, json={"productResolution": {"definitions": []}})

        async with CLIPClient("https://local.example", "operator-key") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            await client.resolve_graph("project/id", refresh_products=True)
            await client.resolve_graph("project/id", product_view="pinned")
            value = await client.resolve_component({"authorityDid": "did:web:local.example",
                "datasetId": "project/id", "entityPath": "pump", "componentSchemaId": "manufacturer-data"},
                product_view="pinned")
            self.assertEqual(value, {"rating": 10})
        self.assertEqual(calls[0].url.params["refresh_products"], "true")
        self.assertEqual(calls[0].url.params["product_view"], "current")
        self.assertEqual(calls[0].headers["x-api-key"], "operator-key")
        self.assertEqual(calls[1].url.params["product_view"], "pinned")
        self.assertEqual(calls[3].url.params["product_view"], "pinned")
        calls = []

        def respond_without_key(request):
            calls.append(request)
            return httpx.Response(200, json={"productResolution": {"definitions": []}})

        async with CLIPClient("https://local.example") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond_without_key))
            await client.resolve_graph("project", refresh_products=True)
        self.assertEqual(len(calls), 1)
        self.assertNotIn("x-api-key", calls[0].headers)

    async def test_project_workflow_uses_local_key_and_escaped_addresses(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"items": [], "revision": 2})

        async with CLIPClient("https://local.example", "operator-key") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            self.assertEqual(await client.list_projects(), [])
            await client.create_project("Product working library", kind="product-library")
            await client.create_entity("urn:project/a", "Door", "door-type")
            await client.set_project_permissions("urn:project/a", {"did:web:partner.example": "viewer"},
                expected_revision=1)
            await client.create_project_invite("urn:project/a")
            await client.redeem_project_invite("CLIP1.example")
            await client.list_project_join_requests("urn:project/a")
            await client.decide_project_join("request/id", "accept", expected_revision=2)
        self.assertEqual(len(calls), 8)
        self.assertTrue(calls[2].url.path.startswith("/ifc/v1/projects/"))
        self.assertTrue(all(call.url.path.startswith("/clip/v1/projects")
                            for index, call in enumerate(calls) if index != 2))
        self.assertTrue(all(call.headers["x-api-key"] == "operator-key" for call in calls))
        self.assertIn("urn%3Aproject%2Fa", str(calls[2].url))
        self.assertEqual(json.loads(calls[1].content), {
            "name": "Product working library", "kind": "product-library", "visibility": "private",
        })
        self.assertEqual(json.loads(calls[3].content)["expectedRevision"], 1)
        self.assertEqual(json.loads(calls[4].content)["role"], "viewer")
        self.assertIn("request%2Fid", str(calls[7].url))

    async def test_project_write_without_local_key_is_sent_without_auth_header(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"projectId": "demo-project"})

        async with CLIPClient("https://local.example") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            await client.create_project("Demo project")
            await client.get_workflow_document("record", "document")
        self.assertEqual(len(calls), 2)
        self.assertTrue(all("x-api-key" not in call.headers for call in calls))

    async def test_workflow_wrappers_preserve_versions_and_local_authorization(self):
        calls = []

        def respond(request):
            calls.append(request)
            return httpx.Response(200, json={"items": []})

        async with CLIPClient("https://local.example", "operator-key") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            await client.list_workflow_records(kind="installation", project_id="urn:project")
            await client.create_workflow_record({"kind": "product", "name": "Pump"})
            await client.get_workflow_record("record/id")
            await client.update_workflow_record("record/id", {"expectedRevision": 3, "name": "Revised pump"})
            await client.publish_workflow_record("record/id", {"expectedRevision": 4})
            await client.get_public_catalogue()
            await client.discover_catalogue("did:web:manufacturer.example")
            await client.list_workflow_projects()
            await client.connect_workflow_project("did:web:client.example", "urn:project")
            await client.list_submissions()
            await client.create_submission({"recordIds": ["record/id"]})
            await client.get_submission("issue/id")
            await client.issue_submission("issue/id", {"expectedRevision": 1})
            await client.decide_submission("issue/id", {"decision": "accept", "expectedRevision": 2})
        self.assertEqual(len(calls), 14)
        self.assertTrue(all(call.url.path.startswith("/clip/v1/supply-chain/") for call in calls))
        self.assertEqual(calls[0].url.params["kind"], "installation")
        self.assertEqual(calls[0].url.params["projectId"], "urn:project")
        self.assertTrue(all(call.headers["x-api-key"] == "operator-key" for call in calls))
        self.assertIn("record%2Fid", str(calls[3].url))
        self.assertEqual(json.loads(calls[3].content)["expectedRevision"], 3)
        self.assertEqual(json.loads(calls[6].content), {"authorityDid": "did:web:manufacturer.example"})
        self.assertIn("issue%2Fid", str(calls[12].url))
        self.assertEqual(json.loads(calls[13].content)["decision"], "accept")

    async def test_public_catalogue_does_not_require_an_operator_key(self):
        def respond(request):
            self.assertNotIn("x-api-key", request.headers)
            return httpx.Response(200, json={"items": []})

        async with CLIPClient("https://manufacturer.example") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            self.assertEqual((await client.get_public_catalogue())["items"], [])

    async def test_documents_dependency_previews_and_scoped_sender_contracts(self):
        calls = []

        def respond(request):
            calls.append(request)
            if request.url.path.endswith("/documents/doc%2Fid"):
                return httpx.Response(200, content=b"document")
            if request.method == "GET" and "/documents/" in request.url.path and not request.url.path.endswith("/grants"):
                return httpx.Response(200, content=b"document")
            return httpx.Response(200, json={"items": []})

        async with CLIPClient("https://local.example", "operator-key") as client:
            await client.http.aclose()
            client.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
            await client.list_workflow_revisions("record/id")
            await client.preview_workflow_dependencies([{"recordId": "upstream", "revision": 3}])
            await client.refresh_workflow_project("did:web:partner.example", "project/id")
            await client.get_workflow_senders("project/id")
            await client.set_workflow_senders("project/id", ["did:web:supplier.example"], expected_revision=0)
            await client.upload_workflow_document("record/id", b"evidence", "delivery.txt",
                expected_revision=2, media_type="text/plain", idempotency_key="upload-once")
            await client.list_workflow_documents("record/id")
            self.assertEqual(await client.get_workflow_document("record/id", "doc/id"), b"document")
            await client.get_workflow_document_grants("record/id", "doc/id")
            await client.grant_workflow_document("record/id", "doc/id", "did:web:client.example",
                expected_revision=4, active=False)
            await client.list_submissions(direction="incoming", project_id="project/id")
            await client.freeze_workflow_revision("record/id", expected_revision=3)
            await client.get_workflow_schema()
            await client.get_public_workflow_revision("record/id", 3)
            await client.get_workflow_adoption_candidates()
            await client.adopt_workflow_product("dataset/id", "types/pump",
                expected_sequence=8, idempotency_key="adopt-once")
            await client.upload_workflow_document("record/id", b"replacement", "updated.txt",
                expected_revision=4, replaces_document_id="old/document")
            await client.detach_workflow_document("record/id", "new/document",
                expected_revision=5, idempotency_key="retire-once")
        self.assertEqual(len(calls), 18)
        uploaded = json.loads(calls[5].content)
        self.assertEqual(base64.b64decode(uploaded["content"]), b"evidence")
        self.assertEqual(uploaded["visibility"], "private")
        self.assertEqual(uploaded["idempotencyKey"], "upload-once")
        self.assertEqual(json.loads(calls[4].content)["expectedRevision"], 0)
        self.assertEqual(json.loads(calls[9].content)["active"], False)
        self.assertIn("record%2Fid", str(calls[7].url))
        self.assertEqual(calls[10].url.params["direction"], "incoming")
        self.assertEqual(json.loads(calls[11].content)["expectedRevision"], 3)
        self.assertIn("record%2Fid/revisions/3", str(calls[13].url))
        self.assertEqual(calls[14].url.path, "/clip/v1/supply-chain/records/adoption-candidates")
        self.assertEqual(json.loads(calls[15].content)["expectedSequence"], 8)
        self.assertEqual(json.loads(calls[16].content)["replacesDocumentId"], "old/document")
        self.assertEqual(json.loads(calls[17].content)["expectedRevision"], 5)
        self.assertIn("new%2Fdocument/detach", str(calls[17].url))

    def test_graph_proposals_normalize_before_signing(self):
        key = NodeKeyManager(bytes(range(32)))
        signed = CLIPClient.sign_transaction({
            "actorDid": "did:web:manufacturer.example",
            "target": {"authorityDid": "did:web:manufacturer.example", "datasetId": "urn:products"},
            "operations": [{"action": "create", "node": {
                "path": "types/door", "attributes": {"ifc::name": "Door"},
            }}],
            "schemaDigest": "0" * 64, "expectedSequence": 0,
        }, key, "did:web:manufacturer.example#authority-key")
        proposal = IfcGraphProposalTransaction.model_validate(signed)
        self.assertEqual(proposal.operations[0].node.path, "types/door")
        self.assertTrue(verify_data_integrity_proof(signed, base64.b64decode(key.public_key_b64)))

    def test_graph_proposal_cannot_be_signed_with_empty_operations(self):
        key = NodeKeyManager(bytes(range(32)))
        with self.assertRaises(ValueError):
            CLIPClient.sign_transaction({
                "actorDid": "did:web:manufacturer.example",
                "target": {"authorityDid": "did:web:manufacturer.example", "datasetId": "urn:products"},
                "operations": [], "schemaDigest": "0" * 64, "expectedSequence": 0,
            }, key, "did:web:manufacturer.example#authority-key")
