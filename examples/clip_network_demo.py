"""Seed and verify the eight-authority CLIP portfolio and IFC graph demo."""

import argparse
import base64
import json
import sys
import time
from contextvars import ContextVar
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
from node.app.core.ifc_product_resolver import IDENTITY_SCHEMA, product_path
from node.app.core.ifc_protocol import IfcGraphProposalTransaction
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
    "component_manufacturer": 8106,
    "supplier_2": 8107,
    "supplier_3": 8108,
}
FACILITIES = (
    "South Hospital", "East Logistics Centre", "West Research Labs",
    "Central Library", "Riverside Leisure Centre", "Hilltop School",
)
SUPPLIERS = ("supplier", "supplier_2", "supplier_3")
DATASET_ID = "urn:owner:north-wing:v1"
MANUFACTURER_DATASET_ID = "urn:manufacturer:door-catalog:v1"
ASSET_PATH = "building/door-1"
INSTALLATION_PATH = "events/installation-1001"
PUMP_INSTALLATION_PATH = "events/commissioning-1002"

class SeedProgress:
    def __init__(self, total: int, stream=None):
        self.total = total
        self.completed = 0
        self.label = "Starting"
        self.stream = stream if stream is not None else sys.stderr
        self.interactive = self.stream.isatty()
        self.started = time.monotonic()
        self.last_render = float("-inf")
        self.line_width = 0

    def update(self, label: str, *, advance: bool = False, force: bool = False) -> None:
        self.label = label
        if advance:
            self.completed += 1
        self.render(force=force)

    def render(self, *, force: bool = False, outcome: str = "") -> None:
        now = time.monotonic()
        if not force and now - self.last_render < (0.2 if self.interactive else 10):
            return
        self.last_render = now
        percent = min(99, int(self.completed * 100 / self.total))
        if outcome == "Complete":
            percent = 100
        filled = percent * 24 // 100
        elapsed = int(now - self.started)
        line = (f"[{'#' * filled}{'-' * (24 - filled)}] {percent:3}% "
                f"{self.completed}/{self.total} steps | {elapsed // 60:02}:{elapsed % 60:02} | "
                f"{outcome + ': ' if outcome else ''}{self.label}")
        if self.interactive:
            self.stream.write("\r" + line.ljust(self.line_width))
            self.line_width = len(line)
            if outcome:
                self.stream.write("\n")
        else:
            self.stream.write(line + "\n")
        self.stream.flush()


_SEED_PROGRESS: ContextVar[SeedProgress | None] = ContextVar("seed_progress", default=None)


def seed_progress(label: str, *, advance: bool = False, force: bool = False) -> None:
    progress = _SEED_PROGRESS.get()
    if progress is not None:
        progress.update(label, advance=advance, force=force)


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
    progress = _SEED_PROGRESS.get()
    if progress is not None:
        progress.render()
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
    issued = issue_submission(sender, recipient, project_id, record, document_ids=document_ids)
    prefix = "/clip/v1/supply-chain"
    submission_id = issued["id"]
    received = request(recipient, "GET", f"{prefix}/submissions/{submission_id}")
    received.raise_for_status()
    decision = post(recipient, f"{prefix}/submissions/{submission_id}/decision", {
        "decision": "accept",
        "reason": "Reviewed in the CLIP network demonstration",
        "expectedRevision": received.json()["revision"],
        "idempotencyKey": f"demo-decision-{uuid4()}",
    })
    if not decision["acceptedRecords"]:
        raise RuntimeError(f"Submission {submission_id} produced no accepted records")
    return {
        "submissionId": submission_id,
        "issue": issued["issue"],
        "acceptedRecord": decision["acceptedRecords"][0],
    }


def issue_submission(
    sender: str, recipient: str, project_id: str, record: dict,
    *, document_ids: list[str] | None = None, supersedes: str | None = None,
) -> dict:
    prefix = "/clip/v1/supply-chain"
    draft = post(sender, prefix + "/submissions", {
        "recipientDid": peer_did(recipient),
        "projectId": project_id,
        "recordIds": [record["id"]],
        "documentIds": document_ids or [],
        **({"supersedes": supersedes} if supersedes else {}),
        "idempotencyKey": f"demo-{uuid4()}",
    })
    issued = post(sender, f"{prefix}/submissions/{draft['id']}/issue", {
        "expectedRevision": draft["revision"],
        "idempotencyKey": f"demo-issue-{uuid4()}",
    })
    if issued["deliveryStatus"] != "delivered":
        raise RuntimeError(f"Submission {draft['id']} was not delivered")
    return issued


def wait_for_nodes(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        ready = True
        for role in NODES:
            try:
                seed_progress(f"Waiting for authority API: {role} / port {NODES[role]}")
                response = httpx.get(
                    f"{node_url(role)}/.well-known/did.json",
                    timeout=2,
                )
                response.raise_for_status()
                document = response.json()
                if document.get("id") != peer_did(role):
                    ready = False
                    break
            except (httpx.HTTPError, ValueError):
                ready = False
                break
        if ready:
            return
        time.sleep(0.25)
    raise TimeoutError(f"{len(NODES)} DID documents did not become available")


def wait_for_gossip(timeout: float = 120.0) -> list[dict]:
    expected = {peer_did(role) for role in NODES if role != "owner"}
    deadline = time.monotonic() + timeout
    last_peers: list[dict] = []
    seed_progress("Verifying background peer discovery", force=True)
    while time.monotonic() < deadline:
        try:
            response = httpx.get(
                f"{node_url('owner')}/clip/v1/network/gossip/peers",
                timeout=3,
            )
        except httpx.RequestError as error:
            print(f"Waiting for initial gossip response ({type(error).__name__}): {error}", flush=True)
            time.sleep(0.25)
            continue
        response.raise_for_status()
        last_peers = response.json()
        known = {peer["did"] for peer in last_peers}
        manufacturer = next(
            (peer for peer in last_peers if peer["did"] == peer_did("manufacturer")),
            None,
        )
        if expected.issubset(known) and manufacturer and manufacturer["status"] == "alive":
            return last_peers
        missing = sorted(expected - known)
        seed_progress(
            f"Verifying gossip: {len(expected & known)}/{len(expected)} peers discovered; "
            f"manufacturer {manufacturer['status'] if manufacturer else 'not discovered'}"
            + (f"; missing {', '.join(missing)}" if missing else ""),
        )
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


def seed_products() -> dict[str, dict]:
    door = create_record(
        "manufacturer", "product", "Northstar Door Model X", ifc_class="IfcDoorType",
        data={"manufacturer": "Northstar", "model": "NDX-90", "fireRatingMinutes": 90},
    )
    door_revision = publish_record("manufacturer", door)
    seed_progress("Published Northstar door", advance=True)
    motor = create_record(
        "component_manufacturer", "product", "Aster Motor M-5", ifc_class="IfcElectricMotorType",
        data={"manufacturer": "Aster", "model": "M-5", "ratedPowerKw": 5.5, "voltage": "400 V"},
    )
    motor_revision = publish_record("component_manufacturer", motor)
    seed_progress("Published Aster motor", advance=True)
    post("manufacturer", "/clip/v1/supply-chain/catalogue/discover", {
        "authorityDid": peer_did("component_manufacturer"),
    })
    pump = create_record(
        "manufacturer", "product", "Northstar Inline Pump P-100", ifc_class="IfcPumpType",
        sources=[source_record(motor_revision, quantity=1, unit="each")],
        data={"manufacturer": "Northstar", "model": "P-100", "flowRateLpm": 420, "headMetres": 28},
    )
    datasheet = post("manufacturer", f"/clip/v1/supply-chain/records/{pump['id']}/documents", {
        "name": "Northstar-P100-datasheet.txt",
        "mediaType": "text/plain",
        "content": base64.b64encode(
            b"Northstar P-100 | 420 L/min | 28 m head | 5.5 kW motor\n"
        ).decode("ascii"),
        "visibility": "public",
        "expectedRevision": pump["revision"],
    })
    pump["revision"] = datasheet["recordRevision"]
    products = {"door": door_revision, "pump": publish_record("manufacturer", pump), "motor": motor_revision}
    seed_progress("Published Northstar pump", advance=True)
    for name, role, label, ifc_class, data, component in (
        ("sensor", "component_manufacturer", "Aster Temperature Sensor T-20", "IfcSensorType",
         {"model": "T-20", "accuracyCelsius": 0.2}, None),
        ("controller", "component_manufacturer", "Aster Controls C-10", "IfcControllerType",
         {"model": "C-10", "protocol": "BACnet"}, "sensor"),
        ("valve", "manufacturer", "Northstar Isolation Valve V-50", "IfcValveType",
         {"model": "V-50", "diameterMm": 50}, None),
        ("fan", "manufacturer", "Northstar Ventilation Fan F-200", "IfcFanType",
         {"model": "F-200", "airflowM3h": 2400}, "motor"),
        ("filter", "manufacturer", "Northstar Air Filter AF-7", "IfcFilterType",
         {"model": "AF-7", "filterGrade": "ePM1 70%"}, None),
    ):
        if component:
            post(role, "/clip/v1/supply-chain/catalogue/discover", {
                "authorityDid": products[component]["authorityDid"],
            })
        record = create_record(
            role, "product", label, ifc_class=ifc_class,
            sources=[source_record(products[component], quantity=1, unit="each")] if component else [],
            data={"manufacturer": "Aster" if role == "component_manufacturer" else "Northstar", **data},
        )
        products[name] = publish_record(role, record)
        seed_progress(f"Published {label}", advance=True)
    return products


def seed() -> None:
    # Completed work units, not an estimate of elapsed time.
    progress = SeedProgress(8 + (len(SUPPLIERS) + 4) * 8 + len(FACILITIES) * 25 + 5 + 4)
    token = _SEED_PROGRESS.set(progress)
    try:
        _seed()
        if progress.completed != progress.total:
            raise RuntimeError(f"Seed progress mismatch: {progress.completed}/{progress.total} steps")
    except BaseException:
        progress.render(force=True, outcome="Stopped")
        raise
    else:
        progress.render(force=True, outcome="Complete")
    finally:
        _SEED_PROGRESS.reset(token)


def _seed() -> None:
    seed_progress("Waiting for authority APIs (gossip converges in the background)", force=True)
    wait_for_nodes()
    seed_progress("Authorities ready; publishing product catalogue", advance=True, force=True)
    products = seed_products()
    seed_progress("Building shared spatial model and North Wing signed history", force=True)
    identities = {
        name: {
            "authorityDid": record["authorityDid"], "recordId": record["id"],
            "revision": record["revision"], "pinnedRevisions": [record["revision"]],
        }
        for name, record in products.items()
    }
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
            IDENTITY_SCHEMA: {"value": {"dataType": "Object"}},
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
                    IDENTITY_SCHEMA: identities["door"],
                    "ifc::name": "Northstar Door Model X",
                    "ifc::manufacturer": "Northstar Construction Products",
                    "ifc::model": "NDX-90",
                },
            },
            {
                "path": "types/pump",
                "attributes": {
                    IDENTITY_SCHEMA: identities["pump"],
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
                "corridor": "building/storey-1/corridor",
            }},
            {"path": "building/storey-1/lobby", "attributes": {
                "ifc::name": "Main Lobby",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SPACE-LOBBY", "class": "IfcSpace", "properties": {}},
            }, "children": {"door-1": ASSET_PATH}},
            {"path": "building/storey-1/plant-room", "attributes": {
                "ifc::name": "Plant Room",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SPACE-PLANT", "class": "IfcSpace", "properties": {}},
            }, "children": {"pump-1": "building/pump-1"}},
            {"path": "building/storey-1/corridor", "attributes": {
                "ifc::name": "Service Corridor",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "SPACE-CORRIDOR", "class": "IfcSpace", "properties": {}},
            }},
            {"path": "building/storey-1/plant-room/assembly", "attributes": {
                "ifc::name": "Plant Room Pump Assembly",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "ASSEMBLY-PUMPS", "class": "IfcElementAssembly", "properties": {}},
            }},
            {"path": "building/storey-1/plant-room", "children": {
                "assembly": "building/storey-1/plant-room/assembly",
            }},
            {"path": "zone/public", "attributes": {
                "ifc::name": "Public Areas",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "ZONE-PUBLIC", "class": "IfcZone", "properties": {}},
            }},
            {"path": "group/life-safety", "attributes": {
                "ifc::name": "Life Safety Assets",
                SOURCE_SCHEMA: {"format": "IFC4X3_ADD2", "id": "GROUP-SAFETY", "class": "IfcGroup", "properties": {}},
            }},
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
    owner_file["data"].extend(portfolio_locations())
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
        "spatialModelDatasetId": DATASET_ID,
    }
    seed_progress("Spatial model ready; North Wing deliveries, handovers and evidence", advance=True, force=True)
    seed_showcase(state, products)
    seed_progress("North Wing complete; publishing supplier offers", advance=True, force=True)
    seed_portfolio(state, products)
    seed_progress("Publishing post-install revisions and pending updates", force=True)
    seed_updates(state, products)
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    seed_progress("Verifying graph identities, events and undecided updates", force=True)
    verify()
    seed_progress("Verified all demo scenarios", advance=True, force=True)
    print("Seeded IFC assets, a multi-authority supply chain, project access, evidence, replication, and DID gossip.")


def seed_showcase(state: dict, products: dict[str, dict]) -> None:
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
        "recipients": [peer_did("owner"), peer_did("inspector")],
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

    spatial_paths = {
        "site": "site", "building": "building", "storey": "building/storey-1",
        "lobby": "building/storey-1/lobby", "plantRoom": "building/storey-1/plant-room",
        "corridor": "building/storey-1/corridor", "zone": "zone/public",
        "group": "group/life-safety", "assembly": "building/storey-1/plant-room/assembly",
    }
    pump_revision = products["pump"]

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
        {"expectedRevision": 0, "senders": [peer_did("main_contractor"), peer_did("manufacturer")]},
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
            "quantity": 1,
            "unit": "each",
            "batch": "NW-2026-10",
            "serials": ["P100-NW-001"],
        },
    )
    delivery_document = post(
        "main_contractor",
        f"/clip/v1/supply-chain/records/{delivery['id']}/documents",
        {
            "name": "North-Wing-delivery-note.txt",
            "mediaType": "text/plain",
            "content": base64.b64encode(
                b"Delivery to North Wing plant room; one serialized pump.\n"
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

    manufacturer_connection = post("manufacturer", "/clip/v1/supply-chain/projects/connect", {
        "authorityDid": peer_did("owner"), "projectId": project_id,
    })
    if manufacturer_connection["projectId"] != project_id or manufacturer_connection["local"]:
        raise RuntimeError("Manufacturer did not connect to the owner project")
    direct_supplies = {}
    for name, serials in (
        ("door", ["NDX-NW-001", "NDX-NW-002"]),
        ("pump", ["P100-NW-002"]),
    ):
        product = products[name]
        offer = create_record(
            "manufacturer", "offering", f"Northstar {name.title()} Direct Offer",
            ifc_class=product["ifcClass"], sources=[source_record(product)],
            data={"route": "Direct from manufacturer"},
        )
        offer_revision = publish_record("manufacturer", offer)
        supply = create_record(
            "manufacturer", "supply", f"North Wing Direct {name.title()} Delivery",
            ifc_class=product["ifcClass"], sources=[source_record(offer_revision)],
            project_id=project_id,
            data={"quantity": len(serials), "unit": "each", "serials": serials},
        )
        direct_supplies[name] = submit_and_accept(
            "manufacturer", "owner", project_id, supply,
        )["acceptedRecord"]
    door_installations = []
    for name, serial, location in (
        ("Lobby Fire Door", "NDX-NW-001", "Main Lobby"),
        ("Corridor Fire Door", "NDX-NW-002", "Service Corridor"),
    ):
        door_installations.append(create_record(
            "owner", "installation", name, ifc_class="IfcDoor",
            sources=[source_record(direct_supplies["door"], quantity=1, unit="each", serials=[serial])],
            project_id=project_id, data={"location": location, "status": "installed"},
        ))
    direct_pump = create_record(
        "owner", "installation", "Standby Circulation Pump", ifc_class="IfcPump",
        sources=[source_record(direct_supplies["pump"], quantity=1, unit="each", serials=["P100-NW-002"])],
        project_id=project_id, data={"location": "Plant Room", "status": "commissioned"},
    )
    connect_installations(project_id, [
        (spatial_paths["lobby"], door_installations[0]),
        (spatial_paths["corridor"], door_installations[1]),
        (spatial_paths["assembly"], installation),
        (spatial_paths["plantRoom"], direct_pump),
    ])

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
    join_ack = post("inspector", "/clip/v1/projects/invites/redeem", {"code": invitation["code"]})
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

    post("inspector", "/clip/v1/node/storage/opt-in", {"opt_in": True})
    placement = post("owner", f"/clip/v1/evidence/{evidence_id}/replicate", {
        "peerDid": peer_did("inspector"),
    })
    if len(placement["receipts"]) != evidence["manifest"]["manifest"]["fragmentCount"]:
        raise RuntimeError("Inspector did not acknowledge every encrypted evidence fragment")

    replication_ack = post("owner", "/clip/v1/replication/push", {
        "peerDid": peer_did("inspector"),
        "datasetId": DATASET_ID,
    })
    state.update({
        "showcaseProjectId": project_id,
        "showcaseEntities": {
            "doorType": product_path((products["door"]["authorityDid"], products["door"]["id"])),
            "pumpType": product_path((pump_revision["authorityDid"], pump_revision["id"])),
            "door": door_installations[0]["graphPath"],
            "secondDoor": door_installations[1]["graphPath"],
            "pump": installation["graphPath"],
            "directPump": direct_pump["graphPath"],
        },
        "spatialEntities": spatial_paths,
        "manufacturerDoorId": products["door"]["id"],
        "manufacturerPumpId": pump_revision["id"],
        "componentManufacturerDid": peer_did("component_manufacturer"),
        "manufacturerMotorId": products["motor"]["id"],
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
        "replicaPeerDid": peer_did("inspector"),
        "viewerJoinRequestId": join_request_id,
    })


def portfolio_locations() -> list[dict]:
    nodes = []
    for index, name in enumerate(FACILITIES, 1):
        site = f"portfolio/facility-{index}"
        building = site + "/building"
        nodes.extend([
            {"path": site, "attributes": {"ifc::name": name + " Campus", SOURCE_SCHEMA: {
                "format": "CLIP", "id": f"SITE-{index}", "class": "IfcSite", "properties": {},
            }}, "children": {"building": building}},
            {"path": building, "attributes": {"ifc::name": name, SOURCE_SCHEMA: {
                "format": "CLIP", "id": f"BLDG-{index}", "class": "IfcBuilding", "properties": {},
            }}, "children": {f"floor-{floor}": building + f"/floor-{floor}" for floor in (1, 2)}},
        ])
        for floor in (1, 2):
            path = building + f"/floor-{floor}"
            nodes.append({"path": path, "attributes": {
                "ifc::name": f"{name} / Floor {floor}", SOURCE_SCHEMA: {
                    "format": "CLIP", "id": f"FLOOR-{index}-{floor}",
                    "class": "IfcBuildingStorey", "properties": {},
                },
            }, "children": {room: path + "/" + room for room in ("plant", "lobby", "workshop")}})
            for room in ("plant", "lobby", "workshop"):
                nodes.append({"path": path + "/" + room, "attributes": {
                    "ifc::name": f"{name} / Floor {floor} / {room.title()}", SOURCE_SCHEMA: {
                        "format": "CLIP", "id": f"SPACE-{index}-{floor}-{room}",
                        "class": "IfcSpace", "properties": {},
                    },
                }})
    return nodes


def connect_sender(sender: str, recipient: str, project_id: str) -> None:
    result = post(sender, "/clip/v1/supply-chain/projects/connect", {
        "authorityDid": peer_did(recipient), "projectId": project_id,
    })
    if result["projectId"] != project_id or result["local"]:
        raise RuntimeError(f"{sender} did not connect to {recipient}'s project")


def receiving_project(role: str, name: str, senders: list[str]) -> dict:
    project = create_project(role, name)
    put(role, f"/clip/v1/supply-chain/projects/{quote(project['projectId'], safe='')}/senders", {
        "expectedRevision": 0, "senders": [peer_did(sender) for sender in senders],
    })
    for sender in senders:
        connect_sender(sender, role, project["projectId"])
    return project


def seed_portfolio(state: dict, products: dict[str, dict]) -> None:
    offers = {}
    direct = {}
    for role in SUPPLIERS:
        for publisher in ("manufacturer", "component_manufacturer"):
            post(role, "/clip/v1/supply-chain/catalogue/discover", {"authorityDid": peer_did(publisher)})
        offers[role] = {}
        for key, product in products.items():
            record = create_record(
                role, "offering", f"{role.replace('_', ' ').title()} / {product['name']}",
                ifc_class=product["ifcClass"], sources=[source_record(product)],
                data={"sku": f"{role}-{key}", "warrantyYears": 3, "leadTimeDays": 7},
            )
            offers[role][key] = publish_record(role, record)
            seed_progress(f"Published {role} offer: {key}", advance=True)
    for key, product in products.items():
        role = "manufacturer" if product["authorityDid"] == peer_did("manufacturer") else "component_manufacturer"
        direct[key] = publish_record(role, create_record(
            role, "offering", f"Factory Direct / {product['name']}",
            ifc_class=product["ifcClass"], sources=[source_record(product)],
            data={"route": "Factory direct", "warrantyYears": 2},
        ))
        seed_progress(f"Published factory direct offer: {key}", advance=True)
    post("supplier_2", "/clip/v1/supply-chain/catalogue/discover", {"authorityDid": peer_did("supplier")})
    nested = {}
    for key, offer in offers["supplier"].items():
        nested[key] = publish_record("supplier_2", create_record(
            "supplier_2", "offering", f"Regional Distribution / {offer['name']}",
            ifc_class=offer["ifcClass"], sources=[source_record(offer)],
            data={"route": "Wholesale supplier to regional supplier", "distributionRegion": "South"},
        ))
        seed_progress(f"Published two-tier distribution offer: {key}", advance=True)
    contractor_offers = {}
    for supplier in SUPPLIERS[:2]:
        post("main_contractor", "/clip/v1/supply-chain/catalogue/discover", {"authorityDid": peer_did(supplier)})
        contractor_offers[supplier] = {}
        for key, offer in offers[supplier].items():
            contractor_offers[supplier][key] = publish_record("main_contractor", create_record(
                "main_contractor", "offering", f"Installed Package / {offer['name']}",
                ifc_class=offer["ifcClass"], sources=[source_record(offer)],
                data={"installationIncluded": True, "warrantyYears": 5},
            ))
            seed_progress(f"Published contractor package: {supplier} / {key}", advance=True)
    routes = (
        ("supplier-contractor", "supplier", "main_contractor", ("door", "pump", "valve")),
        ("regional-contractor", "supplier_2", "main_contractor", ("fan", "filter", "pump")),
        ("supplier-client-self-install", "supplier_3", "owner", ("sensor", "controller", "door")),
        ("two-suppliers-client-self-install", "supplier_2", "owner", ("valve", "fan", "filter")),
        ("northstar-direct-client", "manufacturer", "owner", ("door", "pump", "valve")),
        ("aster-direct-client", "component_manufacturer", "owner", ("motor", "sensor", "controller")),
    )
    portfolio = []
    for facility_index, name in enumerate(FACILITIES, 1):
        print(f"Seeding {name}: six procurement routes, 18 installations, 54 work events...", flush=True)
        owner_project = receiving_project(
            "owner", name + " Asset Renewal", ["main_contractor", *SUPPLIERS, "manufacturer", "component_manufacturer"],
        )
        project_id = owner_project["projectId"]
        put("owner", f"/clip/v1/projects/{quote(project_id, safe='')}/permissions", {
            "expectedRevision": owner_project["revision"], "visibility": "private",
            "members": {peer_did("main_contractor"): "contributor", peer_did("inspector"): "contributor"},
        })
        contractor_project = receiving_project(
            "main_contractor", name + " Installation Works", list(SUPPLIERS[:2]),
        )["projectId"]
        placements = []
        event_operations = {"owner": [], "main_contractor": [], "inspector": []}
        facility = {"name": name, "projectId": project_id, "contractorProjectId": contractor_project,
                    "installations": [], "events": [], "deliveries": []}
        for route_index, (route, sender, installer, keys) in enumerate(routes):
            seed_progress(f"{name}: route {route_index + 1}/6 / {route}", force=True)
            key = keys[(facility_index - 1) % len(keys)]
            product = products[key]
            offer = nested[key] if route_index == 3 else (
                direct[key] if route_index >= 4 else offers[sender][key])
            recipient_project = contractor_project if installer == "main_contractor" else project_id
            serials = [f"F{facility_index}-R{route_index + 1}-{key.upper()}-{number:03}" for number in (1, 2, 3)]
            delivery = create_record(
                sender, "supply", f"{name} / {route} / {key} delivery",
                ifc_class=product["ifcClass"], sources=[source_record(offer)],
                project_id=recipient_project,
                data={"quantity": 3, "unit": "each", "serials": serials, "batch": f"OCT26-F{facility_index}",
                      "procurementRoute": route},
            )
            received = submit_and_accept(sender, installer, recipient_project, delivery)
            seed_progress(f"{name}: route {route_index + 1}/6 delivery accepted", advance=True)
            facility["deliveries"].append({"route": route, "sender": sender, "recipient": installer,
                                          "submissionId": received["submissionId"],
                                          "acceptedRecordId": received["acceptedRecord"]["id"]})
            for number, serial in enumerate(serials, 1):
                floor = 1 if route_index < 3 else 2
                room = "lobby" if key == "door" else "plant" if key in ("pump", "motor", "valve", "fan") else "workshop"
                location = f"portfolio/facility-{facility_index}/building/floor-{floor}/{room}"
                installation = create_record(
                    installer, "installation", f"{name} / {key.title()} / R{route_index + 1}-{number}",
                    ifc_class=product["ifcClass"].removesuffix("Type"),
                    sources=[source_record(received["acceptedRecord"], quantity=1, unit="each", serials=[serial])],
                    project_id=recipient_project,
                    data={"status": "installed", "location": f"{name} / Floor {floor} / {room}",
                          "installerDid": peer_did(installer), "procurementRoute": route,
                          "installedAt": "2026-10-01T09:00:00Z"},
                )
                handover = None
                asset = installation
                if installer == "main_contractor":
                    handover = submit_and_accept("main_contractor", "owner", project_id, installation)
                    asset = handover["acceptedRecord"]
                placements.append((location, asset))
                info = {"recordId": asset["id"], "graphPath": asset["graphPath"], "serial": serial,
                        "productKey": key, "route": route, "installer": installer,
                        "installerRecord": installation, "handoverSubmissionId": handover["submissionId"] if handover else None}
                facility["installations"].append(info)
                seed_progress(f"{name}: route {route_index + 1}/6 / installation {number}/3 accepted", advance=True)
                previous = None
                for stage in range(3):
                    kind = "installation" if stage == 0 else "inspection"
                    status = "failed" if stage == 1 and number == 1 else "complete"
                    actor = installer if stage == 0 else "inspector"
                    path = f"events/f{facility_index}-r{route_index + 1}-{number}-{stage}"
                    label = "Installation" if stage == 0 else "Initial inspection" if stage == 1 else (
                        "Remedial reinspection" if number == 1 else "Periodic inspection")
                    event = {"kind": kind, "actorDid": peer_did(actor),
                             "occurredAt": f"2026-10-0{stage + 1}T10:00:00Z", "subject": asset["graphPath"],
                             "status": status, **({"previousEvent": previous} if previous else {})}
                    event_operations[actor].append({"action": "create", "node": {
                        "path": path, "attributes": {"ifc::name": f"{label} / {serial}", EVENT_SCHEMA: event},
                    }})
                    facility["events"].append({"path": path, "actor": actor, "status": status,
                                               "kind": kind, "subject": asset["graphPath"]})
                    previous = path
        seed_progress(f"{name}: linking 18 locations and accepting 54 signed work events", force=True)
        connect_installations(project_id, placements)
        for actor, operations in event_operations.items():
            commit_graph_operations(project_id, operations, actor)
        portfolio.append(facility)
        seed_progress(f"{name}: facility {facility_index}/{len(FACILITIES)} complete", advance=True, force=True)
    state["portfolio"] = portfolio
    state["portfolioProducts"] = products
    state["portfolioOffers"] = offers
    state["portfolioContractorOffers"] = contractor_offers


def update_record(role: str, record: dict, *, data: dict, sources: list[dict] | None = None) -> dict:
    return put(role, f"/clip/v1/supply-chain/records/{record['id']}", {
        "kind": record["kind"], "name": record["name"], "ifcClass": record["ifcClass"],
        "projectId": record["projectId"], "data": {**record["data"], **data},
        "sources": sources if sources is not None else record["sources"],
        "expectedRevision": record["revision"],
    })


def seed_updates(state: dict, products: dict[str, dict]) -> None:
    pending = []
    revisions = []
    updated_products = {}
    for key, recipient, details in (
        ("pump", "supplier", {"maintenanceIntervalHours": 4000, "catalogueBulletin": "P100 service guidance, edition 2"}),
        ("door", "supplier_2", {"certificationReference": "FIRE-2026-REV-B", "catalogueBulletin": "Updated fire certificate"}),
        ("controller", "supplier_3", {"firmwareCompatibility": "3.x", "catalogueBulletin": "BACnet integration notes"}),
    ):
        original = products[key]
        sender = "manufacturer" if original["authorityDid"] == peer_did("manufacturer") else "component_manufacturer"
        project = receiving_project(recipient, f"{key.title()} Post-install Catalogue Review", [sender])["projectId"]
        baseline = submit_and_accept(sender, recipient, project, original)
        draft = update_record(sender, original, data=details)
        current = publish_record(sender, draft)
        updated_products[key] = current
        issued = issue_submission(sender, recipient, project, current, supersedes=baseline["submissionId"])
        pending.append({"sender": sender, "recipient": recipient, "projectId": project,
                        "submissionId": issued["id"], "baselineId": baseline["submissionId"],
                        "acceptedRecordId": baseline["acceptedRecord"]["id"], "recordId": original["id"]})
        revisions.append({"key": key, "authority": sender, "recordId": original["id"],
                          "installedRevision": original["revision"], "publishedRevision": current["revision"]})
        seed_progress(f"Pending catalogue update delivered: {key} / {recipient}", advance=True)
    # Advance one supplier's offer, while other suppliers deliberately keep their older pins.
    offer = state["portfolioOffers"]["supplier_2"]["pump"]
    project = receiving_project("main_contractor", "Regional Supplier Catalogue Update Review", ["supplier_2"])["projectId"]
    baseline = submit_and_accept("supplier_2", "main_contractor", project, offer)
    post("supplier_2", "/clip/v1/supply-chain/catalogue/discover", {"authorityDid": peer_did("manufacturer")})
    updated_offer = publish_record("supplier_2", update_record(
        "supplier_2", offer, data={"warrantyYears": 6, "catalogueBulletin": "Service bulletin included"},
        sources=[source_record(updated_products["pump"])],
    ))
    issued = issue_submission("supplier_2", "main_contractor", project, updated_offer, supersedes=baseline["submissionId"])
    pending.append({"sender": "supplier_2", "recipient": "main_contractor", "projectId": project,
                    "submissionId": issued["id"], "baselineId": baseline["submissionId"],
                    "acceptedRecordId": baseline["acceptedRecord"]["id"], "recordId": offer["id"]})
    seed_progress("Pending regional supplier update delivered to contractor", advance=True)
    facility = state["portfolio"][0]
    asset = facility["installations"][0]
    updated_installation = update_record("main_contractor", asset["installerRecord"], data={
        "handoverWarrantyYears": 6, "catalogueBulletin": "Post-install handover documentation supplement",
    })
    issued = issue_submission(
        "main_contractor", "owner", facility["projectId"], updated_installation,
        supersedes=asset["handoverSubmissionId"],
    )
    pending.append({"sender": "main_contractor", "recipient": "owner", "projectId": facility["projectId"],
                    "submissionId": issued["id"], "baselineId": asset["handoverSubmissionId"],
                    "acceptedRecordId": asset["recordId"], "recordId": updated_installation["id"]})
    state["productUpdates"] = revisions
    state["pendingUpdates"] = pending
    seed_progress("Pending contractor handover update delivered to client", advance=True)
    print("Published three post-install product revisions; five delivered updates await explicit acceptance.", flush=True)


def connect_installations(project_id: str, placements: list[tuple[str, dict]]) -> None:
    response = request("owner", "GET", f"/ifc/v1/datasets/{quote(project_id, safe='')}/graph")
    response.raise_for_status()
    graph = response.json()
    operations = []
    for parent, record in placements:
        source = graph["entities"][record["graphPath"]]["components"][SOURCE_SCHEMA]
        source["properties"]["locationReference"] = {
            "authorityDid": peer_did("owner"), "datasetId": DATASET_ID, "entityPath": parent,
        }
        operations.append({"action": "contribute", "node": {
            "path": record["graphPath"], "attributes": {SOURCE_SCHEMA: source},
        }})
    commit_graph_operations(project_id, operations)


def commit_graph_operations(project_id: str, operations: list[dict], actor: str = "owner") -> None:
    datasets = request("owner", "GET", "/ifc/v1/datasets")
    datasets.raise_for_status()
    sequence = 0
    schema_digest = None
    for dataset in datasets.json()["items"]:
        if dataset["datasetId"] == project_id:
            schema_digest = dataset["schemaDigest"]
        history = request("owner", "GET", f"/ifc/v1/datasets/{quote(dataset['datasetId'], safe='')}/history")
        history.raise_for_status()
        sequence = max([sequence, *[item["receipt"]["sequence"] for item in history.json()["items"]]])
    if schema_digest is None:
        raise RuntimeError("Installation project is missing from the owner's datasets")
    for offset in range(0, len(operations), 32):
        sequence = commit_graph_batch(project_id, operations[offset:offset + 32], actor, sequence, schema_digest)


def commit_graph_batch(project_id: str, operations: list[dict], actor: str, sequence: int, schema_digest: str) -> int:
    proposal = {
        "@context": ["https://w3id.org/security/data-integrity/v2", {"@vocab": "urn:clip:protocol:"}],
        "transactionId": str(uuid4()), "actorDid": peer_did(actor),
        "target": {"authorityDid": peer_did("owner"), "datasetId": project_id},
        "operations": operations,
        "expectedSequence": sequence, "schemaDigest": schema_digest, "created": _utc_now(),
    }
    normalized = IfcGraphProposalTransaction.model_validate(
        _sign_transaction(proposal, actor, "assertionMethod")
    ).model_dump(mode="json", by_alias=True, exclude={"proof"})
    pending = post("owner", "/ifc/v1/proposals", _sign_transaction(normalized, actor, "assertionMethod"))
    receipt = post("owner", "/ifc/v1/decisions", _sign_transaction({
        "@context": proposal["@context"], "transactionId": str(uuid4()),
        "actorDid": peer_did("owner"), "decision": "accept",
        "proposalId": pending["proposalId"], "proposalDigest": pending["proposalDigest"],
        "expectedSequence": sequence, "created": _utc_now(),
    }, "owner", "capabilityInvocation"))
    if not receipt["accepted"]:
        raise RuntimeError(f"Owner did not accept graph contributions from {actor}")
    return receipt["sequence"]


def verify() -> None:
    state = json.loads(STATE_PATH.read_text(encoding="utf-8"))
    if "portfolio" not in state or "pendingUpdates" not in state:
        raise RuntimeError(
            "This demo was seeded before the large portfolio and update scenarios. "
            "Stop the demo services and run examples\\run-network.ps1 without -KeepData."
        )
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
    expected_products = {
        "doorType": (state["manufacturerDoorId"], ["door", "secondDoor"]),
        "pumpType": (state["manufacturerPumpId"], ["pump", "directPump"]),
    }
    for type_name, (record_id, asset_names) in expected_products.items():
        type_path = state["showcaseEntities"][type_name]
        definitions = [
            path for path, entity in graph["entities"].items()
            if entity["components"].get(IDENTITY_SCHEMA, {}).get("recordId") == record_id
        ]
        if definitions != [type_path]:
            raise AssertionError(f"Expected one shared {type_name}, got {definitions}")
        for asset_name in asset_names:
            asset_path = state["showcaseEntities"][asset_name]
            if graph["entities"][asset_path]["inherits"].get("manufacturerType") != type_path:
                raise AssertionError(f"{asset_name} does not use the shared manufacturer type")
            location = graph["entities"][asset_path]["components"][SOURCE_SCHEMA]["properties"].get("locationReference")
            if not location or location["datasetId"] != state["spatialModelDatasetId"]:
                raise AssertionError(f"{asset_name} does not reference the single owner spatial model")
    motor_path = product_path((state["componentManufacturerDid"], state["manufacturerMotorId"]))
    motor_identity = graph["entities"][motor_path]["components"][IDENTITY_SCHEMA]
    if motor_identity["authorityDid"] != peer_did("component_manufacturer"):
        raise AssertionError("Motor is not attributed to the independent component manufacturer")
    pump_components = graph["entities"][state["showcaseEntities"]["pumpType"]]["components"]
    if motor_path not in pump_components["urn:clip:construction:component-types:v1"]:
        raise AssertionError("Northstar pump does not reference the Aster manufacturer's motor")
    legacy_response = request(
        "owner", "GET", f"/ifc/v1/datasets/{quote(DATASET_ID, safe='')}/graph",
    )
    legacy_response.raise_for_status()
    spatial_graph = legacy_response.json()
    for path in state["spatialEntities"].values():
        if path not in spatial_graph["entities"] or path in graph["entities"]:
            raise AssertionError(f"Spatial entity {path} must be authored only once in the owner spatial model")
    for name, record_id in (("door", state["manufacturerDoorId"]), ("pump", state["manufacturerPumpId"])):
        identity = legacy_response.json()["entities"][f"types/{name}"]["components"][IDENTITY_SCHEMA]
        if identity["authorityDid"] != peer_did("manufacturer") or identity["recordId"] != record_id:
            raise AssertionError(f"Imported {name} type does not share the manufacturer product identity")

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
    if project["members"].get(state["replicaPeerDid"]) != "viewer":
        raise AssertionError(f"Inspector invite was not accepted as a project Viewer: {project['members']}")

    evidence_response = request("owner", "GET", f"/clip/v1/evidence/{state['evidenceId']}")
    evidence_response.raise_for_status()
    inspector_storage = request("inspector", "GET", "/clip/v1/node/storage")
    inspector_storage.raise_for_status()
    if inspector_storage.json()["fragment_count"] < state["evidenceFragmentCount"]:
        raise AssertionError(f"Inspector does not hold all seeded evidence fragments: {inspector_storage.json()}")
    replication_response = request("owner", "GET", "/clip/v1/replication/status")
    replication_response.raise_for_status()
    if not any(
        ack.get("payload", {}).get("digest") == state["replicationDigest"]
        for ack in replication_response.json()["acknowledgements"]
    ):
        raise AssertionError("Owner does not have a matching signed replication acknowledgement")

    verify_portfolio(state)
    peers = wait_for_gossip()
    print(
        f"Verified IFC assets={product!r}/{pump_name!r}, "
        f"commissioning={commissioning['status']}, supply={accepted_supply.json()['kind']}, "
        f"inspection={accepted_asset.json()['kind']}, evidence fragments={state['evidenceFragmentCount']}, "
        f"owner-visible peers={len(peers)}."
    )


def verify_portfolio(state: dict) -> None:
    installations = 0
    events = 0
    routes = set()
    for facility in state["portfolio"]:
        response = request("owner", "GET", f"/ifc/v1/datasets/{quote(facility['projectId'], safe='')}/graph")
        response.raise_for_status()
        graph = response.json()
        for asset in facility["installations"]:
            entity = graph["entities"][asset["graphPath"]]
            location = entity["components"][SOURCE_SCHEMA]["properties"]["locationReference"]
            if location["datasetId"] != DATASET_ID or not location["entityPath"].startswith("portfolio/"):
                raise AssertionError("Portfolio asset does not reference the shared spatial model")
            identity = graph["entities"][entity["inherits"]["manufacturerType"]]["components"][IDENTITY_SCHEMA]
            original = state["portfolioProducts"][asset["productKey"]]
            if (identity["authorityDid"], identity["recordId"]) != (original["authorityDid"], original["id"]):
                raise AssertionError("Portfolio installation lost its original product identity")
            routes.add(asset["route"])
            installations += 1
        for event in facility["events"]:
            component = graph["entities"][event["path"]]["components"][EVENT_SCHEMA]
            if component["actorDid"] != peer_did(event["actor"]) or component["status"] != event["status"]:
                raise AssertionError("Work event actor/status does not match its signed contribution")
            if component["subject"] != event["subject"]:
                raise AssertionError("Work event is not linked to the intended installation")
            events += 1
    if installations != 108 or events != 324 or len(routes) != 6:
        raise AssertionError(f"Incomplete portfolio: installations={installations}, events={events}, routes={len(routes)}")
    for update in state["pendingUpdates"]:
        response = request(update["recipient"], "GET", f"/clip/v1/supply-chain/submissions/{update['submissionId']}")
        response.raise_for_status()
        submission = response.json()
        if submission["status"] != "issued" or submission.get("decision") or submission["supersedes"] != update["baselineId"]:
            raise AssertionError("Seeded update must await an explicit recipient decision")
        accepted = request(update["recipient"], "GET", f"/clip/v1/supply-chain/records/{update['acceptedRecordId']}")
        accepted.raise_for_status()
        snapshot = accepted.json()["acceptedFrom"]["snapshot"]
        revised = next(record for record in submission["issue"]["records"] if record["id"] == update["recordId"])
        if snapshot["revision"] >= revised["revision"]:
            raise AssertionError("Pending update overwrote the accepted baseline or did not advance its revision")
    print(f"Verified portfolio: {installations} new installations, {events} work events, "
          f"{len(routes)} procurement routes, {len(state['pendingUpdates'])} pending updates.", flush=True)


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
        seed()
    else:
        wait_for_nodes()
        verify()
if __name__ == "__main__":
    main()