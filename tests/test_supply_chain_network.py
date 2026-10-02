"""Real HTTP supply-chain workflows between isolated organisation authorities."""

import base64
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from urllib.parse import quote

import httpx
from node.app.core.data_integrity import verify_data_integrity_proof
from node.app.core.did import resolve_ed25519_verification_key


class AuthorityNetwork:
    def __init__(self, roles: tuple[str, ...]):
        self.roles = roles
        self.directory = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.directory.name)
        self.processes: dict[str, subprocess.Popen] = {}
        self.logs = {}
        self.clients: dict[str, httpx.Client] = {}
        self.ports: dict[str, int] = {}
        self.environments: dict[str, dict[str, str]] = {}
        reservations = []
        try:
            for role in roles:
                reservation = socket.socket()
                reservation.bind(("127.0.0.1", 0))
                self.ports[role] = reservation.getsockname()[1]
                reservations.append(reservation)
        finally:
            for reservation in reservations:
                reservation.close()

    def did(self, role: str) -> str:
        return f"did:web:127.0.0.1%3A{self.ports[role]}"

    def start(self):
        trusted = ",".join(self.did(role) for role in self.roles)
        try:
            for role in self.roles:
                log = open(self.root / f"{role}.log", "w+", encoding="utf-8")
                self.logs[role] = log
                environment = os.environ.copy()
                environment.update({
                    "PYTHONUNBUFFERED": "1",
                    "NODE_DOMAIN": f"127.0.0.1:{self.ports[role]}",
                    "NODE_API_BASE": f"http://127.0.0.1:{self.ports[role]}",
                    "API_KEY": f"{role}-test-key",
                    "DATABASE_URL": f"sqlite+aiosqlite:///{(self.root / f'{role}.db').as_posix()}",
                    "PRIVATE_KEY_FILE": str(self.root / f"{role}.key"),
                    "DOCUMENT_STORAGE_DIR": str(self.root / f"documents-{role}"),
                    "DID_WEB_ID": self.did(role),
                    "DID_VERIFICATION_METHOD": f"{self.did(role)}#authority-key",
                    "NODE_ROLE": role,
                    "CLIP_GOSSIP_ENABLED": "false",
                    "CLIP_GOSSIP_SEEDS": "",
                    "CLIP_ALLOW_HTTP_LOOPBACK": "true",
                    "CLIP_TRUSTED_PUBLISHERS": trusted,
                })
                self.environments[role] = environment
                self.processes[role] = subprocess.Popen(
                    [sys.executable, "-m", "uvicorn", "node.app.main:app",
                     "--host", "127.0.0.1", "--port", str(self.ports[role]),
                     "--log-level", "warning"],
                    cwd=Path(__file__).resolve().parents[1], env=environment,
                    stdout=log, stderr=subprocess.STDOUT,
                )
                self.clients[role] = httpx.Client(
                    base_url=f"http://127.0.0.1:{self.ports[role]}",
                    headers={"x-api-key": f"{role}-test-key"},
                    timeout=30, trust_env=False, follow_redirects=False,
                )
            deadline = time.monotonic() + 60
            remaining = set(self.roles)
            while remaining and time.monotonic() < deadline:
                for role in tuple(remaining):
                    if self.processes[role].poll() is not None:
                        raise RuntimeError(f"{role} exited during startup:\n{self.log(role)}")
                    try:
                        response = self.clients[role].get("/.well-known/did.json")
                        if response.status_code == 200 and response.json()["id"] == self.did(role):
                            remaining.remove(role)
                    except httpx.RequestError:
                        pass
                if remaining:
                    time.sleep(0.1)
            if remaining:
                raise TimeoutError(f"Authorities failed to start: {sorted(remaining)}")
            return self
        except (OSError, RuntimeError, TimeoutError, httpx.HTTPError):
            self.close()
            raise

    def log(self, role: str) -> str:
        self.logs[role].flush()
        return (self.root / f"{role}.log").read_text(encoding="utf-8")

    def restart(self, role: str):
        process = self.processes[role]
        process.terminate()
        process.wait(timeout=10)
        self.processes[role] = subprocess.Popen(
            process.args, cwd=Path(__file__).resolve().parents[1],
            env=self.environments[role], stdout=self.logs[role], stderr=subprocess.STDOUT,
        )
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            if self.processes[role].poll() is not None:
                raise RuntimeError(f"{role} failed to restart:\n{self.log(role)}")
            try:
                response = self.clients[role].get("/.well-known/did.json")
                if response.status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.1)
        raise TimeoutError(f"{role} did not become responsive after restart")

    def request(self, role: str, method: str, path: str, body=None, status=200):
        response = self.clients[role].request(method, path, json=body)
        if response.status_code != status:
            raise AssertionError(
                f"{role}: {method} {path}: expected {status}, got "
                f"{response.status_code}: {response.text}\n{self.log(role)}"
            )
        return response.json()

    def close(self):
        for client in self.clients.values():
            client.close()
        for process in self.processes.values():
            if process.poll() is None:
                process.terminate()
        for process in self.processes.values():
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)
        for log in self.logs.values():
            log.close()
        self.directory.cleanup()

    def __enter__(self):
        return self.start()

    def __exit__(self, *_):
        self.close()


@unittest.skipUnless(os.getenv("CLIP_INTEGRATION") == "1", "set CLIP_INTEGRATION=1")
class SupplyChainNetworkTest(unittest.TestCase):
    def test_recursive_sourcing_installation_handover_documents_and_corrections(self):
        prefix = "/clip/v1/supply-chain"
        with AuthorityNetwork(("manufacturer", "supplier", "reseller", "installer", "contractor", "client")) as network:
            documents = {role: network.request(role, "GET", "/.well-known/did.json") for role in network.roles}

            def verified(value, role, purpose="assertionMethod"):
                proof = value["proof"]
                key = resolve_ed25519_verification_key(documents[role], expected_did=network.did(role),
                    verification_method=proof["verificationMethod"], proof_purpose=purpose)
                return proof["proofPurpose"] == purpose and verify_data_integrity_proof(value, key)

            def call(role, method, path, body=None, status=200):
                return network.request(role, method, prefix + path, body, status)

            def source(value, **fields):
                return {"authorityDid": value["authorityDid"], "recordId": value["id"],
                        "revision": value["revision"], **fields}

            def create(role, kind, name, sources=None, **fields):
                return call(role, "POST", "/records", {
                    "kind": kind, "name": name, "ifcClass": "IfcPumpType",
                    "sources": sources or [], **fields,
                }, 201)

            def publish(role, value):
                result = call(role, "POST", f"/records/{value['id']}/publish",
                              {"expectedRevision": value["revision"]})
                self.assertTrue(verified(result, role))
                return result

            def project(role, sender):
                value = network.request(role, "POST", "/clip/v1/projects",
                                        {"name": f"{role} private project"}, 201)
                # Submitters need only scoped sender approval, not project Contributor access.
                call(role, "PUT", f"/projects/{quote(value['projectId'], safe='')}/senders",
                     {"expectedRevision": 0, "senders": [network.did(sender)]})
                call(sender, "POST", "/projects/connect",
                     {"authorityDid": network.did(role), "projectId": value["projectId"]})
                return value

            def submit(sender, recipient, destination, values, document_ids=None, supersedes=None):
                draft = call(sender, "POST", "/submissions", {
                    "recipientDid": network.did(recipient), "projectId": destination["projectId"],
                    "recordIds": [value["id"] for value in values], "documentIds": document_ids or [],
                    "supersedes": supersedes, "idempotencyKey": str(time.monotonic_ns()),
                }, 201)
                body = {"expectedRevision": draft["revision"], "idempotencyKey": draft["id"] + "-issue"}
                issued = call(sender, "POST", f"/submissions/{draft['id']}/issue", body)
                self.assertEqual(issued["deliveryStatus"], "delivered")
                self.assertTrue(verified(issued["issue"], sender))
                self.assertEqual(call(sender, "POST", f"/submissions/{draft['id']}/issue", body)["issue"], issued["issue"])
                received = call(recipient, "GET", f"/submissions/{draft['id']}")
                self.assertEqual(received["issue"], issued["issue"])
                return received

            def decide(recipient, value, decision="accept", reason=""):
                body = {"decision": decision, "reason": reason, "expectedRevision": value["revision"],
                        "idempotencyKey": value["id"] + "-decision"}
                result = call(recipient, "POST", f"/submissions/{value['id']}/decision", body)
                self.assertTrue(verified(result["decision"], recipient, "capabilityInvocation"))
                repeated = call(recipient, "POST", f"/submissions/{value['id']}/decision", body)
                self.assertEqual(repeated["acceptedRecords"], result["acceptedRecords"])
                return result

            motor = publish("manufacturer", create("manufacturer", "product", "Motor"))
            call("supplier", "POST", "/catalogue/discover", {"authorityDid": network.did("manufacturer")})
            motor_offer = publish("supplier", create("supplier", "offering", "Supplied motor", [source(motor)]))
            call("manufacturer", "POST", "/catalogue/discover", {"authorityDid": network.did("supplier")})
            pump = create("manufacturer", "product", "Pump assembly",
                          [source(motor_offer, quantity=1, unit="each")],
                          data={"model": "P-100", "ratedPower": 5.5})
            datasheet = call("manufacturer", "POST", f"/records/{pump['id']}/documents", {
                "name": "datasheet.txt", "mediaType": "text/plain",
                "content": base64.b64encode(b"Original manufacturer data").decode(),
                "visibility": "public", "expectedRevision": pump["revision"],
            }, 201)
            pump["revision"] = datasheet["recordRevision"]
            pump_v1 = publish("manufacturer", pump)
            call("supplier", "POST", "/catalogue/discover", {"authorityDid": network.did("manufacturer")})
            pump_offer = publish("supplier", create("supplier", "offering", "Pump offering", [source(pump_v1)]))
            call("reseller", "POST", "/catalogue/discover", {"authorityDid": network.did("supplier")})
            reseller_offer = publish("reseller", create("reseller", "offering", "Reseller pump", [source(pump_offer)]))
            self.assertIn(network.did("manufacturer"), {entry["authorityDid"] for entry in reseller_offer["lineage"]})
            self.assertIn(network.did("supplier"), {entry["authorityDid"] for entry in reseller_offer["lineage"]})

            installer_project = project("installer", "reseller")
            delivery = create("reseller", "supply", "Private project delivery", [source(reseller_offer)],
                              projectId=installer_project["projectId"],
                              data={"quantity": 4, "unit": "each", "serials": ["A", "B", "C", "D"],
                                    "deliveryAddress": "Restricted project address"})
            delivery_document = call("reseller", "POST", f"/records/{delivery['id']}/documents", {
                "name": "delivery.txt", "mediaType": "text/plain",
                "content": base64.b64encode(b"Private delivery receipt").decode(),
                "visibility": "private", "expectedRevision": delivery["revision"],
            }, 201)
            delivery["revision"] = delivery_document["recordRevision"]
            issue = submit("reseller", "installer", installer_project, [delivery], [delivery_document["id"]])
            accepted_supply = decide("installer", issue)["acceptedRecords"][0]
            self.assertEqual(accepted_supply["authorityDid"], network.did("installer"))
            doc_response = network.clients["installer"].get(
                prefix + f"/records/{accepted_supply['id']}/documents/{delivery_document['id']}")
            self.assertEqual(doc_response.status_code, 200, doc_response.text)
            self.assertEqual(doc_response.content, b"Private delivery receipt")
            public = network.clients["reseller"].get(prefix + "/catalogue", headers={"x-api-key": ""})
            self.assertEqual(public.status_code, 200)
            self.assertNotIn("Restricted project address", public.text)
            self.assertNotIn(delivery_document["id"], public.text)
            unauthorized = network.clients["installer"].get(prefix + "/records", headers={"x-api-key": ""})
            self.assertEqual(unauthorized.status_code, 401)

            installed = create("installer", "installation", "Plant room pump",
                               [source(accepted_supply, quantity=2, unit="each", serials=["A", "B"])],
                               ifcClass="IfcPump", projectId=installer_project["projectId"],
                               data={"location": "Plant room", "status": "complete",
                                     "installedAt": "2026-10-02T12:00:00Z"})
            call("installer", "POST", "/records", {
                "kind": "installation", "name": "Duplicate serial", "ifcClass": "IfcPump",
                "projectId": installer_project["projectId"],
                "sources": [source(accepted_supply, quantity=1, unit="each", serials=["A"])],
            }, 409)
            call("installer", "POST", "/records", {
                "kind": "installation", "name": "Over allocation", "ifcClass": "IfcPump",
                "projectId": installer_project["projectId"],
                "sources": [source(accepted_supply, quantity=3, unit="each", serials=["C", "D"])],
            }, 409)
            main_project = project("contractor", "installer")
            main_issue = submit("installer", "contractor", main_project, [installed])
            main_asset = decide("contractor", main_issue)["acceptedRecords"][0]
            client_project = project("client", "contractor")
            client_issue = submit("contractor", "client", client_project, [main_asset])
            client_asset = decide("client", client_issue)["acceptedRecords"][0]
            self.assertEqual(client_asset["kind"], "asset")
            self.assertEqual(client_asset["acceptedFrom"]["snapshot"]["acceptedFrom"]["snapshot"]["authorityDid"],
                             network.did("installer"))
            self.assertIn(network.did("manufacturer"), {entry["authorityDid"] for entry in client_asset["lineage"]})
            document_path = prefix + f"/documents/{delivery_document['id']}"
            document_params = {"authorityDid": network.did("reseller"), "projectId": client_project["projectId"]}
            restricted = network.clients["client"].get(document_path, params=document_params)
            self.assertNotEqual(restricted.status_code, 200)
            grant = call("reseller", "POST",
                         f"/records/{delivery['id']}/documents/{delivery_document['id']}/grants", {
                             "recipientDid": network.did("client"), "active": True,
                             "expectedRevision": 0,
                         })
            granted = network.clients["client"].get(document_path, params=document_params)
            self.assertEqual(granted.status_code, 200, granted.text)
            self.assertEqual(granted.content, b"Private delivery receipt")
            call("reseller", "POST",
                 f"/records/{delivery['id']}/documents/{delivery_document['id']}/grants", {
                     "recipientDid": network.did("client"), "active": False,
                     "expectedRevision": grant["revision"],
                 })
            revoked = network.clients["client"].get(document_path, params=document_params)
            self.assertNotEqual(revoked.status_code, 200)

            pump_changed = call("manufacturer", "PUT", f"/records/{pump['id']}", {
                "kind": "product", "name": "Pump assembly revision 2", "ifcClass": "IfcPumpType",
                "sources": [source(motor_offer, quantity=1, unit="each")],
                "data": {"model": "P-100", "ratedPower": 7.5}, "expectedRevision": pump["revision"],
            })
            publish("manufacturer", pump_changed)
            from node.app.core.ifc_product_resolver import PRODUCT_DATA_SCHEMA, COMPONENTS_SCHEMA
            graph_url = f"/ifc/v1/datasets/{quote(client_project['projectId'], safe='')}/graph"
            current_graph = network.request("client", "GET", graph_url + "?refresh_products=true")
            resolved_types = current_graph["productResolution"]["definitions"]
            self.assertEqual(len(resolved_types), 2)
            shared_pump = next(item for item in resolved_types if item["recordId"] == pump["id"])
            shared_motor = next(item for item in resolved_types if item["recordId"] == motor["id"])
            self.assertEqual(current_graph["entities"][shared_pump["path"]]["components"][COMPONENTS_SCHEMA],
                             [shared_motor["path"]])
            self.assertEqual(current_graph["effectiveComponents"][client_asset["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["ratedPower"], 7.5)
            pinned_graph = network.request("client", "GET", graph_url + "?product_view=pinned")
            self.assertEqual(pinned_graph["effectiveComponents"][client_asset["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["ratedPower"], 5.5)
            pinned = call("manufacturer", "GET", f"/catalogue/{pump['id']}/revisions/{pump_v1['revision']}")
            self.assertEqual(pinned["data"]["ratedPower"], 5.5)
            self.assertEqual(call("supplier", "GET", f"/records/{pump_offer['id']}")["sources"][0]["revision"],
                             pump_v1["revision"])
            network.restart("manufacturer")
            persisted = call("manufacturer", "GET", f"/catalogue/{pump['id']}/revisions/{pump_v1['revision']}")
            self.assertEqual(persisted, pinned)
            self.assertTrue(verified(persisted, "manufacturer"))
            network.restart("client")
            persisted_asset = call("client", "GET", f"/records/{client_asset['id']}")
            self.assertEqual(persisted_asset["acceptedFrom"], client_asset["acceptedFrom"])
            restarted_graph = network.request("client", "GET", graph_url)
            self.assertEqual(restarted_graph["effectiveComponents"][client_asset["graphPath"]][PRODUCT_DATA_SCHEMA]["data"]["ratedPower"], 7.5)

            revised_issue = submit("installer", "contractor", main_project, [installed], supersedes=main_issue["id"])
            requested = decide("contractor", revised_issue, "request-changes", "Provide commissioning evidence")
            self.assertEqual(requested["status"], "changes-requested")
            corrected = submit("installer", "contractor", main_project, [installed], supersedes=revised_issue["id"])
            self.assertEqual(corrected["issue"]["supersedes"], revised_issue["id"])
            decide("contractor", corrected, "reject", "Commissioning evidence still missing")
            for role in ("manufacturer", "supplier", "reseller", "installer", "contractor", "client"):
                records = call(role, "GET", "/records")["items"]
                graph = network.request(role, "GET",
                    f"/ifc/v1/datasets/{quote(records[0]['datasetId'], safe='')}/graph")
                self.assertIn(records[0]["graphPath"], graph["entities"])
                history = network.request(role, "GET",
                    f"/ifc/v1/datasets/{quote(records[0]['datasetId'], safe='')}/history")
                self.assertGreater(len(history["items"]), 0)
