import hashlib
import json
from pathlib import Path
import unittest

import rfc8785

from node.app.core.data_integrity import base58btc_encode, verify_data_integrity_proof
from node.app.core.did import verify_clip_message_proof
from node.app.core.ifc_graph import flatten_ifc_layers
from node.app.core.ifcx_models import IfcxFile, ensure_clip_component_deletion_schema

VECTORS = Path(__file__).parent / "vectors"


class IfcxInteropTest(unittest.TestCase):
    def test_independent_fixed_proof_vector(self):
        vector = json.loads((VECTORS / "ifcx-proof.json").read_text())
        document = vector["document"]
        options = vector["proofOptions"]
        document_hash = hashlib.sha256(rfc8785.dumps(document)).digest()
        options_hash = hashlib.sha256(rfc8785.dumps(options)).digest()
        self.assertEqual(document_hash.hex(), vector["documentSha256"])
        self.assertEqual(options_hash.hex(), vector["proofOptionsSha256"])
        secured = {**document, "proof": {**options, "proofValue": vector["proofValue"]}}
        public_key = bytes.fromhex(vector["publicKeyHex"])
        self.assertTrue(verify_data_integrity_proof(secured, public_key))
        method = options["verificationMethod"]
        did_document = {"id": document["actorDid"], "verificationMethod": [{"id": method, "type": "Multikey", "controller": document["actorDid"], "publicKeyMultibase": base58btc_encode(bytes.fromhex("ed01") + public_key)}], "authentication": [method]}
        self.assertTrue(verify_clip_message_proof(secured, did_document))
        try:
            import jcs
            from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
        except ImportError:
            self.skipTest("Install requirements-dev.txt for independent library verification")
        self.assertEqual(jcs.canonicalize(document), rfc8785.dumps(document))
        self.assertEqual(jcs.canonicalize(options), rfc8785.dumps(options))
        Ed25519PublicKey.from_public_bytes(public_key).verify(bytes.fromhex(vector["signatureHex"]), options_hash + document_hash)
        secured["payload"]["sequence"] = 2
        self.assertFalse(verify_data_integrity_proof(secured, public_key))

    def test_composition_vectors(self):
        vector = json.loads((VECTORS / "ifcx-composition.json").read_text())
        for case in vector["cases"]:
            with self.subTest(case=case["name"]):
                layers = [ensure_clip_component_deletion_schema(IfcxFile.model_validate({
                    "header": {"id": f"urn:vector:{index}", "ifcxVersion": "ifcx_alpha", "dataVersion": "1.0.0", "author": "vector", "timestamp": "2026-10-01T00:00:00Z"},
                    "imports": [], "schemas": vector["schemas"], "data": nodes,
                })) for index, nodes in enumerate(case["layers"])]
                graph = flatten_ifc_layers(layers, authority_did="did:web:vector.example", dataset_id=layers[0].header.id)
                self.assertEqual(graph.effective_components(case["entity"]), case["effective"])