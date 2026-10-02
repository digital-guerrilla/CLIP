"""Seed and verify the six-node CLIP network and IFC graph demo."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import time
from urllib.parse import quote
from uuid import uuid4

import httpx
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.federation.clip_layers import sha256_sri_integrity


DATA = Path(__file__).resolve().parent / "data"
STATE_PATH = DATA / "clip-demo-state.json"
NODES = {
    "manufacturer": (8101, "manufacturer-key"),
    "supplier": (8102, "supplier-key"),
    "main_contractor": (8103, "contractor-key"),
    "owner": (8104, "owner-key"),
    "inspector": (8105, "inspector-key"),
    "relay": (8106, "relay-key"),
}
DATASET_ID = "urn:owner:north-wing:v1"
MANUFACTURER_DATASET_ID = "urn:manufacturer:door-catalog:v1"
ASSET_PATH = "building/door-1"
INSTALLATION_PATH = "events/installation-1001"


def peer_did(role: str) -> str:
    port, _ = NODES[role]
    return f"did:web:127.0.0.1%3A{port}"


def node_url(role: str) -> str:
    return f"http://127.0.0.1:{NODES[role][0]}"


def post(role: str, path: str, body: dict) -> dict:
    response = httpx.post(
        f"{node_url(role)}{path}",
        headers={"x-api-key": NODES[role][1]},
        json=body,
        timeout=20,
    )
    response.raise_for_status()
    return response.json()


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
            headers={"x-api-key": NODES["owner"][1]},
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


def _publication(file: dict, publisher_did: str, key_manager: NodeKeyManager) -> bytes:
    method = f"{publisher_did}#authority-key"
    signed = add_data_integrity_proof(
        {
            "@context": [
                "https://w3id.org/security/data-integrity/v2",
                {"@vocab": "urn:clip:protocol:"},
            ],
            "publisherDid": publisher_did,
            "file": file,
        },
        key_manager.private_key_bytes,
        verification_method=method,
        proof_purpose="assertionMethod",
        created=datetime.now(timezone.utc),
    )
    return json.dumps(signed).encode("utf-8")


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


def seed() -> None:
    wait_for_nodes()
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
        },
        "data": [
            {"path": "types", "children": {"door": "types/door"}},
            {
                "path": "types/door",
                "attributes": {
                    "ifc::name": "Northstar Door Model X",
                    "ifc::manufacturer": "Northstar Construction Products",
                },
            },
        ],
    }
    manufacturer_registration = post("manufacturer", "/ifc/v1/datasets", {
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
            {"path": "building", "children": {"door-1": "building/door-1"}},
            {
                "path": ASSET_PATH,
                "inherits": {"type": "types/door"},
                "attributes": {
                    "ifc::serial": "DOOR-1001",
                    "clip::installation-reference": INSTALLATION_PATH,
                },
            },
            {
                "path": "events",
                "children": {"installation-1001": INSTALLATION_PATH},
            },
            {"path": INSTALLATION_PATH},
        ],
    }
    owner_registration = post("owner", "/ifc/v1/datasets", {
        "file": owner_file,
        "trustedProposers": [peer_did("main_contractor")],
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

    state = {
        "ownerDid": peer_did("owner"),
        "datasetId": DATASET_ID,
        "assetPath": ASSET_PATH,
        "installationPath": INSTALLATION_PATH,
        "manufacturerDid": peer_did("manufacturer"),
        "productName": "Northstar Door Model X",
        "installationStatus": "complete",
        "acceptedSequence": receipt["sequence"],
    }
    DATA.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")
    verify()
    print("Seeded IFC product type, owner asset, installation agreement, and CLIP DID gossip peers.")


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

    peers = wait_for_gossip()
    print(
        f"Verified product={product!r}, manufacturer={manufacturer!r}, "
        f"installation={installation['status']}, owner-visible peers={len(peers)}."
    )


def get_component(role: str, dataset_id: str, entity_path: str, schema_id: str):
    response = httpx.get(
        f"{node_url(role)}/ifc/v1/datasets/{quote(dataset_id, safe='')}/components",
        params={"entity_path": entity_path, "component_schema_id": schema_id},
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["value"]


def wait_for_gossip(timeout: float = 45.0) -> list[dict]:
    expected = {peer_did(role) for role in NODES if role != "owner"}
    deadline = time.monotonic() + timeout
    latest = []
    while time.monotonic() < deadline:
        response = httpx.get(
            f"{node_url('owner')}/clip/v1/network/gossip/peers",
            headers={"x-api-key": NODES["owner"][1]},
            timeout=3,
        )
        response.raise_for_status()
        latest = response.json()
        known = {peer["did"] for peer in latest}
        manufacturer = next(
            (peer for peer in latest if peer["did"] == peer_did("manufacturer")),
            None,
        )
        if expected.issubset(known) and manufacturer and manufacturer["status"] == "alive":
            return latest
        time.sleep(0.25)
    raise TimeoutError(f"Owner gossip view did not converge: {latest}")


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


def wait_for_nodes(timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            for role in NODES:
                response = httpx.get(f"{node_url(role)}/.well-known/did.json", timeout=2)
                response.raise_for_status()
                if response.json().get("id") != peer_did(role):
                    raise ValueError(f"Unexpected DID document for {role}")
            return
        except (httpx.RequestError, httpx.HTTPStatusError, ValueError):
            time.sleep(0.25)
    raise TimeoutError("Six CLIP authority DID documents did not become available")


if __name__ == "__main__":
    main()