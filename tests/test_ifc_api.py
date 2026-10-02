import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import nacl.signing
from fastapi.testclient import TestClient

from node.app import dependencies
from node.app.api import ifc_transactions
from node.app.config import settings
from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.did import verify_clip_message_proof
from node.app.core.ifc_protocol import IfcDecisionTransaction, IfcProposalTransaction
from node.app.core.ifcx_models import IfcxFile
from node.app.federation.clip_layers import (
    resolve_ifc_layers as resolve_federated_layers,
    sha256_sri_integrity,
)
from node.app.main import app


def _did_document(did: str, verification_method: str, key_multibase: str) -> dict:
    return {
        "id": did,
        "verificationMethod": [{
            "id": verification_method,
            "type": "Multikey",
            "controller": did,
            "publicKeyMultibase": key_multibase,
        }],
        "assertionMethod": [verification_method],
        "capabilityInvocation": [verification_method],
    }


def _sign_model(model, key: nacl.signing.SigningKey, method: str, purpose: str) -> dict:
    unsigned = model.model_dump(mode="json", by_alias=True, exclude={"proof"})
    return add_data_integrity_proof(
        unsigned,
        key,
        verification_method=method,
        proof_purpose=purpose,
        created=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )


def _signed_publication(file: IfcxFile, publisher_did: str, key: nacl.signing.SigningKey) -> bytes:
    signed = add_data_integrity_proof(
        {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "publisherDid": publisher_did,
            "file": file.model_dump(mode="json", by_alias=True),
        },
        key,
        verification_method=f"{publisher_did}#publication-key",
        proof_purpose="assertionMethod",
        created=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    return json.dumps(signed).encode("utf-8")


class IfcxApiTest(unittest.TestCase):
    @patch.object(settings, "CLIP_TRUSTED_PUBLISHERS", "did:web:manufacturer.example")
    def test_owner_api_resolves_imported_manufacturer_type(self) -> None:
        owner_did = "did:web:owner.example"
        manufacturer_did = "did:web:manufacturer.example"
        manufacturer_method = f"{manufacturer_did}#publication-key"
        manufacturer_key = nacl.signing.SigningKey(bytes(range(32)))
        manufacturer_document = _did_document(
            manufacturer_did,
            manufacturer_method,
            NodeKeyManager(bytes(manufacturer_key)).public_key_multibase,
        )
        manufacturer_file = IfcxFile.model_validate({
            "header": {
                "id": "urn:manufacturer:catalog:v1",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "manufacturer",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {"ifc::name": {"value": {"dataType": "String"}}},
            "data": [{
                "path": "types/door",
                "attributes": {"ifc::name": "Northstar Door Model X"},
            }],
        })
        manufacturer_uri = "https://manufacturer.example/ifcx/catalog.json"
        publication = _signed_publication(
            manufacturer_file,
            manufacturer_did,
            manufacturer_key,
        )
        original = {
            "DATABASE_URL": settings.DATABASE_URL,
            "PRIVATE_KEY_FILE": settings.PRIVATE_KEY_FILE,
            "DID_WEB_ID": settings.DID_WEB_ID,
            "DID_VERIFICATION_METHOD": settings.DID_VERIFICATION_METHOD,
            "NODE_DOMAIN": settings.NODE_DOMAIN,
            "NODE_API_BASE": settings.NODE_API_BASE,
            "API_KEY": settings.API_KEY,
        }
        original_key_manager = dependencies._key_manager

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'federated.ifcx.db'}"
                settings.PRIVATE_KEY_FILE = str(root / "owner.key")
                settings.DID_WEB_ID = owner_did
                settings.DID_VERIFICATION_METHOD = f"{owner_did}#authority-key"
                settings.NODE_DOMAIN = "owner.example"
                settings.NODE_API_BASE = "https://owner.example"
                settings.API_KEY = "ifcx-test-key"
                dependencies._key_manager = None

                async def fetch_import(uri: str) -> bytes:
                    self.assertEqual(uri, manufacturer_uri)
                    return publication

                async def resolve_did(did: str) -> dict:
                    self.assertEqual(did, manufacturer_did)
                    return manufacturer_document

                async def resolve_layers(file: IfcxFile):
                    return await resolve_federated_layers(
                        file,
                        fetcher=fetch_import,
                        did_resolver=resolve_did,
                    )

                with TestClient(app) as client, patch.object(
                    ifc_transactions,
                    "resolve_ifc_layers",
                    side_effect=resolve_layers,
                ):
                    registered = client.post(
                        "/ifc/v1/datasets",
                        headers={"x-api-key": "ifcx-test-key"},
                        json={
                            "file": {
                                "header": {
                                    "id": "urn:owner:building:v1",
                                    "ifcxVersion": "ifcx_alpha",
                                    "dataVersion": "1.0.0",
                                    "author": "owner",
                                    "timestamp": "2026-10-01T00:00:00Z",
                                },
                                "imports": [{
                                    "uri": manufacturer_uri,
                                    "integrity": sha256_sri_integrity(publication),
                                }],
                                "schemas": {},
                                "data": [{
                                    "path": "building/door-1",
                                    "inherits": {"type": "types/door"},
                                }],
                            },
                            "trustedProposers": [],
                        },
                    )
                    self.assertEqual(registered.status_code, 201, registered.text)
                    graph_response = client.get("/ifc/v1/datasets/urn:owner:building:v1/graph")
                    self.assertEqual(graph_response.status_code, 200, graph_response.text)
                    self.assertEqual(graph_response.json()["sources"], [{
                        "publisherDid": manufacturer_did,
                        "datasetId": manufacturer_file.header.id,
                        "uri": manufacturer_uri,
                        "integrity": sha256_sri_integrity(publication),
                        "entityPaths": ["types/door"],
                    }])
                    resolved = client.get(
                        "/ifc/v1/datasets/urn:owner:building:v1/components",
                        params={
                            "entity_path": "building/door-1",
                            "component_schema_id": "ifc::name",
                        },
                    )

                self.assertEqual(resolved.status_code, 200, resolved.text)
                self.assertEqual(resolved.json()["value"], "Northstar Door Model X")
        finally:
            for name, value in original.items():
                setattr(settings, name, value)
            dependencies._key_manager = original_key_manager

    def test_proposal_acceptance_returns_verifiable_authority_receipt(self) -> None:
        owner_did = "did:web:owner.example"
        supplier_did = "did:web:supplier.example"
        owner_method = f"{owner_did}#authority-key"
        supplier_method = f"{supplier_did}#assertion-key"
        original = {
            "DATABASE_URL": settings.DATABASE_URL,
            "PRIVATE_KEY_FILE": settings.PRIVATE_KEY_FILE,
            "DID_WEB_ID": settings.DID_WEB_ID,
            "DID_VERIFICATION_METHOD": settings.DID_VERIFICATION_METHOD,
            "NODE_DOMAIN": settings.NODE_DOMAIN,
            "NODE_API_BASE": settings.NODE_API_BASE,
            "API_KEY": settings.API_KEY,
        }
        original_key_manager = dependencies._key_manager

        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'ifcx.db'}"
                settings.PRIVATE_KEY_FILE = str(root / "authority.key")
                settings.DID_WEB_ID = owner_did
                settings.DID_VERIFICATION_METHOD = owner_method
                settings.NODE_DOMAIN = "owner.example"
                settings.NODE_API_BASE = "https://owner.example"
                settings.API_KEY = "ifcx-test-key"
                dependencies._key_manager = None

                with TestClient(app) as client:
                    owner_keys = dependencies.get_key_manager()
                    owner_did_document = _did_document(
                        owner_did,
                        owner_method,
                        owner_keys.public_key_multibase,
                    )
                    supplier_key = nacl.signing.SigningKey(bytes(range(32)))
                    supplier_did_document = _did_document(
                        supplier_did,
                        supplier_method,
                        NodeKeyManager(bytes(supplier_key)).public_key_multibase,
                    )
                    untrusted_did = "did:web:untrusted.example"
                    untrusted_method = f"{untrusted_did}#assertion-key"
                    untrusted_key = nacl.signing.SigningKey(bytes(reversed(range(32))))
                    untrusted_did_document = _did_document(
                        untrusted_did,
                        untrusted_method,
                        NodeKeyManager(bytes(untrusted_key)).public_key_multibase,
                    )
                    did_documents = {
                        owner_did: owner_did_document,
                        supplier_did: supplier_did_document,
                        untrusted_did: untrusted_did_document,
                    }

                    dataset_response = client.post(
                        "/ifc/v1/datasets",
                        headers={"x-api-key": "ifcx-test-key"},
                        json={
                            "file": {
                                "header": {
                                    "id": "building.ifcx",
                                    "ifcxVersion": "ifcx_alpha",
                                    "dataVersion": "1.0.0",
                                    "author": "owner",
                                    "timestamp": "2026-10-01T00:00:00Z",
                                },
                                "imports": [],
                                "schemas": {
                                    "ifc::name": {"value": {"dataType": "String"}},
                                },
                                "data": [{
                                    "path": "building/door-1",
                                    "attributes": {"ifc::name": "Door"},
                                }],
                            },
                            "trustedProposers": [supplier_did],
                        },
                    )
                    self.assertEqual(dataset_response.status_code, 201, dataset_response.text)
                    schema_digest = dataset_response.json()["schemaDigest"]
                    publication_response = client.get(
                        "/ifc/v1/datasets/building.ifcx/publication"
                    )
                    self.assertEqual(
                        publication_response.status_code,
                        200,
                        publication_response.text,
                    )
                    self.assertTrue(
                        verify_clip_message_proof(
                            publication_response.json(),
                            owner_did_document,
                        )
                    )
                    repeated_publication = client.get(
                        "/ifc/v1/datasets/building.ifcx/publication"
                    )
                    self.assertEqual(repeated_publication.content, publication_response.content)
                    denied_policy_update = client.put(
                        "/ifc/v1/datasets/building.ifcx/trusted-proposers",
                        json={"trustedProposers": [supplier_did]},
                    )
                    self.assertEqual(denied_policy_update.status_code, 401)
                    policy_update = client.put(
                        "/ifc/v1/datasets/building.ifcx/trusted-proposers",
                        headers={"x-api-key": "ifcx-test-key"},
                        json={"trustedProposers": [supplier_did]},
                    )
                    self.assertEqual(policy_update.status_code, 200, policy_update.text)
                    self.assertEqual(policy_update.json()["trustedProposers"], [supplier_did])

                    async def resolve_did(did: str) -> dict:
                        return did_documents[did]

                    untrusted_proposal = IfcProposalTransaction.model_validate({
                        "actorDid": untrusted_did,
                        "target": {
                            "authorityDid": owner_did,
                            "datasetId": "building.ifcx",
                            "entityPath": "building/door-1",
                            "componentSchemaId": "ifc::name",
                        },
                        "change": {"action": "set", "value": "Untrusted change"},
                        "expectedSequence": 0,
                        "schemaDigest": schema_digest,
                        "created": "2026-10-01T00:00:00Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:00Z",
                            "verificationMethod": untrusted_method,
                            "proofPurpose": "assertionMethod",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_untrusted_proposal = _sign_model(
                        untrusted_proposal,
                        untrusted_key,
                        untrusted_method,
                        "assertionMethod",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        untrusted_response = client.post(
                            "/ifc/v1/proposals",
                            json=signed_untrusted_proposal,
                        )
                    self.assertEqual(untrusted_response.status_code, 403)

                    proposal = IfcProposalTransaction.model_validate({
                        "actorDid": supplier_did,
                        "target": {
                            "authorityDid": owner_did,
                            "datasetId": "building.ifcx",
                            "entityPath": "building/door-1",
                            "componentSchemaId": "ifc::name",
                        },
                        "change": {"action": "set", "value": "Fire door"},
                        "expectedSequence": 0,
                        "schemaDigest": schema_digest,
                        "created": "2026-10-01T00:00:00Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:00Z",
                            "verificationMethod": supplier_method,
                            "proofPurpose": "assertionMethod",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_proposal = _sign_model(
                        proposal,
                        supplier_key,
                        supplier_method,
                        "assertionMethod",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        proposal_response = client.post(
                            "/ifc/v1/proposals",
                            json=signed_proposal,
                        )
                        self.assertEqual(proposal_response.status_code, 201, proposal_response.text)
                        proposal_result = proposal_response.json()

                        decision = IfcDecisionTransaction.model_validate({
                            "actorDid": owner_did,
                            "decision": "accept",
                            "proposalId": proposal_result["proposalId"],
                            "proposalDigest": proposal_result["proposalDigest"],
                            "expectedSequence": 0,
                            "created": "2026-10-01T00:00:01Z",
                            "proof": {
                                "type": "DataIntegrityProof",
                                "cryptosuite": "eddsa-jcs-2022",
                                "created": "2026-10-01T00:00:01Z",
                                "verificationMethod": owner_method,
                                "proofPurpose": "capabilityInvocation",
                                "proofValue": "z" + "1" * 86,
                            },
                        })
                        signed_decision = _sign_model(
                            decision,
                            nacl.signing.SigningKey(owner_keys.private_key_bytes),
                            owner_method,
                            "capabilityInvocation",
                        )
                        decision_response = client.post(
                            "/ifc/v1/decisions",
                            json=signed_decision,
                        )

                    self.assertEqual(decision_response.status_code, 200, decision_response.text)
                    receipt = decision_response.json()
                    self.assertTrue(receipt["accepted"])
                    self.assertEqual(receipt["sequence"], 1)
                    self.assertTrue(verify_clip_message_proof(receipt, owner_did_document))
                    component_response = client.get(
                        "/ifc/v1/datasets/building.ifcx/components",
                        params={
                            "entity_path": "building/door-1",
                            "component_schema_id": "ifc::name",
                        },
                    )
                    self.assertEqual(component_response.status_code, 200)
                    self.assertEqual(component_response.json()["value"], "Fire door")

                    next_proposal = IfcProposalTransaction.model_validate({
                        "actorDid": supplier_did,
                        "target": {
                            "authorityDid": owner_did,
                            "datasetId": "building.ifcx",
                            "entityPath": "building/door-1",
                            "componentSchemaId": "ifc::name",
                        },
                        "change": {"action": "set", "value": "Rejected label"},
                        "expectedSequence": 1,
                        "schemaDigest": schema_digest,
                        "created": "2026-10-01T00:00:02Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:02Z",
                            "verificationMethod": supplier_method,
                            "proofPurpose": "assertionMethod",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_next_proposal = _sign_model(
                        next_proposal,
                        supplier_key,
                        supplier_method,
                        "assertionMethod",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        next_proposal_response = client.post(
                            "/ifc/v1/proposals",
                            json=signed_next_proposal,
                        )
                    self.assertEqual(
                        next_proposal_response.status_code,
                        201,
                        next_proposal_response.text,
                    )
                    next_proposal_result = next_proposal_response.json()
                    rejection = IfcDecisionTransaction.model_validate({
                        "actorDid": owner_did,
                        "decision": "reject",
                        "proposalId": next_proposal_result["proposalId"],
                        "proposalDigest": next_proposal_result["proposalDigest"],
                        "expectedSequence": 1,
                        "created": "2026-10-01T00:00:03Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:03Z",
                            "verificationMethod": owner_method,
                            "proofPurpose": "capabilityInvocation",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_rejection = _sign_model(
                        rejection,
                        nacl.signing.SigningKey(owner_keys.private_key_bytes),
                        owner_method,
                        "capabilityInvocation",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        rejection_response = client.post(
                            "/ifc/v1/decisions",
                            json=signed_rejection,
                        )

                    self.assertEqual(rejection_response.status_code, 200, rejection_response.text)
                    rejection_receipt = rejection_response.json()
                    self.assertFalse(rejection_receipt["accepted"])
                    self.assertEqual(rejection_receipt["sequence"], 1)
                    self.assertTrue(verify_clip_message_proof(rejection_receipt, owner_did_document))
                    unchanged_component = client.get(
                        "/ifc/v1/datasets/building.ifcx/components",
                        params={
                            "entity_path": "building/door-1",
                            "component_schema_id": "ifc::name",
                        },
                    )
                    self.assertEqual(unchanged_component.json()["value"], "Fire door")

                    remove_proposal = IfcProposalTransaction.model_validate({
                        "actorDid": supplier_did,
                        "target": {
                            "authorityDid": owner_did,
                            "datasetId": "building.ifcx",
                            "entityPath": "building/door-1",
                            "componentSchemaId": "ifc::name",
                        },
                        "change": {"action": "remove"},
                        "expectedSequence": 1,
                        "schemaDigest": schema_digest,
                        "created": "2026-10-01T00:00:04Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:04Z",
                            "verificationMethod": supplier_method,
                            "proofPurpose": "assertionMethod",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_remove = _sign_model(
                        remove_proposal,
                        supplier_key,
                        supplier_method,
                        "assertionMethod",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        remove_response = client.post(
                            "/ifc/v1/proposals",
                            json=signed_remove,
                        )
                    self.assertEqual(remove_response.status_code, 201, remove_response.text)
                    remove_proposal_result = remove_response.json()
                    remove_decision = IfcDecisionTransaction.model_validate({
                        "actorDid": owner_did,
                        "decision": "accept",
                        "proposalId": remove_proposal_result["proposalId"],
                        "proposalDigest": remove_proposal_result["proposalDigest"],
                        "expectedSequence": 1,
                        "created": "2026-10-01T00:00:05Z",
                        "proof": {
                            "type": "DataIntegrityProof",
                            "cryptosuite": "eddsa-jcs-2022",
                            "created": "2026-10-01T00:00:05Z",
                            "verificationMethod": owner_method,
                            "proofPurpose": "capabilityInvocation",
                            "proofValue": "z" + "1" * 86,
                        },
                    })
                    signed_remove_decision = _sign_model(
                        remove_decision,
                        nacl.signing.SigningKey(owner_keys.private_key_bytes),
                        owner_method,
                        "capabilityInvocation",
                    )
                    with patch.object(
                        ifc_transactions,
                        "resolve_did_web_document",
                        side_effect=resolve_did,
                    ):
                        remove_decision_response = client.post(
                            "/ifc/v1/decisions",
                            json=signed_remove_decision,
                        )
                    self.assertEqual(
                        remove_decision_response.status_code,
                        200,
                        remove_decision_response.text,
                    )
                    removed_component = client.get(
                        "/ifc/v1/datasets/building.ifcx/components",
                        params={
                            "entity_path": "building/door-1",
                            "component_schema_id": "ifc::name",
                        },
                    )
                    self.assertEqual(removed_component.status_code, 404)
        finally:
            for name, value in original.items():
                setattr(settings, name, value)
            dependencies._key_manager = original_key_manager


if __name__ == "__main__":
    unittest.main()