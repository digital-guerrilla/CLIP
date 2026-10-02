import unittest

from pydantic import ValidationError

from node.app.core.ifc_graph import (
    IfcComponentAddress,
    apply_ifc_component_change,
    flatten_ifc_layers,
)
from node.app.core.ifcx_models import (
    CLIP_COMPONENT_DELETIONS_SCHEMA_ID,
    IfcxFile,
    IfcxValidationError,
    ensure_clip_component_deletion_schema,
    ifcx_schema_digest,
    validate_ifcx_attributes,
)
from node.app.core.ifc_protocol import (
    IfcComponentChange,
    IfcDecisionTransaction,
    IfcProposalTransaction,
)


class IfcxModelsTest(unittest.TestCase):
    def test_component_proposal_and_owner_decision_bind_the_same_proposal(self) -> None:
        address = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
            entity_path="building/door-1",
            component_schema_id="ifc::name",
        )
        proof = {
            "type": "DataIntegrityProof",
            "cryptosuite": "eddsa-jcs-2022",
            "created": "2026-10-01T00:00:00Z",
            "verificationMethod": "did:web:supplier.example#assertion-key",
            "proofPurpose": "assertionMethod",
            "proofValue": "z" + "1" * 86,
        }
        proposal = IfcProposalTransaction.model_validate({
            "actor_did": "did:web:supplier.example",
            "target": address.model_dump(by_alias=True),
            "change": {"action": "set", "value": "Fire door"},
            "expected_sequence": 4,
            "schema_digest": "a" * 64,
            "created": "2026-10-01T00:00:00Z",
            "proof": proof,
        })
        decision = IfcDecisionTransaction.model_validate({
            "actor_did": "did:web:owner.example",
            "decision": "accept",
            "proposal_id": str(proposal.transaction_id),
            "proposal_digest": "b" * 64,
            "expected_sequence": 4,
            "created": "2026-10-01T00:00:01Z",
            "proof": {**proof, "proofPurpose": "capabilityInvocation"},
        })

        self.assertEqual(proposal.target, address)
        self.assertEqual(decision.proposal_id, proposal.transaction_id)
        self.assertEqual(decision.proof.proof_purpose, "capabilityInvocation")
        self.assertEqual(
            proposal.model_dump(mode="json", by_alias=True)["@context"][0],
            "https://w3id.org/security/data-integrity/v2",
        )

    def test_transaction_roles_require_their_did_verification_relationship(self) -> None:
        proof = {
            "type": "DataIntegrityProof",
            "cryptosuite": "eddsa-jcs-2022",
            "created": "2026-10-01T00:00:00Z",
            "verificationMethod": "did:web:example.org#key-1",
            "proofPurpose": "capabilityInvocation",
            "proofValue": "z" + "1" * 86,
        }

        with self.assertRaises(ValidationError):
            IfcProposalTransaction.model_validate({
                "actorDid": "did:web:supplier.example",
                "target": {
                    "authorityDid": "did:web:owner.example",
                    "datasetId": "building.ifcx",
                    "entityPath": "building/door-1",
                    "componentSchemaId": "ifc::name",
                },
                "change": {"action": "set", "value": "Fire door"},
                "expectedSequence": 4,
                "schemaDigest": "a" * 64,
                "created": "2026-10-01T00:00:00Z",
                "proof": proof,
            })

    def test_component_change_requires_value_only_for_set(self) -> None:
        with self.assertRaises(ValidationError):
            IfcComponentChange(action="set")
        with self.assertRaises(ValidationError):
            IfcComponentChange(action="set", value=None)
        with self.assertRaises(ValidationError):
            IfcComponentChange(action="remove", value=None)
        removal = IfcComponentChange(action="remove")
        self.assertEqual(removal.action, "remove")
        self.assertNotIn("value", removal.model_dump())

    def test_graph_flattens_entities_and_schema_components_in_layer_order(self) -> None:
        header = {
            "id": "example.ifcx",
            "ifcxVersion": "ifcx_alpha",
            "dataVersion": "1.0.0",
            "author": "example-author",
            "timestamp": "2026-10-01T00:00:00Z",
        }
        schemas = {
            "ifc::name": {"value": {"dataType": "String"}},
            "ifc::serial": {"value": {"dataType": "String"}},
        }
        base = IfcxFile.model_validate({
            "header": header,
            "imports": [],
            "schemas": schemas,
            "data": [
                {
                    "path": "building/door",
                    "children": {"leaf": "types/door/leaf", "removed": "types/old"},
                    "inherits": {"type": "types/door", "obsolete": "types/old"},
                    "attributes": {"ifc::name": "Door", "ifc::serial": "D-01"},
                },
                {
                    "path": "types",
                    "children": {"door": "types/door"},
                },
                {
                    "path": "types/door",
                    "attributes": {"ifc::name": "Door type"},
                },
            ],
        })
        overlay = IfcxFile.model_validate({
            "header": header,
            "imports": [],
            "schemas": schemas,
            "data": [{
                "path": "building/door",
                "children": {"removed": None},
                "inherits": {"obsolete": None},
                "attributes": {"ifc::name": "Fire door"},
            }],
        })

        graph = flatten_ifc_layers(
            [base, overlay],
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
        )
        entity = graph.entities["building/door"]

        self.assertEqual(entity.components, {"ifc::name": "Fire door", "ifc::serial": "D-01"})
        self.assertEqual(entity.children, {"leaf": "types/door/leaf"})
        self.assertEqual(entity.inherits, {"type": "types/door"})
        self.assertEqual(set(graph.schemas), {"ifc::name", "ifc::serial"})
        address = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
            entity_path="building/door",
            component_schema_id="ifc::name",
        )
        self.assertEqual(graph.resolve_component(address), "Fire door")
        foreign_address = address.model_copy(update={"dataset_id": "other.ifcx"})
        with self.assertRaisesRegex(ValueError, "different authority or dataset"):
            graph.resolve_component(foreign_address)

    def test_component_address_identifies_the_instance_not_only_its_type(self) -> None:
        first = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
            entity_path="building/door-1",
            component_schema_id="ifc::name",
        )
        second = first.model_copy(update={"entity_path": "building/door-2"})

        self.assertNotEqual(first, second)

    def test_component_resolution_inherits_type_values_then_applies_instance_values(self) -> None:
        header = {
            "id": "building.ifcx",
            "ifcxVersion": "ifcx_alpha",
            "dataVersion": "1.0.0",
            "author": "example-author",
            "timestamp": "2026-10-01T00:00:00Z",
        }
        layer = IfcxFile.model_validate({
            "header": header,
            "imports": [],
            "schemas": {
                "ifc::name": {"value": {"dataType": "String"}},
                "ifc::serial": {"value": {"dataType": "String"}},
            },
            "data": [
                {
                    "path": "types",
                    "children": {"door": "types/door"},
                },
                {
                    "path": "types/door",
                    "attributes": {"ifc::name": "Door"},
                },
                {
                    "path": "building/door-1",
                    "inherits": {"type": "types/door"},
                    "attributes": {"ifc::serial": "D-101", "ifc::name": "Fire door"},
                },
            ],
        })
        graph = flatten_ifc_layers(
            [layer],
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
        )
        address = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
            entity_path="building/door-1",
            component_schema_id="ifc::name",
        )

        self.assertEqual(graph.resolve_component(address), "Fire door")
        self.assertEqual(
            graph.effective_components("building/door-1"),
            {"ifc::name": "Fire door", "ifc::serial": "D-101"},
        )

    def test_versioned_tombstone_removes_inherited_component_and_later_set_restores_it(self) -> None:
        base_file = IfcxFile.model_validate({
            "header": {
                "id": "building.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {"ifc::name": {"value": {"dataType": "String"}}},
            "data": [
                {"path": "types", "children": {"door": "types/door"}},
                {"path": "types/door", "attributes": {"ifc::name": "Door"}},
                {"path": "building/door-1", "inherits": {"type": "types/door"}},
            ],
        })
        file = ensure_clip_component_deletion_schema(base_file)
        address = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
            entity_path="building/door-1",
            component_schema_id="ifc::name",
        )

        removed = apply_ifc_component_change(file, address, action="remove")
        removed_graph = flatten_ifc_layers(
            [removed],
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
        )
        self.assertNotIn("ifc::name", removed_graph.effective_components("building/door-1"))
        self.assertEqual(
            removed.data[-1].attributes[CLIP_COMPONENT_DELETIONS_SCHEMA_ID],
            ["ifc::name"],
        )
        self.assertIn(CLIP_COMPONENT_DELETIONS_SCHEMA_ID, file.schemas)
        self.assertNotEqual(ifcx_schema_digest(base_file), ifcx_schema_digest(file))

        restored = apply_ifc_component_change(
            removed,
            address,
            action="set",
            value="Fire door",
        )
        restored_graph = flatten_ifc_layers(
            [restored],
            authority_did="did:web:owner.example",
            dataset_id="building.ifcx",
        )
        self.assertEqual(restored_graph.resolve_component(address), "Fire door")
        self.assertEqual(
            restored.data[-1].attributes[CLIP_COMPONENT_DELETIONS_SCHEMA_ID],
            [],
        )

    def test_ifcx_file_round_trips_with_layer_contributions(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {
                "ifc::name": {
                    "uri": "https://example.org/ifc/name",
                    "value": {"dataType": "String"},
                },
            },
            "data": [
                {
                    "path": "root",
                    "children": {"door": "root/door", "removed": None},
                    "inherits": {},
                    "attributes": {"ifc::name": "Building"},
                },
                {
                    "path": "root",
                    "attributes": {"ifc::name": "Building - revised"},
                },
            ],
        }

        parsed = IfcxFile.model_validate(document)

        self.assertEqual(
            parsed.model_dump(mode="json", by_alias=True, exclude_unset=True),
            document,
        )
        self.assertEqual([node.path for node in parsed.data], ["root", "root"])
        self.assertIsNone(parsed.data[0].children["removed"])
        validate_ifcx_attributes(parsed)

    def test_attributes_validate_inherited_and_nested_schemas(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {
                "base::name": {"value": {"dataType": "String"}},
                "ifc::name": {
                    "value": {
                        "dataType": "String",
                        "inherits": ["base::name"],
                    },
                },
                "ifc::codes": {
                    "value": {
                        "dataType": "Array",
                        "arrayRestrictions": {
                            "min": 1,
                            "max": 2,
                            "value": {
                                "dataType": "Enum",
                                "enumRestrictions": {"options": ["A", "B"]},
                            },
                        },
                    },
                },
                "ifc::details": {
                    "value": {
                        "dataType": "Object",
                        "objectRestrictions": {
                            "values": {
                                "enabled": {"dataType": "Boolean"},
                                "note": {"dataType": "String", "optional": True},
                            },
                        },
                    },
                },
            },
            "data": [{
                "path": "root",
                "attributes": {
                    "ifc::name": "Door",
                    "ifc::codes": ["A"],
                    "ifc::details": {"enabled": True},
                },
            }],
        }

        validate_ifcx_attributes(IfcxFile.model_validate(document))

    def test_invalid_attribute_values_are_rejected(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {
                "ifc::count": {"value": {"dataType": "Integer"}},
                "ifc::codes": {
                    "value": {
                        "dataType": "Array",
                        "arrayRestrictions": {
                            "min": 1,
                            "value": {"dataType": "String"},
                        },
                    },
                },
            },
            "data": [{
                "path": "root",
                "attributes": {"ifc::count": True, "ifc::codes": []},
            }],
        }

        with self.assertRaises(IfcxValidationError):
            validate_ifcx_attributes(IfcxFile.model_validate(document))

    def test_unknown_attribute_schema_is_rejected(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {},
            "data": [{"path": "root", "attributes": {"unknown::value": 1}}],
        }

        with self.assertRaises(IfcxValidationError):
            validate_ifcx_attributes(IfcxFile.model_validate(document))

    def test_blob_is_rejected_until_ifcx_defines_its_payload_rules(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {"ifc::blob": {"value": {"dataType": "Blob"}}},
            "data": [{"path": "root", "attributes": {"ifc::blob": "data"}}],
        }

        with self.assertRaisesRegex(IfcxValidationError, "Unsupported IFCX datatype"):
            validate_ifcx_attributes(IfcxFile.model_validate(document))

    def test_unknown_ifcx_data_type_is_rejected(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {"ifc::name": {"value": {"dataType": "Unknown"}}},
            "data": [],
        }

        with self.assertRaises(ValidationError):
            IfcxFile.model_validate(document)

    def test_unknown_file_fields_are_rejected(self) -> None:
        document = {
            "header": {
                "id": "example.ifcx",
                "ifcxVersion": "ifcx_alpha",
                "dataVersion": "1.0.0",
                "author": "example-author",
                "timestamp": "2026-10-01T00:00:00Z",
            },
            "imports": [],
            "schemas": {},
            "data": [],
            "unexpected": True,
        }

        with self.assertRaises(ValidationError):
            IfcxFile.model_validate(document)


if __name__ == "__main__":
    unittest.main()