"""Seed and verify the six-node CLIP network and IFC graph demo."""

import argparse
import base64
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

import httpx

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.federation.clip_layers import sha256_sri_integrity
from node.app.imports.ifcx import (
    EVIDENCE_SCHEMA,
    EVENT_SCHEMA,
    SOURCE_SCHEMA,
    construction_schemas,
)


DATA = Path(__file__).resolve().parent / "data"
STATE_PATH = DATA / "clip-demo-state.json"
NODES = {
    "manufacturer": 8101,
    "supplier": 8102,
    "main_contractor": 8103,
    "owner": 8104,
    "inspector": 8105,
    "relay": 8106,
}
DATASET_ID = "urn:owner:north-wing:v1"
MANUFACTURER_DATASET_ID = "urn:manufacturer:door-catalog:v1"
ASSET_PATH = "building/door-1"
INSTALLATION_PATH = "events/installation-1001"
PUMP_INSTALLATION_PATH = "events/commissioning-1002"


def peer_did(role: str) -> str:
    port = NODES[role]
    return f"did:web:127.0.0.1%3A{port}"


def node_url(role: str) -> str:
    return f"http://127.0.0.1:{NODES[role]}"


def post(role: str, path: str, body: dict) -> dict:
    response = request(role, "POST", path, body)
    response.raise_for_status()
    return response.json()


def put(role: str, path: str, body: dict) -> dict:
    response = request(role, "PUT", path, body)
    response.raise_for_status()
    return response.json()


def request(role: str, method: str, path: str, body: dict | None = None) -> httpx.Response:
    return httpx.request(
        method,
        f"{node_url(role)}{path}",
        json=body,
        timeout=30,
    )


def create_record(
    role: str,
    kind: str,
    name: str,
    *,
    ifc_class: str,
    sources: list[dict] | None = None,
    project_id: str | None = None,
    data: dict | None = None,
) -> dict:
    body = {
        "kind": kind,
        "name": name,
        "ifcClass": ifc_class,
        "sources": sources or [],
        "data": data or {},
    }
    if project_id:
        body["projectId"] = project_id
    return post(role, "/clip/v1/supply-chain/records", body)


def source_record(record: dict, **fields) -> dict:
    return {
        "authorityDid": record["authorityDid"],
        "recordId": record["id"],
        "revision": record["revision"],
        **fields,
    }


def publish_record(role: str, record: dict) -> dict:
    return post(
        role,
        f"/clip/v1/supply-chain/records/{record['id']}/publish",
        {"expectedRevision": record["revision"]},
    )


def create_project(role: str, name: str) -> dict:
    return post(role, "/clip/v1/projects", {"name": name, "visibility": "private"})


def create_entity(
    project_id: str,
    template: str,
    name: str,
    *,
    parent_path: str | None = None,
    type_path: str | None = None,
) -> dict:
    body = {"template": template, "name": name}
    if parent_path:
        body["parentPath"] = parent_path
    if type_path:
        body["typePath"] = type_path
    return post("owner", f"/ifc/v1/projects/{quote(project_id, safe='')}/entities", body)


def submit_and_accept(
    sender: str,
    recipient: str,
    project_id: str,
    record: dict,
    *,
    document_ids: list[str] | None = None,
) -> dict:
    prefix = "/clip/v1/supply-chain"
    draft = post(sender, prefix + "/submissions", {
        "recipientDid": peer_did(recipient),
        "projectId": project_id,
        "recordIds": [record["id"]],
        "documentIds": document_ids or [],
        "idempotencyKey": f"demo-{uuid4()}",
    })
    issued = post(sender, f"{prefix}/submissions/{draft['id']}/issue", {
        "expectedRevision": draft["revision"],
        "idempotencyKey": f"demo-issue-{uuid4()}",
    })
    if issued["deliveryStatus"] != "delivered":
        raise RuntimeError(f"Submission {draft['id']} was not delivered")
    received = request(recipient, "GET", f"{prefix}/submissions/{draft['id']}")
    received.raise_for_status()
    decision = post(recipient, f"{prefix}/submissions/{draft['id']}/decision", {
        "decision": "accept",
        "reason": "Reviewed in the CLIP network demonstration",
        "expectedRevision": received.json()["revision"],
        "idempotencyKey": f"demo-decision-{uuid4()}",
    })
    if not decision["acceptedRecords"]:
        raise RuntimeError(f"Submission {draft['id']} produced no accepted records")
    return {
        "submissionId": draft["id"],
        "issue": issued["issue"],
        "acceptedRecord": decision["acceptedRecords"][0],
    }


def wait_for_nodes(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = True
        for role in NODES:
            try:
                document = httpx.get(
                    f"{node_url(role)}/.well-known/did.json",
                    timeout=2,
                ).json()
                if document.get("id") != peer_did(role):
                    ready = False
                    break
            except (httpx.RequestError, ValueError):
                ready = False
                break
        if ready:
            return
        time.sleep(0.25)
    raise TimeoutError("Six DID documents did not become available")


def wait_for_gossip(timeout: float = 45.0) -> list[dict]:
    expected = {peer_did(role) for role in NODES if role != "owner"}
    deadline = time.monotonic() + timeout
    last_peers: list[dict] = []
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{node_url('owner')}/clip/v1/network/gossip/peers",
            timeout=3,
        )
        response.raise_for_status()
        last_peers = response.json()
        known = {peer["did"] for peer in last_peers}
        manufacturer = next(
            (peer for peer in last_peers if peer["did"] == peer_did("manufacturer")),
            None,
        )
        if expected.issubset(known) and manufacturer and manufacturer["status"] == "alive":
            return last_peers
        time.sleep(0.25)
    raise TimeoutError(f"DID gossip has not converged; owner sees: {last_peers}")


def _utc_now() -> str:
    # Servers verify against Pydantic's re-serialization, which emits "Z" rather than "+00:00".
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sign_transaction(transaction: dict, role: str, purpose: str) -> dict:
    key_manager = NodeKeyManager.load_or_create(
        str(DATA / f"{role}.key")
    )
    return add_data_integrity_proof(
        transaction,
        key_manager.private_key_bytes,
        verification_method=f"{peer_did(role)}#authority-key",
        proof_purpose=purpose,
        created=datetime.now(timezone.utc),
    )


def _sign_service_message(role: str, kind: str, audience_did: str, payload: dict) -> dict:
    now = datetime.now(timezone.utc)
    return add_data_integrity_proof(
        {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "messageId": str(uuid4()),
            "actorDid": peer_did(role),
            "audienceDid": audience_did,
            "kind": kind,
            "payload": payload,
            "created": now.isoformat().replace("+00:00", "Z"),
        },
        NodeKeyManager.load_or_create(str(DATA / f"{role}.key")).private_key_bytes,
        verification_method=f"{peer_did(role)}#authority-key",
        proof_purpose="authentication",
        created=now,
    )


def seed() -> None:
    manufacturer_file = {
        "header": {
            "id": MANUFACTURER_DATASET_ID,
            "ifcxVersion": "ifcx_alpha",
            "dataVersion": "1.0.0",
            "author": "manufacturer",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "imports": [],
        "schemas": {
            "ifc::name": {"value": {"dataType": "String"}},
            "ifc::manufacturer": {"value": {"dataType": "String"}},
            "ifc::model": {"value": {"dataType": "String"}},
            "ifc::rated-power-kw": {"value": {"dataType": "Real"}},
        },
        "data": [
            {"path": "types", "children": {
                "door": "types/door",
                "pump": "types/pump",
            }},
            {
                "path": "types/door",
                "attributes": {
                    "ifc::name": "Northstar Door Model X",
                    "ifc::manufacturer": "Northstar Construction Products",
                    "ifc::model": "NDX-90",
                },
            },
            {
                "path": "types/pump",
                "attributes": {
                    "ifc::name": "Northstar Inline Pump P-100",
                    "ifc::manufacturer": "Northstar Construction Products",
                    "ifc::model": "P-100",
                    "ifc::rated-power-kw": 5.5,
                },
            },
        ],
    }
    post("manufacturer", "/ifc/v1/datasets", {
        "file": manufacturer_file,
        "trustedProposers": [],
    })
    publication_path = (
        f"/ifc/v1/datasets/{quote(MANUFACTURER_DATASET_ID, safe='')}/publication"
    )
    publication_response = httpx.get(f"{node_url('manufacturer')}{publication_path}", timeout=10)
    publication_response.raise_for_status()
    publication_bytes = publication_response.content

    owner_file = {
        "header": {
            "id": DATASET_ID,
            "ifcxVersion": "ifcx_alpha",
            "dataVersion": "1.0.0",
            "author": "owner",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        "imports": [{
            "uri": f"{node_url('manufacturer')}{publication_path}",
            "integrity": sha256_sri_integrity(publication_bytes),
        }],
        "schemas": {
            **construction_schemas(),
            "ifc::serial": {"value": {"dataType": "String"}},
            "clip::installation-reference": {"value": {"dataType": "Reference"}},
            "clip::installation-event": {
                "value": {
                    "dataType": "Object",
                    "objectRestrictions": {"values": {
                        "installerDid": {"dataType": "String"},
                        "installedAt": {"dataType": "DateTime"},
                        "status": {
                            "dataType": "Enum",
                            "enumRestrictions": {"options": ["complete", "failed"]},
                        },
                    }},
                },
            },
        },
        "data": [
            {"path": "site", "attributes": {
                "ifc::name": "North Wing Campus",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SITE-NORTH", "class": "IfcSite", "properties": {}},
            }, "children": {"building": "building"}},
            {"path": "building", "attributes": {
                "ifc::name": "North Wing",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "BLDG-NORTH", "class": "IfcBuilding", "properties": {}},
            }, "children": {"storey-1": "building/storey-1"}},
            {"path": "building/storey-1", "attributes": {
                "ifc::name": "Ground Floor",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "STOREY-01", "class": "IfcBuildingStorey", "properties": {}},
            }, "children": {
                "lobby": "building/storey-1/lobby",
                "plant-room": "building/storey-1/plant-room",
            }},
            {"path": "building/storey-1/lobby", "attributes": {
                "ifc::name": "Main Lobby",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SPACE-LOBBY", "class": "IfcSpace", "properties": {}},
            }, "children": {"door-1": ASSET_PATH}},
            {"path": "building/storey-1/plant-room", "attributes": {
                "ifc::name": "Plant Room",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SPACE-PLANT", "class": "IfcSpace", "properties": {}},
            }, "children": {"pump-1": "building/pump-1"}},
            {
                "path": ASSET_PATH,
                "inherits": {"type": "types/door"},
                "attributes": {
                    "ifc::serial": "DOOR-1001",
                    "clip::installation-reference": INSTALLATION_PATH,
                    SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "DOOR-1001", "class": "IfcDoor", "properties": {}},
                },
            },
            {
                "path": "building/pump-1",
                "inherits": {"type": "types/pump"},
                "attributes": {
                    "ifc::serial": "PUMP-1002",
                    "clip::installation-reference": PUMP_INSTALLATION_PATH,
                    SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "PUMP-1002", "class": "IfcPump", "properties": {}},
                },
            },
            {
                "path": "events",
                "children": {
                    "installation-1001": INSTALLATION_PATH,
                    "commissioning-1002": PUMP_INSTALLATION_PATH,
                },
            },
            {"path": INSTALLATION_PATH},
            {"path": PUMP_INSTALLATION_PATH},
        ],
    }
    owner_registration = post("owner", "/ifc/v1/datasets", {
        "file": owner_file,
        "trustedProposers": [peer_did("main_contractor"), peer_did("inspector")],
    })

    proposal = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("main_contractor"),
        "target": {
            "authorityDid": peer_did("owner"),
            "datasetId": DATASET_ID,
            "entityPath": INSTALLATION_PATH,
            "componentSchemaId": "clip::installation-event",
        },
        "change": {"action": "set", "value": {
            "installerDid": peer_did("main_contractor"),
            "installedAt": "2026-09-30T10:00:00Z",
            "status": "complete",
        }},
        "expectedSequence": 0,
        "schemaDigest": owner_registration["schemaDigest"],
        "created": _utc_now(),
    }
    signed_proposal = _sign_transaction(proposal, "main_contractor", "assertionMethod")
    proposal_result = post("owner", "/ifc/v1/proposals", signed_proposal)

    decision = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("owner"),
        "decision": "accept",
        "proposalId": proposal_result["proposalId"],
        "proposalDigest": proposal_result["proposalDigest"],
        "expectedSequence": 0,
        "created": _utc_now(),
    }
    signed_decision = _sign_transaction(decision, "owner", "capabilityInvocation")
    receipt = post("owner", "/ifc/v1/decisions", signed_decision)
    if not receipt["accepted"] or receipt["sequence"] != 1:
        raise RuntimeError("Owner did not commit the contractor installation agreement")

    pump_proposal = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("inspector"),
        "target": {
            "authorityDid": peer_did("owner"),
            "datasetId": DATASET_ID,
            "entityPath": PUMP_INSTALLATION_PATH,
            "componentSchemaId": EVENT_SCHEMA,
        },
        "change": {"action": "set", "value": {
            "kind": "installation",
            "actorDid": peer_did("inspector"),
            "occurredAt": "2026-10-01T12:30:00Z",
            "subject": "building/pump-1",
            "status": "complete",
        }},
        "expectedSequence": receipt["sequence"],
        "schemaDigest": owner_registration["schemaDigest"],
        "created": _utc_now(),
    }
    pump_proposal_result = post(
        "owner",
        "/ifc/v1/proposals",
        _sign_transaction(pump_proposal, "inspector", "assertionMethod"),
    )
    pump_decision = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("owner"),
        "decision": "accept",
        "proposalId": pump_proposal_result["proposalId"],
        "proposalDigest": pump_proposal_result["proposalDigest"],
        "expectedSequence": receipt["sequence"],
        "created": _utc_now(),
    }
    pump_receipt = post(
        "owner",
        "/ifc/v1/decisions",
        _sign_transaction(pump_decision, "owner", "capabilityInvocation"),
    )
    if not pump_receipt["accepted"] or pump_receipt["sequence"] != 2:
        raise RuntimeError("Owner did not commit the inspector commissioning agreement")

    state = {
        "ownerDid": peer_did("owner"),
        "datasetId": DATASET_ID,
        "assetPath": ASSET_PATH,
        "installationPath": INSTALLATION_PATH,
        "pumpPath": "building/pump-1",
        "pumpInstallationPath": PUMP_INSTALLATION_PATH,
        "manufacturerDid": peer_did("manufacturer"),
        "productName": "Northstar Door Model X",
        "installationStatus": "complete",
        "acceptedSequence": pump_receipt["sequence"],
        "schemaDigest": owner_registration["schemaDigest"],
    }
    seed_showcase(state)
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    verify()
    print("Seeded IFC assets, a multi-authority supply chain, project access, evidence, replication, and DID gossip.")


def seed_showcase(state: dict) -> None:
    evidence = post("owner", "/clip/v1/evidence/upload", {
        "target": {
            "authorityDid": peer_did("owner"),
            "datasetId": DATASET_ID,
            "entityPath": state["pumpPath"],
            "componentSchemaId": EVIDENCE_SCHEMA,
        },
        "name": "North-Wing-pump-inspection.txt",
        "mediaType": "text/plain",
        "content": base64.b64encode(
            b"Commissioning checklist: rotation, vibration, flow and pressure checked.\n"
            * 40
        ).decode("ascii"),
        "recipients": [peer_did("owner"), peer_did("relay")],
        "retentionSeconds": 86400,
        "chunkSize": 1024,
    })
    evidence_id = evidence["manifest"]["evidenceId"]
    evidence_proposal = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("inspector"),
        "target": {
            "authorityDid": peer_did("owner"),
            "datasetId": DATASET_ID,
            "entityPath": state["pumpPath"],
            "componentSchemaId": EVIDENCE_SCHEMA,
        },
        "change": {"action": "set", "value": evidence["evidenceReference"]},
        "expectedSequence": state["acceptedSequence"],
        "schemaDigest": state["schemaDigest"],
        "created": _utc_now(),
    }
    evidence_proposal_result = post(
        "owner",
        "/ifc/v1/proposals",
        _sign_transaction(evidence_proposal, "inspector", "assertionMethod"),
    )
    evidence_decision = {
        "@context": [
            "https://w3id.org/security/data-integrity/v2",
            {"@vocab": "urn:clip:protocol:"},
        ],
        "transactionId": str(uuid4()),
        "actorDid": peer_did("owner"),
        "decision": "accept",
        "proposalId": evidence_proposal_result["proposalId"],
        "proposalDigest": evidence_proposal_result["proposalDigest"],
        "expectedSequence": state["acceptedSequence"],
        "created": _utc_now(),
    }
    evidence_receipt = post(
        "owner",
        "/ifc/v1/decisions",
        _sign_transaction(evidence_decision, "owner", "capabilityInvocation"),
    )
    if not evidence_receipt["accepted"]:
        raise RuntimeError("Owner did not accept the signed evidence reference")
    state["acceptedSequence"] = evidence_receipt["sequence"]

    owner_project = create_project("owner", "North Wing Renewal")
    project_id = owner_project["projectId"]

    site = create_entity(project_id, "site", "North Wing Campus", parent_path="project")
    building = create_entity(project_id, "building", "North Wing", parent_path=site["entityPath"])
    storey = create_entity(project_id, "storey", "Ground Floor", parent_path=building["entityPath"])
    lobby = create_entity(project_id, "space", "Main Lobby", parent_path=storey["entityPath"])
    plant_room = create_entity(project_id, "space", "Plant Room", parent_path=storey["entityPath"])
    zone = create_entity(project_id, "zone", "Public Areas", parent_path=site["entityPath"])
    group = create_entity(project_id, "group", "Life Safety Assets", parent_path="project")
    door_type = create_entity(project_id, "door-type", "Northstar Door Model X", parent_path="project")
    pump_type = create_entity(project_id, "pump-type", "Northstar Inline Pump P-100", parent_path="project")
    door = create_entity(
        project_id,
        "door",
        "Lobby Fire Door",
        parent_path=lobby["entityPath"],
        type_path=door_type["entityPath"],
    )
    assembly = create_entity(project_id, "assembly", "Plant Room Pump Assembly", parent_path=storey["entityPath"])
    pump = create_entity(
        project_id,
        "pump",
        "Primary Circulation Pump",
        parent_path=assembly["entityPath"],
        type_path=pump_type["entityPath"],
    )

    motor = create_record(
        "manufacturer",
        "product",
        "Northstar Motor M-5",
        ifc_class="IfcPumpType",
        data={"model": "M-5", "ratedPowerKw": 5.5, "voltage": "400 V"},
    )
    motor_revision = publish_record("manufacturer", motor)
    pump_product = create_record(
        "manufacturer",
        "product",
        "Northstar Inline Pump P-100",
        ifc_class="IfcPumpType",
        sources=[source_record(motor_revision, quantity=1, unit="each")],
        data={"model": "P-100", "flowRateLpm": 420, "headMetres": 28},
    )
    datasheet = post("manufacturer", f"/clip/v1/supply-chain/records/{pump_product['id']}/documents", {
        "name": "Northstar-P100-datasheet.txt",
        "mediaType": "text/plain",
        "content": base64.b64encode(
            b"Northstar P-100 | 420 L/min | 28 m head | 5.5 kW motor\n"
        ).decode("ascii"),
        "visibility": "public",
        "expectedRevision": pump_product["revision"],
    })
    pump_product["revision"] = datasheet["recordRevision"]
    pump_revision = publish_record("manufacturer", pump_product)

    post("supplier", "/clip/v1/supply-chain/catalogue/discover", {
        "authorityDid": peer_did("manufacturer"),
    })
    supplier_offer = create_record(
        "supplier",
        "offering",
        "Northstar P-100 Supply Offer",
        ifc_class="IfcPumpType",
        sources=[source_record(pump_revision)],
        data={"sku": "NS-P100", "warranty": "5 years", "serviceRegion": "North District"},
    )
    supplier_offer_revision = publish_record("supplier", supplier_offer)

    post("main_contractor", "/clip/v1/supply-chain/catalogue/discover", {
        "authorityDid": peer_did("supplier"),
    })
    contractor_offer = create_record(
        "main_contractor",
        "offering",
        "North Wing Mechanical Package",
        ifc_class="IfcPumpType",
        sources=[source_record(supplier_offer_revision)],
        data={"sku": "NWR-MECH-P100", "coordinationPackage": "MEP-04"},
    )
    contractor_offer_revision = publish_record("main_contractor", contractor_offer)

    put(
        "owner",
        f"/clip/v1/supply-chain/projects/{quote(project_id, safe='')}/senders",
        {"expectedRevision": 0, "senders": [peer_did("main_contractor")]},
    )
    contractor_connection = post("main_contractor", "/clip/v1/supply-chain/projects/connect", {
        "authorityDid": peer_did("owner"),
        "projectId": project_id,
    })
    if contractor_connection["projectId"] != project_id or contractor_connection["local"]:
        raise RuntimeError("Main contractor did not connect to the owner project")

    delivery = create_record(
        "main_contractor",
        "supply",
        "North Wing Pump Delivery",
        ifc_class="IfcPumpType",
        sources=[source_record(contractor_offer_revision)],
        project_id=project_id,
        data={
            "quantity": 2,
            "unit": "each",
            "batch": "NW-2026-10",
            "serials": ["P100-NW-001", "P100-NW-002"],
        },
    )
    delivery_document = post(
        "main_contractor",
        f"/clip/v1/supply-chain/records/{delivery['id']}/documents",
        {
            "name": "North-Wing-delivery-note.txt",
            "mediaType": "text/plain",
            "content": base64.b64encode(
                b"Delivery to North Wing plant room; two serialized pumps.\n"
            ).decode("ascii"),
            "visibility": "private",
            "expectedRevision": delivery["revision"],
        },
    )
    delivery["revision"] = delivery_document["recordRevision"]
    received_delivery = submit_and_accept(
        "main_contractor",
        "owner",
        project_id,
        delivery,
        document_ids=[delivery_document["id"]],
    )
    accepted_supply = received_delivery["acceptedRecord"]

    installation = create_record(
        "owner",
        "installation",
        "Primary Pump Installation",
        ifc_class="IfcPump",
        sources=[source_record(
            accepted_supply,
            quantity=1,
            unit="each",
            serials=["P100-NW-001"],
        )],
        project_id=project_id,
        data={
            "location": "North Wing / Ground Floor / Plant Room",
            "status": "commissioned",
            "installedAt": "2026-10-01T12:30:00Z",
            "installer": "North Wing Mechanical Team",
        },
    )

    inspector_project = create_project("inspector", "North Wing Inspection Review")
    inspector_project_id = inspector_project["projectId"]
    put(
        "inspector",
        f"/clip/v1/supply-chain/projects/{quote(inspector_project_id, safe='')}/senders",
        {"expectedRevision": 0, "senders": [peer_did("owner")]},
    )
    owner_connection = post("owner", "/clip/v1/supply-chain/projects/connect", {
        "authorityDid": peer_did("inspector"),
        "projectId": inspector_project_id,
    })
    if owner_connection["projectId"] != inspector_project_id or owner_connection["local"]:
        raise RuntimeError("Owner did not connect to the inspector project")
    inspection_handover = submit_and_accept(
        "owner",
        "inspector",
        inspector_project_id,
        installation,
    )

    invitation = post(
        "owner",
        f"/clip/v1/projects/{quote(project_id, safe='')}/invites",
        {"role": "viewer", "expiresHours": 168},
    )
    join_ack = post("relay", "/clip/v1/projects/invites/redeem", {"code": invitation["code"]})
    join_request_id = join_ack["payload"]["joinRequestId"]
    join_requests = request(
        "owner",
        "GET",
        f"/clip/v1/projects/{quote(project_id, safe='')}/join-requests",
    )
    join_requests.raise_for_status()
    post("owner", f"/clip/v1/projects/join-requests/{join_request_id}/decision", {
        "decision": "accept",
        "expectedRevision": join_requests.json()["revision"],
    })

    post("relay", "/clip/v1/node/storage/opt-in", {"opt_in": True})
    placement = post("owner", f"/clip/v1/evidence/{evidence_id}/replicate", {
        "peerDid": peer_did("relay"),
    })
    if len(placement["receipts"]) != evidence["manifest"]["manifest"]["fragmentCount"]:
        raise RuntimeError("Relay did not acknowledge every encrypted evidence fragment")

    replication_ack = post("owner", "/clip/v1/replication/push", {
        "peerDid": peer_did("relay"),
        "datasetId": DATASET_ID,
    })
    state.update({
        "showcaseProjectId": project_id,
        "showcaseEntities": {
            "site": site["entityPath"],
            "building": building["entityPath"],
            "storey": storey["entityPath"],
            "lobby": lobby["entityPath"],
            "plantRoom": plant_room["entityPath"],
            "zone": zone["entityPath"],
            "group": group["entityPath"],
            "doorType": door_type["entityPath"],
            "pumpType": pump_type["entityPath"],
            "door": door["entityPath"],
            "assembly": assembly["entityPath"],
            "pump": pump["entityPath"],
        },
        "manufacturerPumpId": pump_revision["id"],
        "supplierOfferId": supplier_offer_revision["id"],
        "contractorOfferId": contractor_offer_revision["id"],
        "deliveryRecordId": delivery["id"],
        "acceptedSupplyId": accepted_supply["id"],
        "installationRecordId": installation["id"],
        "inspectionProjectId": inspector_project_id,
        "inspectionSubmissionId": inspection_handover["submissionId"],
        "inspectedAssetId": inspection_handover["acceptedRecord"]["id"],
        "evidenceId": evidence_id,
        "evidenceFragmentCount": len(placement["receipts"]),
        "replicationDigest": replication_ack["payload"]["digest"],
        "relayDid": peer_did("relay"),
        "relayJoinRequestId": join_request_id,
    })


def verify() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    product = get_component(
        "owner",
        state["datasetId"],
        state["assetPath"],
        "ifc::name",
    )
    manufacturer = get_component(
        "owner",
        state["datasetId"],
        state["assetPath"],
        "ifc::manufacturer",
    )
    installation = get_component(
        "owner",
        state["datasetId"],
        state["installationPath"],
        "clip::installation-event",
    )
    if product != state["productName"]:
        raise AssertionError(f"Unexpected inherited product type: {product}")
    if installation["status"] != state["installationStatus"]:
        raise AssertionError(f"Unexpected accepted installation state: {installation}")

    pump_name = get_component("owner", state["datasetId"], state["pumpPath"], "ifc::name")
    pump_rating = get_component("owner", state["datasetId"], state["pumpPath"], "ifc::rated-power-kw")
    commissioning = get_component(
        "owner",
        state["datasetId"],
        state["pumpInstallationPath"],
        EVENT_SCHEMA,
    )
    evidence_reference = get_component(
        "owner",
        state["datasetId"],
        state["pumpPath"],
        EVIDENCE_SCHEMA,
    )
    if pump_name != "Northstar Inline Pump P-100" or pump_rating != 5.5:
        raise AssertionError(f"Unexpected inherited pump type data: {pump_name!r}, {pump_rating!r}")
    if commissioning["status"] != "complete":
        raise AssertionError(f"Unexpected accepted pump commissioning state: {commissioning}")
    if evidence_reference["evidenceId"] != state["evidenceId"]:
        raise AssertionError(f"Unexpected accepted evidence reference: {evidence_reference}")

    graph_response = request(
        "owner",
        "POST",
        "/clip/v1/projects/read",
        _sign_service_message(
            "owner",
            "projectRead",
            peer_did("owner"),
            {"projectId": state["showcaseProjectId"]},
        ),
    )
    graph_response.raise_for_status()
    graph = graph_response.json()["payload"]["graph"]
    missing_entities = set(state["showcaseEntities"].values()) - set(graph["entities"])
    if missing_entities:
        raise AssertionError(f"Showcase IFC graph is missing entities: {sorted(missing_entities)}")

    accepted_supply = request(
        "owner",
        "GET",
        f"/clip/v1/supply-chain/records/{state['acceptedSupplyId']}",
    )
    accepted_supply.raise_for_status()
    accepted_asset = request(
        "inspector",
        "GET",
        f"/clip/v1/supply-chain/records/{state['inspectedAssetId']}",
    )
    accepted_asset.raise_for_status()
    if accepted_supply.json()["kind"] != "supply" or accepted_asset.json()["kind"] != "asset":
        raise AssertionError("Supply-chain handover did not create the expected recipient-owned records")

    project_response = request("owner", "GET", "/clip/v1/projects")
    project_response.raise_for_status()
    project = next(
        item for item in project_response.json()["items"]
        if item["projectId"] == state["showcaseProjectId"]
    )
    if project["members"].get(state["relayDid"]) != "viewer":
        raise AssertionError(f"Relay invite was not accepted as a project Viewer: {project['members']}")

    evidence_response = request("owner", "GET", f"/clip/v1/evidence/{state['evidenceId']}")
    evidence_response.raise_for_status()
    relay_storage = request("relay", "GET", "/clip/v1/node/storage")
    relay_storage.raise_for_status()
    if relay_storage.json()["fragment_count"] < state["evidenceFragmentCount"]:
        raise AssertionError(f"Relay does not hold all seeded evidence fragments: {relay_storage.json()}")
    replication_response = request("owner", "GET", "/clip/v1/replication/status")
    replication_response.raise_for_status()
    if not any(
        ack.get("payload", {}).get("digest") == state["replicationDigest"]
        for ack in replication_response.json()["acknowledgements"]
    ):
        raise AssertionError("Owner does not have a matching signed replication acknowledgement")

    peers = wait_for_gossip()
    print(
        f"Verified IFC assets={product!r}/{pump_name!r}, "
        f"commissioning={commissioning['status']}, supply={accepted_supply.json()['kind']}, "
        f"inspection={accepted_asset.json()['kind']}, evidence fragments={state['evidenceFragmentCount']}, "
        f"owner-visible peers={len(peers)}."
    )


def get_component(role: str, dataset_id: str, entity_path: str, schema_id: str):
    response = httpx.get(
        f"{node_url(role)}/ifc/v1/datasets/{quote(dataset_id, safe='')}/components",
        params={"entity_path": entity_path, "component_schema_id": schema_id},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["value"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("seed", "verify"))
    arguments = parser.parse_args()
    if arguments.command == "seed":
        wait_for_nodes()
        seed()
    else:
        wait_for_nodes()
        verify()
if __name__ == "__main__":
    main()