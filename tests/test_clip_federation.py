import json
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

import nacl.signing

from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.ifc_graph import (
    IfcComponentAddress,
    apply_ifc_component_change,
    flatten_ifc_layers,
)
from node.app.core.ifcx_models import IfcxFile, ensure_clip_component_deletion_schema
from node.app.federation.clip_layers import (
    ClipFederationError,
    resolve_ifc_layers,
    sha256_sri_integrity,
)


def _ifcx_file(dataset_id: str, *, imports=None, schemas=None, data=None) -> IfcxFile:
    return IfcxFile.model_validate({
        "header": {
            "id": dataset_id,
            "ifcxVersion": "ifcx_alpha",
            "dataVersion": "1.0.0",
            "author": dataset_id,
            "timestamp": "2026-10-01T00:00:00Z",
        },
        "imports": imports or [],
        "schemas": schemas or {},
        "data": data or [],
    })


def _publication(file: IfcxFile, publisher_did: str, signing_key: nacl.signing.SigningKey) -> bytes:
    verification_method = f"{publisher_did}#publication-key"
    unsigned = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "publisherDid": publisher_did,
        "file": file.model_dump(mode="json", by_alias=True),
    }
    signed = add_data_integrity_proof(
        unsigned,
        signing_key,
        verification_method=verification_method,
        proof_purpose="assertionMethod",
        created=datetime(2026, 10, 1, tzinfo=timezone.utc),
    )
    return json.dumps(signed).encode("utf-8")


def _did_document(publisher_did: str, signing_key: nacl.signing.SigningKey) -> dict:
    verification_method = f"{publisher_did}#publication-key"
    return {
        "id": publisher_did,
        "verificationMethod": [{
            "id": verification_method,
            "type": "Multikey",
            "controller": publisher_did,
            "publicKeyMultibase": NodeKeyManager(bytes(signing_key)).public_key_multibase,
        }],
        "assertionMethod": [verification_method],
        "capabilityInvocation": [],
    }


class IfcxFederationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from node.app.config import settings

        trust = patch.object(settings, "CLIP_TRUSTED_PUBLISHERS", "did:web:manufacturer.example")
        trust.start()
        self.addCleanup(trust.stop)

    async def test_owner_asset_resolves_manufacturer_type_through_import(self) -> None:
        manufacturer_uri = "https://manufacturer.example/ifcx/catalog.json"
        manufacturer_did = "did:web:manufacturer.example"
        manufacturer_key = nacl.signing.SigningKey(bytes(range(32)))
        manufacturer_document = _did_document(manufacturer_did, manufacturer_key)
        manufacturer = _ifcx_file(
            "urn:manufacturer:catalog:v1",
            schemas={
                "ifc::name": {"value": {"dataType": "String"}},
                "ifc::manufacturer": {"value": {"dataType": "String"}},
            },
            data=[
                {"path": "types", "children": {"door": "types/door"}},
                {
                    "path": "types/door",
                    "attributes": {
                        "ifc::name": "Model X Door",
                        "ifc::manufacturer": "Northstar Doors",
                    },
                },
            ],
        )
        manufacturer_bytes = _publication(
            manufacturer,
            manufacturer_did,
            manufacturer_key,
        )
        owner = _ifcx_file(
            "urn:owner:building:west-wing",
            imports=[{
                "uri": manufacturer_uri,
                "integrity": sha256_sri_integrity(manufacturer_bytes),
            }],
            schemas={"ifc::serial": {"value": {"dataType": "String"}}},
            data=[
                {
                    "path": "building/door-1",
                    "inherits": {"type": "types/door"},
                    "attributes": {"ifc::serial": "D-1001"},
                },
                {
                    "path": "building/door-2",
                    "inherits": {"type": "types/door"},
                    "attributes": {"ifc::name": "Fire-rated Model X"},
                },
            ],
        )
        owner = ensure_clip_component_deletion_schema(owner)

        async def fetcher(uri: str) -> bytes:
            self.assertEqual(uri, manufacturer_uri)
            return manufacturer_bytes

        async def did_resolver(did: str) -> dict:
            self.assertEqual(did, manufacturer_did)
            return manufacturer_document

        resolved = await resolve_ifc_layers(
            owner,
            fetcher=fetcher,
            did_resolver=did_resolver,
        )
        graph = flatten_ifc_layers(
            resolved.layers,
            authority_did="did:web:owner.example",
            dataset_id=owner.header.id,
        )
        door_address = IfcComponentAddress(
            authority_did="did:web:owner.example",
            dataset_id=owner.header.id,
            entity_path="building/door-1",
            component_schema_id="ifc::name",
        )
        overridden_address = door_address.model_copy(
            update={"entity_path": "building/door-2"}
        )

        self.assertEqual([layer.header.id for layer in resolved.layers], [
            owner.header.id,
            manufacturer.header.id,
        ])
        self.assertEqual(graph.resolve_component(door_address), "Model X Door")
        self.assertEqual(graph.resolve_component(overridden_address), "Fire-rated Model X")
        self.assertEqual(
            graph.resolve_component(door_address.model_copy(
                update={
                    "entity_path": "building/door-1",
                    "component_schema_id": "ifc::manufacturer",
                }
            )),
            "Northstar Doors",
        )
        owner_override = apply_ifc_component_change(
            owner,
            door_address,
            action="set",
            value="Owner fire-rated override",
            layers=resolved.layers,
        )
        overridden_layers = (owner_override, *resolved.layers[1:])
        overridden_graph = flatten_ifc_layers(
            overridden_layers,
            authority_did="did:web:owner.example",
            dataset_id=owner.header.id,
        )
        self.assertEqual(
            overridden_graph.resolve_component(door_address),
            "Owner fire-rated override",
        )

    async def test_nested_imports_are_loaded_before_their_importer(self) -> None:
        schema_uri = "https://manufacturer.example/ifcx/schemas/door.json"
        catalog_uri = "https://manufacturer.example/ifcx/catalog.json"
        manufacturer_did = "did:web:manufacturer.example"
        manufacturer_key = nacl.signing.SigningKey(bytes(range(32)))
        manufacturer_document = _did_document(manufacturer_did, manufacturer_key)
        schema_file = _ifcx_file(
            "urn:manufacturer:schema:door:v1",
            schemas={"ifc::name": {"value": {"dataType": "String"}}},
        )
        schema_bytes = _publication(schema_file, manufacturer_did, manufacturer_key)
        catalog_file = _ifcx_file(
            "urn:manufacturer:catalog:v1",
            imports=[{
                "uri": schema_uri,
                "integrity": sha256_sri_integrity(schema_bytes),
            }],
            data=[{
                "path": "types/door",
                "attributes": {"ifc::name": "Model X Door"},
            }],
        )
        catalog_bytes = _publication(catalog_file, manufacturer_did, manufacturer_key)
        owner_file = _ifcx_file(
            "urn:owner:building",
            imports=[{
                "uri": catalog_uri,
                "integrity": sha256_sri_integrity(catalog_bytes),
            }],
        )
        payloads = {catalog_uri: catalog_bytes, schema_uri: schema_bytes}

        async def fetcher(uri: str) -> bytes:
            return payloads[uri]

        async def did_resolver(did: str) -> dict:
            self.assertEqual(did, manufacturer_did)
            return manufacturer_document

        resolved = await resolve_ifc_layers(
            owner_file,
            fetcher=fetcher,
            did_resolver=did_resolver,
        )

        self.assertEqual([layer.header.id for layer in resolved.layers], [
            owner_file.header.id,
            catalog_file.header.id,
            schema_file.header.id,
        ])
        self.assertIn("ifc::name", resolved.federated_file.schemas)

    async def test_import_fails_closed_for_missing_or_altered_integrity(self) -> None:
        uri = "https://manufacturer.example/ifcx/catalog.json"
        manufacturer_did = "did:web:manufacturer.example"
        manufacturer_key = nacl.signing.SigningKey(bytes(range(32)))
        manufacturer_document = _did_document(manufacturer_did, manufacturer_key)
        imported = _ifcx_file("urn:manufacturer:catalog:v1")
        imported_bytes = _publication(imported, manufacturer_did, manufacturer_key)
        missing_pin = _ifcx_file("urn:owner:missing-pin", imports=[{"uri": uri}])
        altered_pin = _ifcx_file(
            "urn:owner:altered-pin",
            imports=[{"uri": uri, "integrity": sha256_sri_integrity(imported_bytes)}],
        )

        async def fetcher(_uri: str) -> bytes:
            return imported_bytes + b" "

        async def did_resolver(_did: str) -> dict:
            return manufacturer_document

        with self.assertRaisesRegex(ClipFederationError, "no integrity pin"):
            await resolve_ifc_layers(missing_pin, fetcher=fetcher)
        with self.assertRaisesRegex(ClipFederationError, "do not match"):
            await resolve_ifc_layers(
                altered_pin,
                fetcher=fetcher,
                did_resolver=did_resolver,
            )

    async def test_forged_publication_fails_even_with_matching_byte_pin(self) -> None:
        uri = "https://manufacturer.example/ifcx/catalog.json"
        publisher_did = "did:web:manufacturer.example"
        signing_key = nacl.signing.SigningKey(bytes(range(32)))
        did_document = _did_document(publisher_did, signing_key)
        publication = json.loads(_publication(
            _ifcx_file("urn:manufacturer:catalog:v1"),
            publisher_did,
            signing_key,
        ))
        publication["file"]["header"]["author"] = "impersonated publisher"
        forged_bytes = json.dumps(publication).encode("utf-8")
        owner = _ifcx_file(
            "urn:owner:building",
            imports=[{
                "uri": uri,
                "integrity": sha256_sri_integrity(forged_bytes),
            }],
        )

        async def fetcher(_uri: str) -> bytes:
            return forged_bytes

        async def did_resolver(_did: str) -> dict:
            return did_document

        with self.assertRaisesRegex(ClipFederationError, "publication proof"):
            await resolve_ifc_layers(
                owner,
                fetcher=fetcher,
                did_resolver=did_resolver,
            )


if __name__ == "__main__":
    unittest.main()