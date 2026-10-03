"""Real API/SQL/IFCX/signature tests for recursive multi-authority workflows."""

import base64
import copy
import json
import unittest
from contextlib import contextmanager
from unittest.mock import patch

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from node.app import dependencies
from node.app.config import settings
from node.app.core import supply_chain as service
from node.app.core.crypto import NodeKeyManager
from node.app.core.did import verify_clip_message_proof
from node.app.core.ifc_product_resolver import (
    IDENTITY_SCHEMA, PRODUCT_DATA_SCHEMA, COMPONENTS_SCHEMA, PRODUCT_REFERENCES_SCHEMA,
)
from node.app.db.database import get_db
from node.app.db.migrations import upgrade
from node.app.db.orm_models import IfcAcceptedTransaction, IfcReceiptRecord, SupplyChainDocument
from node.app.main import app


class SupplyChainApiTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.nodes = {}
        self.original = {name: getattr(settings, name) for name in
            ("DID_WEB_ID", "DID_VERIFICATION_METHOD", "NODE_API_BASE", "API_KEY", "CLIP_TRUSTED_PUBLISHERS")}
        self.original_manager = dependencies._key_manager
        for index, name in enumerate(("manufacturer", "supplier", "installer", "contractor", "client", "outsider")):
            did = f"did:web:{name}.example"
            engine = create_async_engine("sqlite+aiosqlite:///:memory:")
            async with engine.begin() as connection:
                await connection.run_sync(upgrade)
            key = NodeKeyManager(bytes([index + 1]) * 32)
            self.nodes[did] = {"engine": engine, "sessions": async_sessionmaker(engine, expire_on_commit=False),
                "key": key, "name": name, "document": {"id": did,
                    "verificationMethod": [{"id": did + "#key", "type": "Multikey", "controller": did,
                        "publicKeyMultibase": key.public_key_multibase}],
                    "assertionMethod": [did + "#key"], "capabilityInvocation": [did + "#key"],
                    "authentication": [did + "#key"],
                    "service": [{"id": did + "#clip-supply-chain", "type": "ClipSupplyChainService",
                        "serviceEndpoint": f"https://{name}.example/clip/v1/supply-chain/receive"}]}}
        settings.CLIP_TRUSTED_PUBLISHERS = ",".join(self.nodes)

        async def db():
            async with self.nodes[settings.DID_WEB_ID]["sessions"]() as session:
                yield session

        async def resolver(did):
            return copy.deepcopy(self.nodes[did]["document"])

        async def endpoint(document, **kwargs):
            return document["service"][0]["serviceEndpoint"]

        async def request(method, url, *, body=None):
            did = "did:web:" + url.split("/")[2]
            with self.node(did):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="https://" + url.split("/")[2]) as client:
                    response = await client.request(method, "/clip/v1/supply-chain/receive", json=body)
                    response.raise_for_status()
                    return response.json()

        app.dependency_overrides[get_db] = db
        self.patches = [patch.object(service, "resolve_did_web_document", resolver),
            patch.object(service, "resolve_clip_service_endpoint", endpoint),
            patch.object(service, "request_json", request)]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        app.dependency_overrides.pop(get_db, None)
        for item in self.patches:
            item.stop()
        for name, value in self.original.items():
            setattr(settings, name, value)
        dependencies._key_manager = self.original_manager
        for value in self.nodes.values():
            await value["engine"].dispose()

    @contextmanager
    def node(self, name):
        did = name if name.startswith("did:") else f"did:web:{name}.example"
        before = {key: getattr(settings, key) for key in self.original}
        manager = dependencies._key_manager
        settings.DID_WEB_ID = did
        settings.DID_VERIFICATION_METHOD = did + "#key"
        settings.NODE_API_BASE = "https://" + did.removeprefix("did:web:")
        settings.API_KEY = "supply-test"
        dependencies._key_manager = self.nodes[did]["key"]
        try:
            yield
        finally:
            for key, value in before.items():
                setattr(settings, key, value)
            dependencies._key_manager = manager

    async def call(self, node, method, path, body=None, status=200, auth=True):
        with self.node(node):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE) as client:
                response = await client.request(method, "/clip/v1/supply-chain" + path, json=body,
                    headers={"x-api-key": "supply-test"} if auth else {})
        self.assertEqual(response.status_code, status, response.text)
        return response.json() if "json" in response.headers.get("content-type", "") else response.content

    async def create(self, node, kind, name, sources=None, **kwargs):
        return await self.call(node, "POST", "/records", {"kind": kind, "name": name,
            "ifcClass": kwargs.pop("ifcClass", "IfcPumpType"), "sources": sources or [], **kwargs}, 201)

    def ref(self, value, **kwargs):
        return {"authorityDid": value["authorityDid"], "recordId": value["id"], "revision": value["revision"], **kwargs}

    async def publish(self, node, value):
        return await self.call(node, "POST", f"/records/{value['id']}/publish", {"expectedRevision": value["revision"]})

    async def test_bulk_revision_read_is_authorized_local_and_matches_individual_history(self):
        product = await self.create("manufacturer", "product", "Pump")
        await self.publish("manufacturer", product)
        updated = await self.call("manufacturer", "PUT", f"/records/{product['id']}", {
            "kind": "product", "name": "Pump", "ifcClass": "IfcPumpType",
            "data": {"serviceInterval": 4000}, "sources": [], "expectedRevision": product["revision"],
        })
        await self.publish("manufacturer", updated)
        second = await self.create("manufacturer", "product", "Door", ifcClass="IfcDoorType")
        await self.call("manufacturer", "POST", f"/records/{second['id']}/revisions", {"expectedRevision": 1})
        await self.call("manufacturer", "GET", "/revisions", status=401, auth=False)
        bulk = await self.call("manufacturer", "GET", "/revisions")
        individual = []
        for record in (product, second):
            individual.extend((await self.call("manufacturer", "GET", f"/records/{record['id']}/revisions"))["items"])
        self.assertEqual(bulk["items"], sorted(individual, key=lambda value: (value["id"], value["revision"])))
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        self.assertEqual((await self.call("supplier", "GET", "/revisions"))["items"], [])

    async def project(self, node, member):
        with self.node(node):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                response = await client.post("/clip/v1/projects", json={"name": node + " project"})
                self.assertEqual(response.status_code, 201, response.text)
                value = response.json()
                response = await client.put(f"/clip/v1/projects/{value['projectId']}/permissions",
                    json={"visibility": "private", "members": {f"did:web:{member}.example": "contributor"}, "expectedRevision": 1})
                self.assertEqual(response.status_code, 200, response.text)
                result = response.json()
        await self.call(member, "POST", "/projects/connect", {
            "authorityDid": f"did:web:{node}.example", "projectId": result["projectId"]})
        return result

    async def submit(self, sender, recipient, project, records, documents=None):
        await self.call(sender, "POST", "/projects/connect", {"authorityDid": f"did:web:{recipient}.example", "projectId": project["projectId"]})
        body = {"recipientDid": f"did:web:{recipient}.example", "projectId": project["projectId"],
            "recordIds": [item["id"] for item in records], "documentIds": documents or [],
            "idempotencyKey": records[0]["id"] + "-" + str(records[0]["revision"]) + "-" + project["projectId"] + "-create"}
        draft = await self.call(sender, "POST", "/submissions", body, 201)
        issue = await self.call(sender, "POST", f"/submissions/{draft['id']}/issue",
            {"expectedRevision": draft["revision"], "idempotencyKey": draft["id"] + "-issue"})
        async def no_redelivery(*args, **kwargs):
            raise AssertionError("Delivered issue replay must not use remote egress")
        with patch.object(service, "remote", no_redelivery):
            replay = await self.call(sender, "POST", f"/submissions/{draft['id']}/issue",
                {"expectedRevision": draft["revision"], "idempotencyKey": draft["id"] + "-issue"})
        self.assertEqual(replay, issue)
        inbox = await self.call(recipient, "GET", "/submissions")
        self.assertTrue(any(item["id"] == issue["id"] for item in inbox["items"]))
        return issue

    async def accept(self, recipient, issue, scope=None):
        return await self.call(recipient, "POST", f"/submissions/{issue['id']}/decision",
            {"decision": "accept", "expectedRevision": 1, "idempotencyKey": issue["id"] + "-accept",
                **({"recordIds": scope} if scope else {})})

    async def graph(self, node, dataset_id, **params):
        with self.node(node):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                response = await client.get(f"/ifc/v1/datasets/{dataset_id}/graph", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_canonical_products_resolve_direct_and_three_supplier_nested_paths_and_updates(self):
        component = await self.create("manufacturer", "product", "Original motor",
                                      data={"rating": 10, "sku": "MOTOR"})
        component = await self.publish("manufacturer", component)
        pinned = copy.deepcopy(component)
        upstream = component
        for supplier in ("supplier", "installer", "contractor"):
            await self.call(supplier, "POST", "/catalogue/discover", {"authorityDid": upstream["authorityDid"]})
            upstream = await self.create(supplier, "offering", supplier + " motor offer", [self.ref(upstream)],
                                         data={"warranty": supplier})
            upstream = await self.publish(supplier, upstream)
        await self.call("manufacturer", "POST", "/catalogue/discover", {"authorityDid": upstream["authorityDid"]})
        assembly = await self.create("manufacturer", "product", "Pump assembly",
                                     [self.ref(upstream, quantity=2, unit="each")], data={"assembly": True})
        assembly = await self.publish("manufacturer", assembly)
        project = await self.project("client", "manufacturer")
        issue = await self.submit("manufacturer", "client", project, [component, assembly])
        accepted = await self.accept("client", issue)
        accepted_before = copy.deepcopy(accepted)
        initial = await self.graph("client", project["projectId"])
        definitions = initial["productResolution"]["definitions"]
        self.assertEqual(len(definitions), 2)
        motor = next(item for item in definitions if item["recordId"] == component["id"])
        pump = next(item for item in definitions if item["recordId"] == assembly["id"])
        self.assertEqual(pump["componentPaths"], [motor["path"]])
        self.assertEqual(pump["componentLinks"][0]["quantity"], 2)
        direct = next(item for item in accepted["acceptedRecords"] if item["name"] == "Original motor")
        self.assertEqual(initial["entities"][direct["graphPath"]]["inherits"]["manufacturerType"], motor["path"])
        self.assertEqual(initial["entities"][pump["path"]]["components"][COMPONENTS_SCHEMA], [motor["path"]])
        self.assertEqual(initial["effectiveComponents"][direct["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["rating"], 10)
        # Names/SKUs never merge identities.
        lookalike = await self.create("manufacturer", "product", "Original motor", data={"sku": "MOTOR"})
        await self.publish("manufacturer", lookalike)
        revised = await self.call("manufacturer", "PUT", f"/records/{component['id']}", {
            "kind": "product", "name": "Motor updated", "ifcClass": "IfcPumpType",
            "expectedRevision": 1, "data": {"rating": 20, "sku": "MOTOR"}})
        revised = await self.publish("manufacturer", revised)
        updated = await self.graph("client", project["projectId"], refresh_products=True)
        self.assertEqual(len(updated["productResolution"]["definitions"]), 2)
        current_motor = next(item for item in updated["productResolution"]["definitions"] if item["recordId"] == component["id"])
        self.assertEqual(current_motor["path"], motor["path"])
        self.assertEqual(current_motor["revision"], 2)
        self.assertEqual(current_motor["pinnedRevisions"], [1])
        self.assertEqual(updated["effectiveComponents"][direct["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["rating"], 20)
        self.assertEqual(updated["entities"][pump["path"]]["components"][COMPONENTS_SCHEMA], [motor["path"]])
        history_view = await self.graph("client", project["projectId"], product_view="pinned")
        self.assertEqual(history_view["effectiveComponents"][direct["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["rating"], 10)
        with self.node("client"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                for view, rating in (("current", 20), ("pinned", 10)):
                    response = await client.get(f"/ifc/v1/datasets/{project['projectId']}/components",
                        params={"entity_path": direct["graphPath"], "component_schema_id": PRODUCT_DATA_SCHEMA,
                                "product_view": view})
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["value"]["data"]["rating"], rating)
        self.assertEqual((await self.call("client", "GET", f"/submissions/{issue['id']}"))["acceptedRecords"],
                         accepted_before["acceptedRecords"])
        self.assertEqual(await self.call("manufacturer", "GET",
            f"/catalogue/{component['id']}/revisions/1", auth=False), pinned)
        revision_issue = await self.submit("manufacturer", "client", project, [revised])
        revision_asset = (await self.accept("client", revision_issue))["acceptedRecords"][0]
        mixed = await self.graph("client", project["projectId"], product_view="pinned")
        self.assertEqual(mixed["effectiveComponents"][direct["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["rating"], 10)
        self.assertEqual(mixed["effectiveComponents"][revision_asset["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["rating"], 20)
        shared = await self.graph("client", project["projectId"])
        self.assertEqual(shared["entities"][direct["graphPath"]]["inherits"]["manufacturerType"],
                         shared["entities"][revision_asset["graphPath"]]["inherits"]["manufacturerType"])
        self.assertEqual(len(shared["productResolution"]["definitions"]), 2)
        # Product identity is independent of the client's project/dataset address.
        other_project = await self.project("client", "manufacturer")
        second_issue = await self.submit("manufacturer", "client", other_project, [revised])
        await self.accept("client", second_issue)
        second_graph = await self.graph("client", other_project["projectId"])
        self.assertEqual(second_graph["productResolution"]["definitions"][0]["path"], motor["path"])

    async def test_canonical_current_refresh_fails_explicitly_for_unavailable_or_untrusted_publisher(self):
        product = await self.create("manufacturer", "product", "Pump")
        await self.publish("manufacturer", product)
        project = await self.project("client", "manufacturer")
        issue = await self.submit("manufacturer", "client", project, [product])
        await self.accept("client", issue)
        with patch.object(service, "remote", side_effect=service.HTTPException(424, "Publisher offline")):
            with self.node("client"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                    headers={"x-api-key": "supply-test"}) as client:
                    result = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph",
                                              params={"refresh_products": True})
            self.assertEqual(result.status_code, 424)
            self.assertIn("Publisher offline", result.text)
        with self.node("client"), patch.object(settings, "CLIP_TRUSTED_PUBLISHERS", ""):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                result = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph")
            self.assertEqual(result.status_code, 403)

    async def test_canonical_refresh_requires_operator_and_rejects_equivocation_and_class_changes(self):
        product = await self.create("manufacturer", "product", "Pump")
        product = await self.publish("manufacturer", product)
        project = await self.project("client", "manufacturer")
        issue = await self.submit("manufacturer", "client", project, [product])
        await self.accept("client", issue)
        with self.node("client"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE) as client:
                response = await client.put(f"/clip/v1/projects/{project['projectId']}/permissions",
                    headers={"x-api-key": "supply-test"},
                    json={"visibility": "public", "members": {"did:web:manufacturer.example": "contributor"},
                          "expectedRevision": 2})
                self.assertEqual(response.status_code, 200, response.text)
                response = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph",
                                             params={"refresh_products": True})
                self.assertEqual(response.status_code, 403, response.text)
                response = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph",
                    headers={"x-api-key": "supply-test"}, params={"refresh_products": True, "product_view": "pinned"})
                self.assertEqual(response.status_code, 422, response.text)

        equivocation = copy.deepcopy(product)
        equivocation.pop("proof")
        equivocation["name"] = "Conflicting signed product"
        with self.node("manufacturer"):
            equivocation = service.sign(equivocation)

        async def conflicting_catalogue(*args, **kwargs):
            return {"authorityDid": product["authorityDid"], "items": [equivocation]}

        with patch.object(service, "remote", conflicting_catalogue):
            with self.node("client"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                    headers={"x-api-key": "supply-test"}) as client:
                    response = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph",
                                                 params={"refresh_products": True})
            self.assertEqual(response.status_code, 409, response.text)

        revised = await self.call("manufacturer", "PUT", f"/records/{product['id']}", {
            "kind": "product", "name": "Wrong class", "ifcClass": "IfcValveType",
            "expectedRevision": 1, "data": {}})
        await self.publish("manufacturer", revised)
        with self.node("client"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                response = await client.get(f"/ifc/v1/datasets/{project['projectId']}/graph",
                                             params={"refresh_products": True})
        self.assertEqual(response.status_code, 409, response.text)
        pinned = await self.graph("client", project["projectId"], product_view="pinned")
        self.assertEqual(pinned["productResolution"]["definitions"][0]["ifcClass"], "IfcPumpType")

    async def test_complete_recursive_workflow_documents_allocations_and_graph_history(self):
        component = await self.create("manufacturer", "product", "Motor")
        component = await self.publish("manufacturer", component)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": component["authorityDid"]})
        offering = await self.create("supplier", "offering", "Motor offering", [self.ref(component)])
        offering = await self.publish("supplier", offering)
        await self.call("manufacturer", "POST", "/catalogue/discover", {"authorityDid": offering["authorityDid"]})
        product = await self.create("manufacturer", "product", "Pump", [self.ref(offering, quantity=1, unit="each")])
        public_doc = await self.call("manufacturer", "POST", f"/records/{product['id']}/documents", {
            "name": "datasheet.pdf", "mediaType": "application/pdf", "content": base64.b64encode(b"public datasheet").decode(),
            "visibility": "public", "expectedRevision": 1}, 201)
        private_doc = await self.call("manufacturer", "POST", f"/records/{product['id']}/documents", {
            "name": "private.pdf", "mediaType": "application/pdf", "content": base64.b64encode(b"private certificate").decode(),
            "visibility": "private", "expectedRevision": 2}, 201)
        product["revision"] = private_doc["recordRevision"]
        product = await self.publish("manufacturer", product)
        self.assertEqual([item["id"] for item in product["documents"]], [public_doc["id"]])
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        pump_offer = await self.create("supplier", "offering", "Pump offering", [self.ref(product)])
        pump_offer = await self.publish("supplier", pump_offer)
        project = await self.project("installer", "supplier")
        supply = await self.create("supplier", "supply", "Delivery", [self.ref(pump_offer)],
            projectId=project["projectId"], data={"quantity": 4, "unit": "each", "serials": ["a", "b", "c", "d"]})
        document = await self.call("supplier", "POST", f"/records/{supply['id']}/documents", {
            "name": "delivery.pdf", "mediaType": "application/pdf", "content": base64.b64encode(b"private delivery").decode(),
            "visibility": "private", "expectedRevision": 1}, 201)
        issue = await self.submit("supplier", "installer", project, [supply], [document["id"]])
        self.assertEqual(await self.call("installer", "GET",
            f"/submissions/{issue['id']}/documents/{document['id']}"), b"private delivery")
        accepted = await self.accept("installer", issue)
        owned_supply = accepted["acceptedRecords"][0]
        self.assertEqual(owned_supply["authorityDid"], "did:web:installer.example")
        self.assertEqual(await self.call("installer", "GET", f"/records/{owned_supply['id']}/documents/{document['id']}"), b"private delivery")
        installation = await self.create("installer", "installation", "Installed pump", [
            self.ref(owned_supply, quantity=2, unit="each", serials=["a", "b"])],
            projectId=project["projectId"], ifcClass="IfcPump", data={"location": "Plant room", "installedBy": "installer"})
        await self.create("installer", "installation", "Second pump", [
            self.ref(owned_supply, quantity=2, unit="each", serials=["c", "d"])],
            projectId=project["projectId"], ifcClass="IfcPump")
        await self.call("installer", "POST", "/records", {"kind": "installation", "name": "Over allocation",
            "ifcClass": "IfcPump", "sources": [self.ref(owned_supply, quantity=1, unit="each", serials=["a"])],
            "projectId": project["projectId"]}, 409)
        contractor_project = await self.project("contractor", "installer")
        installer_issue = await self.submit("installer", "contractor", contractor_project, [installation])
        main_asset = (await self.accept("contractor", installer_issue))["acceptedRecords"][0]
        client_project = await self.project("client", "contractor")
        client_issue = await self.submit("contractor", "client", client_project, [main_asset])
        final_asset = (await self.accept("client", client_issue))["acceptedRecords"][0]
        self.assertEqual(final_asset["acceptedFrom"]["snapshot"]["acceptedFrom"]["snapshot"]["authorityDid"], "did:web:installer.example")
        self.assertTrue(any(item["authorityDid"] == "did:web:manufacturer.example" for item in final_asset["lineage"]))
        for node in ("manufacturer", "supplier", "installer", "contractor", "client"):
            did = f"did:web:{node}.example"
            async with self.nodes[did]["sessions"]() as session:
                history = (await session.execute(select(IfcAcceptedTransaction))).scalars().all()
                self.assertGreater(len(history), 0)
                self.assertTrue(all(item.transaction_json["kind"] == "graphProposalAcceptance" for item in history))
                did_document = self.nodes[did]["document"]
                for transaction in history:
                    self.assertTrue(verify_clip_message_proof(transaction.transaction_json["proposal"], did_document))
                    self.assertTrue(verify_clip_message_proof(transaction.transaction_json["decision"], did_document))
                    receipt = (await session.execute(select(IfcReceiptRecord).where(
                        IfcReceiptRecord.transaction_id == transaction.transaction_id))).scalar_one()
                    self.assertTrue(verify_clip_message_proof(receipt.receipt_json, did_document))
        async with self.nodes["did:web:supplier.example"]["sessions"]() as session:
            stored = await session.get(SupplyChainDocument, document["id"])
            self.assertNotIn(b"private delivery", stored.ciphertext)

    async def test_private_auth_concurrency_immutable_revisions_and_dependency_errors(self):
        schema = await self.call("manufacturer", "GET", "/schema", auth=False)
        self.assertEqual({item["kind"] for item in schema["kinds"]}, service.KINDS)
        await self.call("manufacturer", "GET", "/records", status=401, auth=False)
        value = await self.create("manufacturer", "product", "Original")
        first = await self.publish("manufacturer", value)
        await self.call("manufacturer", "PUT", f"/records/{value['id']}", {"kind": "product", "name": "Changed",
            "ifcClass": "IfcPumpType", "expectedRevision": 1}, 200)
        await self.call("manufacturer", "PUT", f"/records/{value['id']}", {"kind": "product", "name": "Stale",
            "ifcClass": "IfcPumpType", "expectedRevision": 1}, 409)
        catalogue = await self.call("manufacturer", "GET", "/catalogue", auth=False)
        self.assertEqual(catalogue["items"][0], first)
        self.assertEqual(catalogue["items"][0]["name"], "Original")
        frozen = await self.call("manufacturer", "POST", f"/records/{value['id']}/revisions", {"expectedRevision": 2})
        self.assertEqual(frozen["revision"], 2)
        self.assertEqual(frozen["status"], "issued")
        self.assertEqual(frozen, await self.call("manufacturer", "POST", f"/records/{value['id']}/revisions", {"expectedRevision": 2}))
        await self.call("supplier", "POST", "/records", {"kind": "offering", "name": "Missing",
            "sources": [{"authorityDid": "did:web:manufacturer.example", "recordId": "missing", "revision": 1}]}, 424)
        await self.call("outsider", "POST", "/projects/connect", {"authorityDid": "did:web:installer.example", "projectId": "missing"}, 424)

    async def test_tampered_proof_and_wrong_audience_are_rejected(self):
        with self.node("supplier"):
            message = service.service_message("did:web:installer.example", "catalogue", {})
        message["payload"]["tampered"] = True
        await self.call("installer", "POST", "/receive", message, 401, auth=False)
        with self.node("supplier"):
            wrong = service.service_message("did:web:client.example", "catalogue", {})
        await self.call("installer", "POST", "/receive", wrong, 401, auth=False)

    async def test_scoped_acceptance_decision_replay_and_corrections(self):
        product = await self.create("manufacturer", "product", "Pump")
        product = await self.publish("manufacturer", product)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        offering = await self.create("supplier", "offering", "Offering", [self.ref(product)])
        offering = await self.publish("supplier", offering)
        project = await self.project("installer", "supplier")
        records = [await self.create("supplier", "supply", str(index), [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 1, "unit": "each"}) for index in range(2)]
        issue = await self.submit("supplier", "installer", project, records)
        decision = await self.accept("installer", issue, [records[0]["id"]])
        self.assertEqual(len(decision["acceptedRecords"]), 1)
        onward_project = await self.project("contractor", "installer")
        onward = await self.submit("installer", "contractor", onward_project, decision["acceptedRecords"])
        self.assertNotIn('"name": "1"', json.dumps(onward["issue"]["records"]))
        self.assertNotIn("records", decision["acceptedRecords"][0]["acceptedFrom"]["issue"])
        body = {"decision": "accept", "expectedRevision": 1, "idempotencyKey": issue["id"] + "-accept",
            "recordIds": [records[0]["id"]]}
        replay = await self.call("installer", "POST", f"/submissions/{issue['id']}/decision", body)
        self.assertEqual(replay["acceptedRecords"], decision["acceptedRecords"])
        body["decision"] = "reject"
        await self.call("installer", "POST", f"/submissions/{issue['id']}/decision", body, 409)
        correction = await self.call("supplier", "POST", "/submissions", {
            "recipientDid": "did:web:installer.example", "projectId": project["projectId"],
            "recordIds": [records[1]["id"]], "supersedes": issue["id"], "idempotencyKey": "correction"}, 201)
        revised = await self.call("supplier", "POST", f"/submissions/{correction['id']}/issue",
            {"expectedRevision": 1, "idempotencyKey": "correction-issue"})
        self.assertEqual(revised["issue"]["supersedes"], issue["id"])
        original = await self.call("supplier", "GET", f"/submissions/{issue['id']}")
        self.assertEqual(original["issue"], issue["issue"])

    async def test_client_downloads_exact_cached_public_document_without_source_access(self):
        product = await self.create("manufacturer", "product", "Door", ifcClass="IfcDoorType")
        original = await self.call("manufacturer", "POST", f"/records/{product['id']}/documents", {
            "name": "door.txt", "mediaType": "text/plain", "content": base64.b64encode(b"original").decode(),
            "visibility": "public", "expectedRevision": 1}, 201)
        product["revision"] = 2
        await self.publish("manufacturer", product)
        replacement = await self.call("manufacturer", "POST", f"/records/{product['id']}/documents", {
            "name": "door.txt", "mediaType": "text/plain", "content": base64.b64encode(b"updated").decode(),
            "visibility": "public", "expectedRevision": 2, "replacesDocumentId": original["id"]}, 201)
        product["revision"] = 3
        await self.publish("manufacturer", product)
        await self.call("client", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        query = f"?authorityDid={product['authorityDid']}&recordId={product['id']}&revision="
        route = f"/documents/{replacement['id']}"
        with patch.object(service, "remote", side_effect=AssertionError("Cached download must not contact source")):
            self.assertEqual(await self.call("client", "GET", route + query + "3"), b"updated")
            self.assertEqual(await self.call("client", "GET", f"/documents/{original['id']}" + query + "2"), b"original")
            await self.call("client", "GET", route + query + "2", status=404)
            await self.call("client", "GET", route + query + "4", status=404)
            await self.call("client", "GET", route + query + "3", status=401, auth=False)
            await self.call("client", "GET", route + f"?authorityDid={product['authorityDid']}&revision=3", status=422)
        private = await self.call("manufacturer", "POST", f"/records/{product['id']}/documents", {
            "name": "private.txt", "mediaType": "text/plain", "content": base64.b64encode(b"private").decode(),
            "visibility": "private", "expectedRevision": 3}, 201)
        product["revision"] = 4
        await self.publish("manufacturer", product)
        await self.call("client", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        await self.call("client", "GET", f"/documents/{private['id']}" + query + "4", status=404)

    async def test_deliberate_public_documents_and_source_authorised_private_grants(self):
        value = await self.create("manufacturer", "product", "Documented pump")
        with patch.object(settings, "MAX_DOCUMENT_BYTES", 3):
            await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", {
                "name": "oversize.txt", "mediaType": "text/plain", "content": base64.b64encode(b"four").decode(),
                "visibility": "private", "expectedRevision": 1}, 413)
        await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", {
            "name": "invalid.txt", "mediaType": "text/plain", "content": "not base64%",
            "visibility": "private", "expectedRevision": 1}, 422)
        public = await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", {
            "name": "public.txt", "mediaType": "text/plain", "content": base64.b64encode(b"public").decode(),
            "visibility": "public", "expectedRevision": 1}, 201)
        private = await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", {
            "name": "private.txt", "mediaType": "text/plain", "content": base64.b64encode(b"restricted").decode(),
            "visibility": "private", "expectedRevision": 2}, 201)
        value["revision"] = 3
        published = await self.publish("manufacturer", value)
        self.assertEqual(await self.call("manufacturer", "GET",
            f"/catalogue/{value['id']}/revisions/3/documents/{public['id']}", auth=False), b"public")
        await self.call("manufacturer", "GET", f"/catalogue/{value['id']}/revisions/3/documents/{private['id']}",
            status=404, auth=False)
        fetched = await self.call("manufacturer", "GET", f"/catalogue/{value['id']}/revisions/3", auth=False)
        self.assertEqual(fetched, published)
        source_route = f"/documents/{private['id']}?authorityDid=did:web:manufacturer.example"
        await self.call("client", "GET", source_route, status=403)
        grant_path = f"/records/{value['id']}/documents/{private['id']}/grants"
        grant = await self.call("manufacturer", "POST", grant_path, {
            "recipientDid": "did:web:client.example", "expectedRevision": 0, "active": True})
        self.assertTrue(grant["active"])
        self.assertEqual(await self.call("client", "GET", source_route), b"restricted")
        await self.call("outsider", "GET", source_route, status=403)
        await self.call("manufacturer", "POST", grant_path, {
            "recipientDid": "did:web:client.example", "expectedRevision": 0, "active": False}, 409)
        await self.call("manufacturer", "POST", grant_path, {
            "recipientDid": "did:web:client.example", "expectedRevision": 1, "active": False})
        await self.call("client", "GET", source_route, status=403)

    async def test_document_replacement_and_retirement_preserve_signed_history(self):
        product = await self.create("manufacturer", "product", "Document versions")
        path = f"/records/{product['id']}/documents"
        original = await self.call("manufacturer", "POST", path, {
            "name": "manual-v1.txt", "mediaType": "text/plain", "content": base64.b64encode(b"version one").decode(),
            "visibility": "public", "expectedRevision": 1}, 201)
        product["revision"] = 2
        first = await self.publish("manufacturer", product)
        replacement_body = {"name": "manual-v2.txt", "mediaType": "text/plain",
            "content": base64.b64encode(b"version two").decode(), "visibility": "public",
            "expectedRevision": 2, "replacesDocumentId": original["id"], "idempotencyKey": "replace-manual"}
        replacement = await self.call("manufacturer", "POST", path, replacement_body, 201)
        self.assertEqual(replacement["supersedesDocumentId"], original["id"])
        self.assertEqual(replacement, await self.call("manufacturer", "POST", path, replacement_body, 201))
        current = await self.call("manufacturer", "GET", path)
        self.assertEqual([item["id"] for item in current["items"]], [replacement["id"]])
        self.assertEqual(await self.call("manufacturer", "GET",
            f"/catalogue/{product['id']}/revisions/2/documents/{original['id']}", auth=False), b"version one")
        revisions = await self.call("manufacturer", "GET", f"/records/{product['id']}/revisions")
        self.assertEqual(revisions["items"][0], first)
        retire_path = path + f"/{replacement['id']}/detach"
        await self.call("manufacturer", "POST", retire_path, {"expectedRevision": 2}, 409)
        retired = await self.call("manufacturer", "POST", retire_path, {
            "expectedRevision": 3, "idempotencyKey": "retire-manual"})
        self.assertEqual(retired["recordRevision"], 4)
        self.assertEqual((await self.call("manufacturer", "GET", path))["items"], [])
        self.assertEqual(retired, await self.call("manufacturer", "POST", retire_path, {
            "expectedRevision": 3, "idempotencyKey": "retire-manual"}))
        async with self.nodes["did:web:manufacturer.example"]["sessions"]() as session:
            self.assertIsNotNone(await session.get(SupplyChainDocument, original["id"]))
            self.assertIsNotNone(await session.get(SupplyChainDocument, replacement["id"]))

    async def test_dependency_cycles_class_mismatch_digest_pins_and_private_catalogue_exclusion(self):
        first = await self.create("manufacturer", "product", "Pump")
        first = await self.publish("manufacturer", first)
        self.assertTrue(first["proof"]["created"].endswith("Z"))
        with patch.object(settings, "CLIP_TRUSTED_PUBLISHERS", ""):
            await self.call("supplier", "POST", "/catalogue/discover",
                {"authorityDid": first["authorityDid"]}, 403)
        second = await self.create("manufacturer", "product", "Assembly", [self.ref(first, quantity=1, unit="each")])
        second = await self.publish("manufacturer", second)
        await self.call("manufacturer", "PUT", f"/records/{first['id']}", {
            "kind": "product", "name": "Cycle", "ifcClass": "IfcPumpType", "expectedRevision": 1,
            "sources": [self.ref(second, quantity=1, unit="each")]}, 422)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": first["authorityDid"]})
        await self.call("supplier", "POST", "/records", {"kind": "offering", "name": "Mismatch",
            "ifcClass": "IfcDoorType", "sources": [self.ref(first)]}, 422)
        await self.call("supplier", "POST", "/records", {"kind": "offering", "name": "Wrong digest",
            "ifcClass": "IfcPumpType", "sources": [self.ref(first, digest="0" * 64)]}, 424)
        offering = await self.create("supplier", "offering", "Private details", [self.ref(first)],
            data={"price": 999, "client": "secret customer"})
        await self.call("supplier", "POST", f"/records/{offering['id']}/publish", {"expectedRevision": 1}, 422)
        catalogue = await self.call("supplier", "GET", "/catalogue", auth=False)
        self.assertEqual(catalogue["items"], [])

    async def test_record_and_document_idempotency_and_issue_delivery_retry(self):
        body = {"kind": "product", "name": "Pump", "ifcClass": "IfcPumpType", "idempotencyKey": "owned-product"}
        value = await self.call("manufacturer", "POST", "/records", body, 201)
        repeated = await self.call("manufacturer", "POST", "/records", body, 201)
        self.assertEqual(value, repeated)
        await self.call("manufacturer", "POST", "/records", {**body, "name": "Different"}, 409)
        doc_body = {"name": "note.txt", "mediaType": "text/plain", "content": base64.b64encode(b"private").decode(),
            "visibility": "private", "expectedRevision": 1, "idempotencyKey": "upload-one"}
        doc = await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", doc_body, 201)
        self.assertEqual(doc, await self.call("manufacturer", "POST", f"/records/{value['id']}/documents", doc_body, 201))
        value["revision"] = 2
        published = await self.publish("manufacturer", value)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": published["authorityDid"]})
        offering = await self.create("supplier", "offering", "Offering", [self.ref(published)])
        offering = await self.publish("supplier", offering)
        project = await self.project("installer", "supplier")
        supply = await self.create("supplier", "supply", "Delivery", [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 1, "unit": "each"})
        await self.call("supplier", "POST", "/projects/connect", {"authorityDid": "did:web:installer.example", "projectId": project["projectId"]})
        draft = await self.call("supplier", "POST", "/submissions", {"recipientDid": "did:web:installer.example",
            "projectId": project["projectId"], "recordIds": [supply["id"]], "idempotencyKey": "create-retry"}, 201)
        issue_body = {"expectedRevision": 1, "idempotencyKey": "issue-retry"}
        async def unavailable(*args, **kwargs):
            from fastapi import HTTPException
            raise HTTPException(424, "Offline recipient")

        with patch.object(service, "remote", unavailable):
            await self.call("supplier", "POST", f"/submissions/{draft['id']}/issue", issue_body, 424)
        pending = await self.call("supplier", "GET", f"/submissions/{draft['id']}")
        self.assertEqual(pending["deliveryStatus"], "pending")
        await self.call("supplier", "POST", f"/submissions/{draft['id']}/issue",
            {**issue_body, "expectedRevision": 2}, 409)
        delivered = await self.call("supplier", "POST", f"/submissions/{draft['id']}/issue", issue_body)
        self.assertEqual(delivered["issue"], pending["issue"])
        self.assertEqual(delivered["deliveryStatus"], "delivered")
        persisted = await self.call("supplier", "GET", f"/submissions/{draft['id']}")
        self.assertEqual(persisted["deliveryStatus"], "delivered")
        with patch.object(service, "remote", unavailable):
            self.assertEqual(await self.call("supplier", "POST", f"/submissions/{draft['id']}/issue", issue_body), delivered)
        decision_body = {"decision": "accept", "expectedRevision": 1, "idempotencyKey": "decision-retry"}
        with patch.object(service, "remote", unavailable):
            await self.call("installer", "POST", f"/submissions/{draft['id']}/decision", decision_body, 424)
        pending_decision = await self.call("installer", "GET", f"/submissions/{draft['id']}")
        self.assertEqual(pending_decision["decisionDeliveryStatus"], "pending")
        completed = await self.call("installer", "POST", f"/submissions/{draft['id']}/decision", decision_body)
        self.assertEqual(completed["acceptedRecords"], pending_decision["acceptedRecords"])
        self.assertEqual(completed["decision"], pending_decision["decision"])
        self.assertEqual(completed["decisionDeliveryStatus"], "delivered")

    async def test_upstream_signature_tampering_is_not_laundered_by_sender(self):
        product = await self.create("manufacturer", "product", "Pump")
        product = await self.publish("manufacturer", product)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        offering = await self.create("supplier", "offering", "Offering", [self.ref(product)])
        offering = await self.publish("supplier", offering)
        project = await self.project("installer", "supplier")
        supply = await self.create("supplier", "supply", "Delivery", [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 1, "unit": "each"})
        issue = await self.submit("supplier", "installer", project, [supply])
        tampered = copy.deepcopy(issue["issue"])
        tampered["submissionId"] = "new-malicious-issue"
        tampered_snapshot = tampered["records"][0]
        tampered_snapshot["dependencies"][0]["dependencies"][0]["name"] = "Altered manufacturer specification"
        with self.node("supplier"):
            altered_offering = tampered_snapshot["dependencies"][0]
            altered_offering["sources"][0]["digest"] = service.digest_json(altered_offering["dependencies"][0])
            altered_offering = service.sign({key: value for key, value in altered_offering.items() if key != "proof"})
            tampered_snapshot["dependencies"][0] = altered_offering
            tampered_snapshot["sources"][0]["digest"] = service.digest_json(altered_offering)
            tampered["records"][0] = service.sign({key: value for key, value in tampered_snapshot.items() if key != "proof"})
            tampered["manifest"]["submissionId"] = tampered["submissionId"]
            tampered["manifest"]["recordDigests"] = {item["id"]: service.digest_json(item) for item in tampered["records"]}
            tampered["manifest"] = service.sign({key: value for key, value in tampered["manifest"].items() if key != "proof"})
            tampered = service.sign({key: value for key, value in tampered.items() if key != "proof"})
            message = service.service_message("did:web:installer.example", "issue", {"issue": tampered})
        await self.call("installer", "POST", "/receive", message, 424, auth=False)

    async def test_scoped_sender_permission_does_not_grant_project_contributor_access(self):
        product = await self.create("manufacturer", "product", "Pump")
        product = await self.publish("manufacturer", product)
        await self.call("supplier", "POST", "/catalogue/discover", {"authorityDid": product["authorityDid"]})
        offering = await self.create("supplier", "offering", "Offering", [self.ref(product)])
        offering = await self.publish("supplier", offering)
        project = await self.project("installer", "outsider")
        sender_policy = await self.call("installer", "PUT", f"/projects/{project['projectId']}/senders",
            {"expectedRevision": 0, "senders": ["did:web:supplier.example"]})
        self.assertEqual(sender_policy["revision"], 1)
        await self.call("supplier", "POST", "/projects/connect",
            {"authorityDid": "did:web:installer.example", "projectId": project["projectId"]})
        supply = await self.create("supplier", "supply", "Scoped delivery", [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 1, "unit": "each"})
        issued = await self.submit("supplier", "installer", project, [supply])
        self.assertEqual((await self.accept("installer", issued))["status"], "accepted")
        from node.app.db.orm_models import ClipProjectPolicy, IfcDatasetTrustPolicy
        async with self.nodes["did:web:installer.example"]["sessions"]() as session:
            policy = await session.get(ClipProjectPolicy, ("did:web:installer.example", project["projectId"]))
            trust = await session.get(IfcDatasetTrustPolicy, ("did:web:installer.example", project["projectId"]))
            self.assertNotIn("did:web:supplier.example", policy.members)
            self.assertNotIn("did:web:supplier.example", trust.trusted_proposers)
        await self.call("installer", "PUT", f"/projects/{project['projectId']}/senders",
            {"expectedRevision": 1, "senders": []})
        await self.call("supplier", "POST", "/projects/connect", {
            "authorityDid": "did:web:installer.example", "projectId": project["projectId"]}, 403)

    async def test_invitation_pending_persistence_and_authenticated_status_refresh(self):
        from node.app.api import clip_projects
        from node.app.federation import clip_replication
        project = await self.project("installer", "outsider")
        with self.node("installer"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                invite = await client.post(f"/clip/v1/projects/{project['projectId']}/invites", json={"role": "viewer"})
                self.assertEqual(invite.status_code, 201, invite.text)
                code = invite.json()["code"]

        async def resolver(did):
            return self.nodes[did]["document"]

        async def endpoint(document, **kwargs):
            return "https://" + document["id"].removeprefix("did:web:") + "/clip/v1/projects/invites/receive"

        async def request(method, url, *, body=None):
            with self.node("did:web:" + url.split("/")[2]):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE) as client:
                    response = await client.request(method, "/clip/v1/projects/invites/receive", json=body)
                    response.raise_for_status()
                    return response.json()

        with patch.object(clip_projects, "resolve_did_web_document", resolver), patch.object(
            clip_replication, "resolve_did_web_document", resolver), patch.object(
            clip_projects, "resolve_clip_service_endpoint", endpoint), patch.object(clip_projects, "request_json", request):
            with self.node("supplier"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                    headers={"x-api-key": "supply-test"}) as client:
                    response = await client.post("/clip/v1/projects/invites/redeem", json={"code": code})
                    self.assertEqual(response.status_code, 200, response.text)
                    join_id = response.json()["payload"]["joinRequestId"]
            pending = await self.call("supplier", "GET", "/projects")
            joined = next(item for item in pending["items"] if item["projectId"] == project["projectId"])
            self.assertEqual(joined["status"], "pending")
            refreshed = await self.call("supplier", "POST", "/projects/refresh",
                {"authorityDid": "did:web:installer.example", "projectId": project["projectId"]})
            self.assertEqual(refreshed["status"], "pending")
            with self.node("installer"):
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                    headers={"x-api-key": "supply-test"}) as client:
                    response = await client.post(f"/clip/v1/projects/join-requests/{join_id}/decision",
                        json={"decision": "accept", "expectedRevision": project["revision"]})
                    self.assertEqual(response.status_code, 200, response.text)
            refreshed = await self.call("supplier", "POST", "/projects/refresh",
                {"authorityDid": "did:web:installer.example", "projectId": project["projectId"]})
            self.assertEqual(refreshed["status"], "accepted")
            self.assertIsNotNone(refreshed["decision"]["proof"])

    async def test_same_authority_issue_accept_and_replay_preserves_local_recipient(self):
        product = await self.create("manufacturer", "product", "Local pump")
        product = await self.publish("manufacturer", product)
        offering = await self.create("manufacturer", "offering", "Local offering", [self.ref(product)])
        offering = await self.publish("manufacturer", offering)
        project = await self.project("manufacturer", "outsider")
        supply = await self.create("manufacturer", "supply", "Local delivery", [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 2, "unit": "each", "serials": ["a", "b"]})
        draft = await self.call("manufacturer", "POST", "/submissions", {
            "recipientDid": product["authorityDid"], "projectId": project["projectId"],
            "recordIds": [supply["id"]], "idempotencyKey": "self-draft"}, 201)
        issue_body = {"expectedRevision": 1, "idempotencyKey": "self-issue"}
        issued = await self.call("manufacturer", "POST", f"/submissions/{draft['id']}/issue", issue_body)
        self.assertTrue(issued["localRecipient"])
        self.assertTrue((await self.call("manufacturer", "GET", f"/submissions/{draft['id']}"))["localRecipient"])
        incoming = await self.call("manufacturer", "GET", "/submissions?direction=incoming")
        self.assertTrue(any(item["id"] == draft["id"] for item in incoming["items"]))
        self.assertEqual(issued, await self.call("manufacturer", "POST", f"/submissions/{draft['id']}/issue", issue_body))
        decision_body = {"expectedRevision": 2, "idempotencyKey": "self-accept", "decision": "accept"}
        accepted = await self.call("manufacturer", "POST", f"/submissions/{draft['id']}/decision", decision_body)
        self.assertEqual(accepted["status"], "accepted")
        self.assertEqual(len(accepted["acceptedRecords"]), 1)
        replay = await self.call("manufacturer", "POST", f"/submissions/{draft['id']}/decision", decision_body)
        self.assertEqual(replay["acceptedRecords"], accepted["acceptedRecords"])
        self.assertTrue(replay["localRecipient"])

    async def test_record_project_destinations_must_be_explicit_and_private(self):
        await self.call("manufacturer", "POST", "/records", {
            "kind": "product", "name": "Typo destination", "ifcClass": "IfcPumpType", "projectId": "typo"}, 404)
        with self.node("manufacturer"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                created = await client.post("/clip/v1/projects", json={"name": "Public project", "visibility": "public"})
                self.assertEqual(created.status_code, 201)
                public_id = created.json()["projectId"]
        await self.call("manufacturer", "POST", "/records", {
            "kind": "product", "name": "Public project private material", "ifcClass": "IfcPumpType",
            "projectId": public_id}, 409)
        await self.call("manufacturer", "POST", "/records", {
            "kind": "installation", "name": "No local project", "ifcClass": "IfcPump",
            "projectId": "unknown", "sources": []}, 422)

    async def test_fractional_allocations_use_exact_decimal_threshold_without_epsilon(self):
        product = await self.create("manufacturer", "product", "Fractional pump")
        product = await self.publish("manufacturer", product)
        offering = await self.create("manufacturer", "offering", "Measured offering", [self.ref(product)])
        offering = await self.publish("manufacturer", offering)
        project = await self.project("installer", "manufacturer")
        supply = await self.create("manufacturer", "supply", "Measured delivery", [self.ref(offering)],
            projectId=project["projectId"], data={"quantity": 0.3, "unit": "metre"})
        issue = await self.submit("manufacturer", "installer", project, [supply])
        accepted = (await self.accept("installer", issue))["acceptedRecords"][0]
        for quantity in (0.1, 0.2):
            await self.create("installer", "installation", f"Allocation {quantity}", [
                self.ref(accepted, quantity=quantity, unit="metre")], projectId=project["projectId"], ifcClass="IfcPump")
        await self.call("installer", "POST", "/records", {
            "kind": "installation", "name": "Tiny excess", "ifcClass": "IfcPump",
            "projectId": project["projectId"], "sources": [self.ref(accepted, quantity=0.00000000000000001, unit="metre")]}, 409)

    async def test_adopt_owned_native_product_without_duplicate_entity_or_authority_transfer(self):
        with self.node("manufacturer"):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=settings.NODE_API_BASE,
                headers={"x-api-key": "supply-test"}) as client:
                project = (await client.post("/clip/v1/projects", json={"name": "Native private library",
                    "kind": "product-library"})).json()
                response = await client.post(f"/ifc/v1/projects/{project['projectId']}/entities",
                    json={"name": "Native pump", "template": "pump-type"})
                self.assertEqual(response.status_code, 201, response.text)
                entity_path = response.json()["entityPath"]
        async with self.nodes["did:web:manufacturer.example"]["sessions"]() as session:
            state = await session.get(service.IfcAuthoritySequence, "did:web:manufacturer.example")
            sequence = state.sequence
            dataset = await session.get(service.IfcDatasetRecord, ("did:web:manufacturer.example", project["projectId"]))
            fixture = copy.deepcopy(dataset.file_json)
            native_node = next(node for node in fixture["data"] if node["path"] == entity_path)
            original_id = native_node["attributes"][service.SOURCE_SCHEMA]["id"]
            native_node["attributes"][service.SOURCE_SCHEMA]["format"] = "IFC"
            native_node["attributes"][service.SOURCE_SCHEMA]["properties"]["ratedPower"] = 42
            fixture["schemas"]["urn:test:custom"] = {"value": {"dataType": "String"}}
            native_node["attributes"]["urn:test:custom"] = "untouched"
            native_node["children"] = {"library": "library"}
            native_node["inherits"] = {"library": "library"}
            dataset.file_json = fixture
            await session.commit()
        body = {"datasetId": project["projectId"], "entityPath": entity_path,
            "kind": "product", "expectedSequence": sequence, "idempotencyKey": "adopt-native"}
        candidates = await self.call("manufacturer", "GET", "/records/adoption-candidates")
        self.assertEqual(candidates, await self.call("manufacturer", "GET", "/records/adopt/candidates"))
        self.assertEqual(candidates["sequence"], sequence)
        self.assertTrue(any(item["entityPath"] == entity_path for item in candidates["items"]))
        adopted = await self.call("manufacturer", "POST", "/records/adopt", body, 201)
        self.assertFalse(any(item["entityPath"] == entity_path for item in (
            await self.call("manufacturer", "GET", "/records/adoption-candidates"))["items"]))
        self.assertEqual(adopted["graphPath"], entity_path)
        self.assertEqual(adopted["datasetId"], project["projectId"])
        self.assertEqual(adopted, await self.call("manufacturer", "POST", "/records/adopt", body, 201))
        await self.call("manufacturer", "POST", "/records/adopt",
            {**body, "expectedSequence": sequence + 1, "idempotencyKey": "duplicate-native"}, 409)
        changed = await self.call("manufacturer", "PUT", f"/records/{adopted['id']}", {
            "kind": "product", "name": "Edited native pump", "ifcClass": "IfcPumpType",
            "data": {"nativeProperties": {"warrantyYears": 5}}, "expectedRevision": 1})
        self.assertEqual(changed["datasetId"], project["projectId"])
        async with self.nodes["did:web:manufacturer.example"]["sessions"]() as session:
            dataset = await session.get(service.IfcDatasetRecord, ("did:web:manufacturer.example", project["projectId"]))
            graph = service.flatten_ifc_layers([service.IfcxFile.model_validate(dataset.file_json)],
                authority_did="did:web:manufacturer.example", dataset_id=project["projectId"])
            self.assertEqual(len(graph.entities), 2)
            native = graph.entities[entity_path].components[service.SOURCE_SCHEMA]
            self.assertEqual(native["class"], "IfcPumpType")
            self.assertEqual(native["properties"]["warrantyYears"], 5)
            self.assertEqual(native["properties"]["mappingSchema"], "IFC4X3_ADD2")
            self.assertEqual(native["properties"]["ratedPower"], 42)
            self.assertEqual(native["id"], original_id)
            self.assertEqual(native["format"], "IFC")
            self.assertEqual(graph.entities[entity_path].components["urn:test:custom"], "untouched")
            self.assertEqual(graph.entities[entity_path].children, {"library": "library"})
            self.assertEqual(graph.entities[entity_path].inherits, {"library": "library"})
        published = await self.publish("manufacturer", changed)
        self.assertEqual(published["graphPath"], entity_path)
        await self.call("manufacturer", "POST", "/records/adopt", {
            **body, "entityPath": "imported/type", "expectedSequence": sequence + 3, "idempotencyKey": "foreign"}, 403)
