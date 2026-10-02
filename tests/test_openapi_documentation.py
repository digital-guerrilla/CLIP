import unittest

from node.app.main import app


class OpenApiDocumentationTests(unittest.TestCase):
    def test_openapi_spec_includes_api_key_security_and_tags(self):
        schema = app.openapi()

        self.assertIn("components", schema)
        self.assertIn("securitySchemes", schema["components"])
        self.assertIn("x_api_key", schema["components"]["securitySchemes"])

        self.assertIn("tags", schema)
        self.assertTrue(any(tag.get("name") == "ifc" for tag in schema["tags"]))
        self.assertTrue(any(tag.get("name") == "node" for tag in schema["tags"]))
        self.assertFalse(any(path.startswith("/v3") for path in schema["paths"]))

    def test_supply_chain_workflows_are_documented_with_revision_preconditions(self):
        schema = app.openapi()
        prefix = "/clip/v1/supply-chain"
        required = {
            "/records": {"get", "post"},
            "/records/adoption-candidates": {"get"},
            "/records/adopt": {"post"},
            "/records/{record_id}": {"get", "put"},
            "/records/{record_id}/publish": {"post"},
            "/records/{record_id}/revisions": {"get", "post"},
            "/records/{record_id}/documents/{document_id}/detach": {"post"},
            "/dependencies/preview": {"post"},
            "/catalogue": {"get"},
            "/catalogue/discover": {"post"},
            "/projects/connect": {"post"},
            "/projects/refresh": {"post"},
            "/projects/{project_id}/senders": {"get", "put"},
            "/submissions": {"get", "post"},
            "/submissions/{submission_id}/issue": {"post"},
            "/submissions/{submission_id}/decision": {"post"},
            "/submissions/{submission_id}/documents/{document_id}": {"get"},
            "/receive": {"post"},
        }
        for path, methods in required.items():
            with self.subTest(path=path):
                self.assertTrue(methods <= set(schema["paths"][prefix + path]))
        self.assertTrue(any(tag["name"] == "supply-chain" for tag in schema["tags"]))
        edit_schema = schema["components"]["schemas"]["RecordUpdate"]
        self.assertIn("expectedRevision", edit_schema["required"])
        self.assertIn("idempotencyKey", schema["components"]["schemas"]["IssueRequest"]["required"])


if __name__ == "__main__":
    unittest.main()
