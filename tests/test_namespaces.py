import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

from node.app.api.did import did_document
from node.app.config import Settings, settings
from node.app.core.crypto import NodeKeyManager
from node.app.db.migrations import (
    NAMESPACE_TABLES,
    REVISIONS,
    checksum,
    ledger,
    upgrade,
)
from node.app.main import app


class NamespaceTest(unittest.TestCase):
    def test_routes_separate_clip_infrastructure_from_ifc_graph(self):
        paths = app.openapi()["paths"]
        for path in paths:
            self.assertFalse(path.startswith("/ifcx/"), path)
            if path.startswith("/ifc/v1"):
                self.assertTrue(
                    path.startswith(("/ifc/v1/datasets", "/ifc/v1/proposals", "/ifc/v1/decisions",
                                     "/ifc/v1/imports", "/ifc/v1/projects")),
                    path,
                )
                self.assertTrue(all("ifc" in operation["tags"] or "imports" in operation["tags"]
                                    for operation in paths[path].values()))
        for path in (
            "/clip/v1/network/gossip/sync", "/clip/v1/network/gossip/peers",
            "/clip/v1/node/info", "/clip/v1/replication/receive", "/clip/v1/evidence/upload",
            "/clip/v1/projects", "/clip/v1/supply-chain/records",
            "/ifc/v1/datasets", "/ifc/v1/proposals", "/ifc/v1/decisions",
            "/ifc/v1/projects/templates", "/ifc/v1/projects/{project_id}/entities",
        ):
            self.assertIn(path, paths)
        client = TestClient(app)
        try:
            self.assertEqual(client.get("/ifcx/v1/node/info").status_code, 404)
            self.assertEqual(client.get("/ifc/v1/network/gossip/peers").status_code, 404)
            self.assertEqual(client.get("/clip/v1/datasets").status_code, 404)
            self.assertEqual(client.get("/").json()["protocol_version"], "clip/v1")
        finally:
            client.close()

    def test_configuration_has_only_clip_network_names(self):
        self.assertFalse(any(name.startswith("IFCX_") for name in Settings.model_fields))
        self.assertNotIn("GOSSIP_SEEDS", Settings.model_fields)
        for name in (
            "CLIP_GOSSIP_ENABLED", "CLIP_GOSSIP_SEEDS", "CLIP_GOSSIP_INTERVAL",
            "CLIP_GOSSIP_SUSPECT_TIMEOUT", "CLIP_GOSSIP_DEAD_TIMEOUT",
            "CLIP_ALLOW_HTTP_LOOPBACK", "CLIP_EGRESS_ALLOWED_HOSTS",
            "CLIP_TRUSTED_PUBLISHERS", "CLIP_MAX_SERVICE_BYTES",
        ):
            self.assertIn(name, Settings.model_fields)

    def test_namespace_migration_preserves_rows_and_frozen_checksums(self):
        engine = create_engine("sqlite://")
        try:
            with engine.begin() as connection:
                ledger.create(connection)
                connection.exec_driver_sql('ALTER TABLE "clip_schema_revisions" RENAME TO "ifcx_schema_revisions"')
                for revision, tables in enumerate(REVISIONS[:8], start=1):
                    for table in tables:
                        table.create(connection)
                    connection.execute(text("INSERT INTO ifcx_schema_revisions VALUES (:revision, :checksum)"),
                                       {"revision": revision, "checksum": checksum(tables)})
                connection.execute(text("INSERT INTO ifcx_peers (peer_did,status,generation) VALUES ('did:web:peer.example','alive',7)"))
                connection.execute(text("INSERT INTO ifcx_authority_sequences VALUES ('did:web:owner.example',12)"))
                connection.execute(text("INSERT INTO ifcx_supply_chain_records VALUES ('product','did:web:owner.example','product',NULL,3,'{\"original\":\"signed history\"}')"))
                upgrade(connection)
                upgrade(connection)
                names = set(inspect(connection).get_table_names())
                self.assertTrue(set(NAMESPACE_TABLES.values()) <= names)
                self.assertFalse(set(NAMESPACE_TABLES) & names)
                self.assertNotIn("ifcx_schema_revisions", names)
                self.assertEqual(connection.execute(text("SELECT generation FROM clip_peers")).scalar(), 7)
                self.assertEqual(connection.execute(text("SELECT sequence FROM ifc_authority_sequences")).scalar(), 12)
                self.assertEqual(connection.execute(text("SELECT record_json FROM clip_supply_chain_records")).scalar(), '{"original":"signed history"}')
                checksums = {revision: digest for revision, digest in
                             connection.execute(text("SELECT revision,checksum FROM clip_schema_revisions")).all()}
                for revision, tables in enumerate(REVISIONS[:8], start=1):
                    self.assertEqual(checksums[revision], checksum(tables))
        finally:
            engine.dispose()


class DidNamespaceTest(unittest.IsolatedAsyncioTestCase):
    async def test_advertised_clip_services_use_registered_routes(self):
        authority = "did:web:namespace.example"
        with patch.object(settings, "DID_WEB_ID", authority), \
             patch.object(settings, "DID_VERIFICATION_METHOD", authority + "#key"), \
             patch.object(settings, "NODE_API_BASE", "https://namespace.example"), \
             patch.object(settings, "CLIP_GOSSIP_ENABLED", True):
            document = await did_document(NodeKeyManager(bytes(range(32))))
        paths = app.openapi()["paths"]
        for service in document["service"]:
            self.assertTrue(service["type"].startswith("Clip"))
            route = service["serviceEndpoint"].removeprefix("https://namespace.example")
            self.assertTrue(route.startswith("/clip/v1/"), route)
            self.assertIn(route, paths)


if __name__ == "__main__":
    unittest.main()
