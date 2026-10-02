import os
import subprocess
import sys
import tempfile
import sqlite3
import base64
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import httpx
import nacl.signing

from node.app.core.crypto import NodeKeyManager
from node.app.core.data_integrity import add_data_integrity_proof
from node.app.core.did import verify_clip_message_proof
from node.app.core.ifc_protocol import IfcDecisionTransaction, IfcProposalTransaction
from node.app.federation.clip_layers import sha256_sri_integrity


@unittest.skipUnless(os.getenv("CLIP_INTEGRATION") == "1", "set CLIP_INTEGRATION=1")
class ClipNetworkIntegrationTest(unittest.TestCase):
    def test_six_authority_ifc_type_agreement_and_clip_gossip(self) -> None:
        roles = ["manufacturer", "supplier", "main_contractor", "owner", "inspector", "relay"]
        ports = list(range(8301, 8307))
        peer_dids = {
            role: f"did:web:127.0.0.1%3A{port}"
            for role, port in zip(roles, ports)
        }
        processes: list[subprocess.Popen] = []
        logs = []
        environments = []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            try:
                seed_dids = ",".join(peer_dids.values())
                for role, port in zip(roles, ports):
                    log = open(root / f"{role}.log", "w+", encoding="utf-8")
                    logs.append(log)
                    did = peer_dids[role]
                    environment = os.environ.copy()
                    environment.update({
                        "PYTHONUNBUFFERED": "1",
                        "NODE_DOMAIN": f"127.0.0.1:{port}",
                        "NODE_API_BASE": f"http://127.0.0.1:{port}",
                        "API_KEY": f"{role}-key",
                        "DATABASE_URL": f"sqlite+aiosqlite:///{(root / f'{role}.db').as_posix()}",
                        "PRIVATE_KEY_FILE": str(root / f"{role}.key"),
                        "DOCUMENT_STORAGE_DIR": str(root / f"documents/{role}"),
                        "CLIP_GOSSIP_INTERVAL": "1",
                        "CLIP_GOSSIP_ENABLED": "true",
                        "CLIP_GOSSIP_SEEDS": seed_dids,
                        "CLIP_TRUSTED_PUBLISHERS": seed_dids,
                        "CLIP_ALLOW_HTTP_LOOPBACK": "true",
                        "DID_WEB_ID": did,
                        "DID_VERIFICATION_METHOD": f"{did}#authority-key",
                        "NODE_ROLE": role,
                        "ENCRYPTED_STORAGE_OPT_IN": "true",
                    })
                    environments.append(environment)
                    processes.append(subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "uvicorn",
                            "node.app.main:app",
                            "--host",
                            "127.0.0.1",
                            "--port",
                            str(port),
                            "--log-level",
                            "warning",
                        ],
                        cwd=Path(__file__).resolve().parents[1],
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    ))

                self._wait_for_did_documents(ports, processes)
                manufacturer_port = ports[0]
                owner_port = ports[3]
                manufacturer_did = peer_dids["manufacturer"]
                owner_did = peer_dids["owner"]
                contractor_did = peer_dids["main_contractor"]
                manufacturer_dataset_id = "urn:manufacturer:catalog:v1"
                owner_dataset_id = "urn:owner:west-wing:v1"

                manufacturer_file = {
                    "header": {
                        "id": manufacturer_dataset_id,
                        "ifcxVersion": "ifcx_alpha",
                        "dataVersion": "1.0.0",
                        "author": "manufacturer",
                        "timestamp": "2026-10-01T00:00:00Z",
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
                registered_manufacturer = self._post(
                    manufacturer_port,
                    "/ifc/v1/datasets",
                    {"file": manufacturer_file, "trustedProposers": []},
                    "manufacturer-key",
                )
                self.assertTrue(registered_manufacturer["schemaDigest"])

                manufacturer_publication_path = (
                    "/ifc/v1/datasets/"
                    + quote(manufacturer_dataset_id, safe="")
                    + "/publication"
                )
                publication_response = httpx.get(
                    f"http://127.0.0.1:{manufacturer_port}{manufacturer_publication_path}",
                    timeout=5,
                )
                publication_response.raise_for_status()

                owner_file = {
                    "header": {
                        "id": owner_dataset_id,
                        "ifcxVersion": "ifcx_alpha",
                        "dataVersion": "1.0.0",
                        "author": "owner",
                        "timestamp": "2026-10-01T00:00:00Z",
                    },
                    "imports": [{
                        "uri": f"http://127.0.0.1:{manufacturer_port}{manufacturer_publication_path}",
                        "integrity": sha256_sri_integrity(publication_response.content),
                    }],
                    "schemas": {
                        "ifc::serial": {"value": {"dataType": "String"}},
                        "clip::event-reference": {"value": {"dataType": "Reference"}},
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
                            "path": "building/door-1",
                            "inherits": {"type": "types/door"},
                            "attributes": {
                                "ifc::serial": "DOOR-1001",
                                "clip::event-reference": "events/installation-1001",
                            },
                        },
                        {
                            "path": "events",
                            "children": {"installation-1001": "events/installation-1001"},
                        },
                        {"path": "events/installation-1001"},
                    ],
                }
                owner_registration = self._post(
                    owner_port,
                    "/ifc/v1/datasets",
                    {"file": owner_file, "trustedProposers": [contractor_did]},
                    "owner-key",
                )

                product = self._get_component(
                    owner_port,
                    owner_dataset_id,
                    "building/door-1",
                    "ifc::name",
                )
                manufacturer = self._get_component(
                    owner_port,
                    owner_dataset_id,
                    "building/door-1",
                    "ifc::manufacturer",
                )
                self.assertEqual(product, "Northstar Door Model X")
                self.assertEqual(manufacturer, "Northstar Construction Products")

                contractor_key = NodeKeyManager.load_or_create(str(root / "main_contractor.key"))
                contractor_method = f"{contractor_did}#authority-key"
                proposal_model = IfcProposalTransaction.model_validate({
                    "actorDid": contractor_did,
                    "target": {
                        "authorityDid": owner_did,
                        "datasetId": owner_dataset_id,
                        "entityPath": "events/installation-1001",
                        "componentSchemaId": "clip::installation-event",
                    },
                    "change": {"action": "set", "value": {
                        "installerDid": contractor_did,
                        "installedAt": "2026-09-30T10:00:00Z",
                        "status": "complete",
                    }},
                    "expectedSequence": 0,
                    "schemaDigest": owner_registration["schemaDigest"],
                    "created": "2026-10-01T00:01:00Z",
                    "proof": {
                        "type": "DataIntegrityProof",
                        "cryptosuite": "eddsa-jcs-2022",
                        "created": "2026-10-01T00:01:00Z",
                        "verificationMethod": contractor_method,
                        "proofPurpose": "assertionMethod",
                        "proofValue": "z" + "1" * 86,
                    },
                })
                signed_proposal = self._sign_model(
                    proposal_model,
                    contractor_key,
                    contractor_method,
                    "assertionMethod",
                )
                proposal = self._post(owner_port, "/ifc/v1/proposals", signed_proposal, "owner-key")

                owner_key = NodeKeyManager.load_or_create(str(root / "owner.key"))
                owner_method = f"{owner_did}#authority-key"
                decision_model = IfcDecisionTransaction.model_validate({
                    "actorDid": owner_did,
                    "decision": "accept",
                    "proposalId": proposal["proposalId"],
                    "proposalDigest": proposal["proposalDigest"],
                    "expectedSequence": 0,
                    "created": "2026-10-01T00:01:01Z",
                    "proof": {
                        "type": "DataIntegrityProof",
                        "cryptosuite": "eddsa-jcs-2022",
                        "created": "2026-10-01T00:01:01Z",
                        "verificationMethod": owner_method,
                        "proofPurpose": "capabilityInvocation",
                        "proofValue": "z" + "1" * 86,
                    },
                })
                signed_decision = self._sign_model(
                    decision_model,
                    owner_key,
                    owner_method,
                    "capabilityInvocation",
                )
                receipt = self._post(owner_port, "/ifc/v1/decisions", signed_decision, "owner-key")
                self.assertTrue(receipt["accepted"])
                self.assertEqual(receipt["sequence"], 1)
                event = self._get_component(
                    owner_port,
                    owner_dataset_id,
                    "events/installation-1001",
                    "clip::installation-event",
                )
                self.assertEqual(event["installerDid"], contractor_did)
                self.assertEqual(event["status"], "complete")

                self._wait_for_peer_health(owner_port, "owner-key", peer_dids, owner_did)
                relay_port = ports[-1]
                acknowledgement = self._post(owner_port, "/clip/v1/replication/push", {"peerDid": peer_dids["relay"], "datasetId": owner_dataset_id}, "owner-key")
                relay_document = httpx.get(f"http://127.0.0.1:{relay_port}/.well-known/did.json").json()
                self.assertTrue(verify_clip_message_proof(acknowledgement, relay_document))
                self.assertEqual(acknowledgement["payload"]["sequence"], 1)
                retry = self._post(owner_port, "/clip/v1/replication/push", {"peerDid": peer_dids["relay"], "datasetId": owner_dataset_id}, "owner-key")
                self.assertEqual(retry["payload"]["digest"], acknowledgement["payload"]["digest"])

                self._post(owner_port, "/clip/v1/node/offline", {"offline": True}, "owner-key")
                self.assertEqual(httpx.get(f"http://127.0.0.1:{owner_port}/ifc/v1/datasets").status_code, 503)
                self._post(owner_port, "/clip/v1/node/offline", {"offline": False}, "owner-key")
                self._wait_for_peer_health(owner_port, "owner-key", peer_dids, owner_did)

                owner_process = processes[3]
                owner_process.terminate()
                owner_process.wait(timeout=10)
                processes[3] = subprocess.Popen(owner_process.args, cwd=Path(__file__).resolve().parents[1], env=environments[3], stdout=logs[3], stderr=subprocess.STDOUT)
                self._wait_for_did_documents([owner_port], [processes[3]])
                restarted_document = httpx.get(f"http://127.0.0.1:{owner_port}/.well-known/did.json").json()
                self.assertTrue(verify_clip_message_proof(receipt, restarted_document))
                self.assertEqual(self._get_component(owner_port, owner_dataset_id, "events/installation-1001", "clip::installation-event")["status"], "complete")
                restarted_ack = self._post(owner_port, "/clip/v1/replication/push", {"peerDid": peer_dids["relay"], "datasetId": owner_dataset_id}, "owner-key")
                self.assertEqual(restarted_ack["payload"]["digest"], acknowledgement["payload"]["digest"])

                with sqlite3.connect(root / "owner.db") as database:
                    database.execute("UPDATE clip_import_cache SET content = ?, fetched_at = ?", (b"corrupt cache", "2000-01-01 00:00:00"))
                self.assertEqual(self._get_component(owner_port, owner_dataset_id, "building/door-1", "ifc::name"), product)
                with sqlite3.connect(root / "owner.db") as database:
                    content = database.execute("SELECT content FROM clip_import_cache").fetchone()[0]
                    self.assertNotEqual(content, b"corrupt cache")

                from node.app.imports.ifcx import construction_schemas, EVIDENCE_SCHEMA
                evidence_dataset = {"header": {"id": "urn:owner:evidence", "ifcxVersion": "ifcx_alpha", "dataVersion": "1.0.0", "author": owner_did, "timestamp": "2026-10-01T00:00:00Z"}, "imports": [], "schemas": construction_schemas(), "data": [{"path": "inspection"}]}
                self._post(owner_port, "/ifc/v1/datasets", {"file": evidence_dataset, "trustedProposers": []}, "owner-key")
                evidence = self._post(owner_port, "/clip/v1/evidence/upload", {
                    "target": {"authorityDid": owner_did, "datasetId": "urn:owner:evidence", "entityPath": "inspection", "componentSchemaId": EVIDENCE_SCHEMA},
                    "name": "inspection.txt", "content": base64.b64encode(b"completed inspection" * 100).decode(), "recipients": [owner_did], "retentionSeconds": 3600, "chunkSize": 1024,
                }, "owner-key")
                evidence_id = evidence["manifest"]["evidenceId"]
                placements = self._post(owner_port, f"/clip/v1/evidence/{evidence_id}/replicate", {"peerDid": peer_dids["relay"]}, "owner-key")
                self.assertEqual(len(placements["receipts"]), evidence["manifest"]["manifest"]["fragmentCount"])
                (root / "documents" / "owner" / "encrypted" / evidence_id / "0.bin").unlink()
                repair = self._post(owner_port, f"/clip/v1/evidence/{evidence_id}/repair", {"peerDid": peer_dids["relay"]}, "owner-key")
                self.assertEqual(repair["repaired"], [0])
                print(
                    "verified IFCX owner asset type="
                    f"{product!r}, installation={event['status']}, "
                    f"accepted_sequence={receipt['sequence']}, peers={len(peer_dids) - 1}"
                )
            except Exception:
                for log in logs:
                    log.flush()
                    log.seek(0)
                    print(log.read()[-3000:])
                raise
            finally:
                for process in processes:
                    process.terminate()
                for process in processes:
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=5)
                for log in logs:
                    log.close()

    def _wait_for_did_documents(self, ports: list[int], processes: list[subprocess.Popen]) -> None:
        deadline = time.monotonic() + 45
        for port, process in zip(ports, processes):
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Node {port} exited with {process.returncode}")
                try:
                    response = httpx.get(f"http://127.0.0.1:{port}/.well-known/did.json", timeout=1)
                    if response.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(f"DID document on node {port} did not become ready")

    def _wait_for_peer_health(
        self,
        owner_port: int,
        owner_key: str,
        expected_dids: dict[str, str],
        owner_did: str,
    ) -> None:
        expected = set(expected_dids.values()) - {owner_did}
        deadline = time.monotonic() + 35
        last_peers = []
        while time.monotonic() < deadline:
            response = httpx.get(
                f"http://127.0.0.1:{owner_port}/clip/v1/network/gossip/peers",
                headers={"x-api-key": owner_key},
                timeout=2,
            )
            response.raise_for_status()
            last_peers = response.json()
            known = {peer["did"] for peer in last_peers}
            manufacturer = next(
                (peer for peer in last_peers if peer["did"] == expected_dids["manufacturer"]),
                None,
            )
            if expected.issubset(known) and manufacturer and manufacturer["status"] == "alive":
                return
            time.sleep(0.25)
        raise AssertionError(f"DID gossip did not converge; owner peers={last_peers}")

    @staticmethod
    def _get_component(port: int, dataset_id: str, entity_path: str, schema_id: str):
        response = httpx.get(
            f"http://127.0.0.1:{port}/ifc/v1/datasets/{quote(dataset_id, safe='')}/components",
            params={"entity_path": entity_path, "component_schema_id": schema_id},
            timeout=10,
        )
        response.raise_for_status()
        return response.json()["value"]

    @staticmethod
    def _sign_model(model, key: NodeKeyManager, method: str, purpose: str) -> dict:
        unsigned = model.model_dump(mode="json", by_alias=True, exclude={"proof"})
        return add_data_integrity_proof(
            unsigned,
            key.private_key_bytes,
            verification_method=method,
            proof_purpose=purpose,
            created=datetime.now(timezone.utc),
        )

    @staticmethod
    def _post(port: int, path: str, body: dict, api_key: str) -> dict:
        response = httpx.post(
            f"http://127.0.0.1:{port}{path}",
            json=body,
            headers={"x-api-key": api_key},
            timeout=15,
        )
        if not response.is_success:
            raise AssertionError(
                f"POST {path} on {port} returned {response.status_code}: {response.text}"
            )
        return response.json()


if __name__ == "__main__":
    unittest.main()