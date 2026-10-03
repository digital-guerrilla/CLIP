"""Exercise the actual demo seeder without touching the running demo's data."""

import json
import io
import os
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch

import httpx

from examples import clip_network_demo as demo


@contextmanager
def demo_directory():
    directory = tempfile.TemporaryDirectory()
    try:
        yield directory.name
    finally:
        for attempt in range(20):
            try:
                directory.cleanup()
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.5)


class DemoDefinitionTest(unittest.TestCase):
    def test_seeding_starts_after_api_readiness_without_waiting_for_gossip(self):
        with patch.object(demo, "wait_for_nodes") as ready, patch.object(
            demo, "wait_for_gossip", side_effect=TimeoutError("manufacturer still unknown")
        ) as gossip, patch.object(
            demo, "seed_products", side_effect=RuntimeError("catalogue started")
        ):
            with self.assertRaisesRegex(RuntimeError, "catalogue started"):
                demo._seed()
        ready.assert_called_once()
        gossip.assert_not_called()

    def test_gossip_verification_waits_for_unknown_manufacturer_to_become_alive(self):
        peers = [{"did": demo.peer_did(role), "status": "alive"} for role in demo.NODES if role != "owner"]
        pending = [{**peer, "status": "unknown" if peer["did"] == demo.peer_did("manufacturer") else "alive"}
                   for peer in peers]
        responses = [Mock(), Mock()]
        responses[0].json.return_value = pending
        responses[1].json.return_value = peers
        with patch.object(demo.httpx, "get", side_effect=responses), patch.object(
            demo.time, "sleep"
        ), patch.object(demo, "seed_progress") as report:
            result = demo.wait_for_gossip()
        self.assertEqual(result, peers)
        self.assertTrue(any("manufacturer unknown" in call.args[0] for call in report.call_args_list))

    def test_node_readiness_retries_http_failure_and_checks_every_authority(self):
        failed = httpx.Response(503, request=httpx.Request("GET", "http://127.0.0.1"))
        ready = [httpx.Response(200, json={"id": demo.peer_did(role)},
                               request=httpx.Request("GET", demo.node_url(role))) for role in demo.NODES]
        with patch.object(demo.httpx, "get", side_effect=[failed, *ready]) as get, patch.object(
            demo.time, "sleep"
        ):
            demo.wait_for_nodes()
        self.assertEqual(get.call_count, len(demo.NODES) + 1)

    def test_progress_reports_elapsed_work_without_early_success(self):
        output = io.StringIO()
        with patch.object(demo.time, "monotonic", return_value=0):
            progress = demo.SeedProgress(2, output)
        with patch.object(demo.time, "monotonic", return_value=65):
            progress.update("First installation", advance=True, force=True)
            progress.update("Verifying", advance=True, force=True)
            progress.render(force=True, outcome="Complete")
        lines = output.getvalue().splitlines()
        self.assertIn("50% 1/2 steps | 01:05", lines[0])
        self.assertIn("99% 2/2 steps", lines[1])
        self.assertIn("100% 2/2 steps", lines[2])
        self.assertNotIn("\r", output.getvalue())

    def test_interactive_progress_reuses_line_and_logs_are_throttled(self):
        output = io.StringIO()
        with patch.object(output, "isatty", return_value=True):
            progress = demo.SeedProgress(2, output)
        progress.update("Long description", force=True)
        progress.update("Short", advance=True, force=True)
        progress.render(force=True, outcome="Stopped")
        self.assertEqual(output.getvalue().count("\r"), 3)
        self.assertEqual(output.getvalue().count("\n"), 1)
        self.assertIn("Stopped: Short", output.getvalue())
        log = io.StringIO()
        reporter = demo.SeedProgress(2, log)
        reporter.update("Starting")
        reporter.update("Working", advance=True)
        self.assertEqual(len(log.getvalue().splitlines()), 1)
        self.assertEqual(reporter.completed, 1)

    def test_seed_failure_never_reports_complete_and_resets_reporter(self):
        output = io.StringIO()
        with patch.object(demo.sys, "stderr", output), patch.object(
            demo, "_seed", side_effect=RuntimeError("API rejected")
        ):
            with self.assertRaisesRegex(RuntimeError, "API rejected"):
                demo.seed()
        self.assertIn("Stopped", output.getvalue())
        self.assertNotIn("100%", output.getvalue())
        self.assertIsNone(demo._SEED_PROGRESS.get())

    def test_seed_success_reports_complete_only_after_all_work(self):
        output = io.StringIO()
        def complete():
            progress = demo._SEED_PROGRESS.get()
            assert progress is not None
            for _ in range(progress.total):
                demo.seed_progress("Verified", advance=True)
        with patch.object(demo.sys, "stderr", output), patch.object(demo, "_seed", side_effect=complete):
            demo.seed()
        self.assertIn("100% 223/223 steps", output.getvalue())
        self.assertIsNone(demo._SEED_PROGRESS.get())

    def test_seed_incomplete_count_cannot_claim_success(self):
        output = io.StringIO()
        with patch.object(demo.sys, "stderr", output), patch.object(demo, "_seed"):
            with self.assertRaisesRegex(RuntimeError, "Seed progress mismatch"):
                demo.seed()
        self.assertNotIn("100%", output.getvalue())
        self.assertIn("Stopped", output.getvalue())
        self.assertIsNone(demo._SEED_PROGRESS.get())

    def test_product_catalogue_progress_counts_all_eight_products(self):
        def record(role, kind, name, **fields):
            return {"authorityDid": demo.peer_did(role), "id": name, "revision": 1,
                    "name": name, "ifcClass": fields["ifc_class"]}
        with patch.object(demo, "create_record", side_effect=record), patch.object(
            demo, "publish_record", side_effect=lambda role, value: value
        ), patch.object(demo, "post", return_value={"recordRevision": 2}), patch.object(
            demo, "seed_progress"
        ) as report:
            products = demo.seed_products()
        self.assertEqual(len(products), 8)
        self.assertEqual(sum(call.kwargs.get("advance", False) for call in report.call_args_list), 8)

    def test_actual_portfolio_progress_accounts_for_offers_deliveries_and_installations(self):
        output = io.StringIO()
        progress = demo.SeedProgress(223, output)
        token = demo._SEED_PROGRESS.set(progress)
        products = {key: {"name": key, "authorityDid": demo.peer_did("manufacturer"),
                         "id": key, "revision": 1, "ifcClass": "IfcPumpType"}
                    for key in ("door", "pump", "motor", "sensor", "controller", "valve", "fan", "filter")}
        def record(role, kind, name, **fields):
            return {"authorityDid": demo.peer_did(role), "id": name, "revision": 1,
                    "kind": kind, "name": name, "graphPath": name,
                    "ifcClass": fields["ifc_class"], **fields}
        try:
            with patch.object(demo, "post", return_value={}), patch.object(demo, "put"), patch.object(
                demo, "receiving_project", return_value={"projectId": "project", "revision": 1}
            ), patch.object(demo, "create_record", side_effect=record), patch.object(
                demo, "publish_record", side_effect=lambda role, value: value
            ), patch.object(demo, "submit_and_accept", side_effect=lambda sender, recipient, project, value: {
                "acceptedRecord": value, "submissionId": value["id"],
            }), patch.object(demo, "connect_installations"), patch.object(
                demo, "commit_graph_operations"
            ), patch("builtins.print"):
                demo.seed_portfolio({}, products)
        finally:
            demo._SEED_PROGRESS.reset(token)
        self.assertEqual(progress.completed, 56 + 6 * 25)

    def test_portfolio_locations_are_unique_and_references_resolve(self):
        nodes = demo.portfolio_locations()
        paths = {node["path"] for node in nodes}
        self.assertEqual(len(nodes), 60)
        self.assertEqual(len(paths), len(nodes))
        self.assertEqual(sum(node["attributes"][demo.SOURCE_SCHEMA]["class"] == "IfcSite" for node in nodes), 6)
        self.assertEqual(sum(node["attributes"][demo.SOURCE_SCHEMA]["class"] == "IfcSpace" for node in nodes), 36)
        for node in nodes:
            for child in node.get("children", {}).values():
                self.assertIn(child, paths)

    def test_graph_operations_respect_protocol_batch_limit_and_advance_sequence(self):
        operations = [{"action": "create", "node": {"path": f"events/{index}"}} for index in range(65)]
        datasets = Mock()
        datasets.json.return_value = {"items": [{"datasetId": "project", "schemaDigest": "a" * 64}]}
        history = Mock()
        history.json.return_value = {"items": [{"receipt": {"sequence": 5}}]}
        with patch.object(demo, "request", side_effect=[datasets, history]), patch.object(
            demo, "commit_graph_batch", side_effect=[6, 7, 8]
        ) as commit:
            demo.commit_graph_operations("project", operations, "inspector")
        self.assertEqual([len(call.args[1]) for call in commit.call_args_list], [32, 32, 1])
        self.assertEqual([call.args[3] for call in commit.call_args_list], [5, 6, 7])
        self.assertTrue(all(call.args[2] == "inspector" for call in commit.call_args_list))


@unittest.skipUnless(os.getenv("CLIP_INTEGRATION") == "1", "set CLIP_INTEGRATION=1")
class DemoGraphTest(unittest.TestCase):
    def test_shared_types_and_direct_supplier_installations(self):
        root = Path(__file__).resolve().parents[1]
        with demo_directory() as directory:
            data = Path(directory)
            sockets = [socket.socket() for _ in demo.NODES]
            try:
                for listener in sockets:
                    listener.bind(("127.0.0.1", 0))
                nodes = dict(zip(demo.NODES, [listener.getsockname()[1] for listener in sockets]))
            finally:
                for listener in sockets:
                    listener.close()
            processes = []
            logs = []
            with patch.object(demo, "NODES", nodes), patch.object(demo, "DATA", data), patch.object(
                demo, "STATE_PATH", data / "state.json"
            ):
                seeds = ",".join(demo.peer_did(role) for role in nodes)
                try:
                    for role, port in nodes.items():
                        log = open(data / f"{role}.log", "w+", encoding="utf-8")
                        logs.append(log)
                        env = {
                            **os.environ,
                            "NODE_DOMAIN": f"127.0.0.1:{port}",
                            "NODE_API_BASE": demo.node_url(role),
                            "DID_WEB_ID": demo.peer_did(role),
                            "DID_VERIFICATION_METHOD": f"{demo.peer_did(role)}#authority-key",
                            "DATABASE_URL": f"sqlite+aiosqlite:///{(data / f'{role}.db').as_posix()}",
                            "PRIVATE_KEY_FILE": str(data / f"{role}.key"),
                            "DOCUMENT_STORAGE_DIR": str(data / "documents" / role),
                            "CLIP_DEMO_OPEN_ACCESS": "true",
                            "CLIP_GOSSIP_ENABLED": "true",
                            "CLIP_GOSSIP_SEEDS": seeds,
                            "CLIP_GOSSIP_INTERVAL": "15",
                            "CLIP_TRUSTED_PUBLISHERS": seeds,
                            "CLIP_ALLOW_HTTP_LOOPBACK": "true",
                            "NODE_ROLE": "manufacturer" if role == "component_manufacturer" else (
                                "supplier" if role in demo.SUPPLIERS else role),
                        }
                        processes.append(subprocess.Popen(
                            [sys.executable, "-m", "uvicorn", "node.app.main:app",
                             "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                            cwd=root, env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
                        ))
                    demo.wait_for_nodes()
                    demo.seed()
                    state = json.loads(demo.STATE_PATH.read_text(encoding="utf-8"))
                    with httpx.Client(timeout=30) as client:
                        datasets = client.get(f"{demo.node_url('owner')}/ifc/v1/datasets")
                        datasets.raise_for_status()
                        graphs = []
                        for dataset in datasets.json()["items"]:
                            response = client.get(
                                f"{demo.node_url('owner')}/ifc/v1/datasets/"
                                f"{demo.quote(dataset['datasetId'], safe='')}/graph",
                                params={"refresh_products": "true"},
                            )
                            response.raise_for_status()
                            graphs.append(response.json())
                    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const dashboard=fs.readFileSync('node\\app\\static\\dashboard.js','utf8');
const context=vm.createContext();
vm.runInContext(dashboard.slice(0,dashboard.indexOf('function activateView')),context);
context.graphs=JSON.parse(fs.readFileSync(0,'utf8'));
context.showcaseProjectId=process.argv[1];
console.log(vm.runInContext(`state.graphs=graphs;
const network=entityNetworkData(),pump=network.entries.find(entry=>entry.label==='Primary Pump Installation');
const project=graphs.find(graph=>graph.datasetId===showcaseProjectId);
state.scope=project.datasetId;
const scoped=entityNetworkData();
const compact=entry=>({...entry,parent:null,contributions:[],
  graph:{authorityDid:entry.graph.authorityDid,datasetId:entry.graph.datasetId,
    entities:{[entry.path]:entry.graph.entities[entry.path]},
    effectiveComponents:{[entry.path]:entry.graph.effectiveComponents[entry.path]}}});
const lineage=lineageNetworkData(network,pump.key);
JSON.stringify({...network,entries:network.entries.map(compact),lineage:{...lineage,entries:lineage.entries.map(compact)},
  flow:network.links.map(entityFlowLink),scoped:scoped.entries.map(entry=>entry.label)})`,context));
"""
                    result = subprocess.run(
                        ["node", "-e", script, state["showcaseProjectId"]], cwd=root, input=json.dumps(graphs),
                        text=True, capture_output=True, check=True,
                    )
                    network = json.loads(result.stdout)
                    for record_id in (state["manufacturerDoorId"], state["manufacturerPumpId"]):
                        types = [
                            entry for entry in network["entries"]
                            if entry["graph"]["entities"][entry["path"]]["components"].get(
                                demo.IDENTITY_SCHEMA, {}
                            ).get("recordId") == record_id
                        ]
                        self.assertEqual(len(types), 1)
                        self.assertEqual(types[0]["kind"], "type")
                        links = [link for link in network["links"] if link["kind"] == "Type"
                                 and link["target"] == types[0]["key"]]
                        self.assertGreater(len(links), 3)
                    canonical = [
                        entry for entry in network["entries"]
                        if demo.IDENTITY_SCHEMA in entry["graph"]["entities"][entry["path"]]["components"]
                        and entry["graph"]["entities"][entry["path"]]["components"].get(
                            "urn:clip:construction:source:v1"
                        )
                    ]
                    classes = [
                        entry["graph"]["entities"][entry["path"]]["components"][
                            "urn:clip:construction:source:v1"
                        ]["class"]
                        for entry in canonical
                    ]
                    self.assertEqual(classes.count("IfcPumpType"), 1)
                    self.assertEqual(classes.count("IfcDoorType"), 1)
                    self.assertEqual(classes.count("IfcElectricMotorType"), 1)
                    self.assertEqual(len(canonical), 8)
                    publishers = {
                        entry["graph"]["entities"][entry["path"]]["components"][demo.IDENTITY_SCHEMA]["authorityDid"]
                        for entry in canonical
                    }
                    self.assertEqual(publishers, {
                        demo.peer_did("manufacturer"), demo.peer_did("component_manufacturer"),
                    })
                    motor = next(entry for entry in canonical if entry["label"] == "Aster Motor M-5")
                    self.assertEqual(
                        motor["graph"]["entities"][motor["path"]]["components"][demo.IDENTITY_SCHEMA]["authorityDid"],
                        demo.peer_did("component_manufacturer"),
                    )
                    self.assertEqual(
                        motor["graph"]["effectiveComponents"][motor["path"]][
                            "urn:clip:construction:manufacturer-data:v1"
                        ]["data"]["manufacturer"],
                        "Aster",
                    )
                    lineage_labels = [entry["label"] for entry in network["lineage"]["entries"]]
                    self.assertIn("Aster Motor M-5", lineage_labels)
                    self.assertIn("Northstar P-100 Supply Offer", lineage_labels)
                    self.assertIn("North Wing Mechanical Package", lineage_labels)
                    self.assertNotIn("Standby Circulation Pump", lineage_labels)
                    self.assertNotIn("Lobby Fire Door", lineage_labels)
                    self.assertNotIn("Corridor Fire Door", lineage_labels)
                    labels = [link["label"] for link in network["links"]]
                    self.assertIn("Direct supply", labels)
                    self.assertTrue(any(label.startswith("Supply via ") for label in labels))
                    self.assertGreaterEqual(labels.count("Allocated from"), 76)
                    self.assertGreaterEqual(len(network["entries"]), 500)
                    self.assertEqual(len(state["portfolio"]), 6)
                    self.assertEqual(len(state["portfolioProducts"]), 8)
                    self.assertEqual(len(state["pendingUpdates"]), 5)
                    self.assertEqual(sum(len(item["installations"]) for item in state["portfolio"]), 108)
                    self.assertEqual(sum(len(item["events"]) for item in state["portfolio"]), 324)
                    for facility in state["portfolio"]:
                        self.assertEqual(sum(entry["label"] == facility["name"] for entry in network["entries"]), 1)
                        self.assertEqual(sum(entry["label"] == facility["name"] + " Campus" for entry in network["entries"]), 1)
                    print(f"\nExpanded owner graph: {len(network['entries'])} unique nodes, "
                          f"{len(network['links'])} relationships.")
                    self.assertIn("Component type", labels)
                    for label in ("North Wing Campus", "North Wing", "Ground Floor", "Main Lobby", "Plant Room",
                                  "Service Corridor", "Public Areas", "Life Safety Assets", "Plant Room Pump Assembly"):
                        self.assertEqual(sum(entry["label"] == label for entry in network["entries"]), 1, label)
                    self.assertEqual(network["scoped"].count("North Wing Campus"), 1)
                    self.assertEqual(network["scoped"].count("Ground Floor"), 1)
                    entries = {entry["key"]: entry for entry in network["entries"]}
                    flow = network["flow"]
                    self.assertTrue(any(
                        entries[link["source"]]["label"] == "Aster Motor M-5"
                        and entries[link["target"]]["label"] == "Northstar Inline Pump P-100"
                        for link in flow if link["kind"] == "Component type"
                    ))
                    self.assertTrue(any(
                        entries[link["source"]]["label"] == "Northstar Inline Pump P-100"
                        and entries[link["target"]]["label"] == "Primary Pump Installation"
                        for link in flow if link["kind"] == "Type"
                    ))
                    self.assertTrue(any(link["kind"] == "Event" and entries[link["source"]]["kind"] == "event"
                                        and entries[link["target"]]["kind"] == "asset" for link in flow))
                    published = {item["recordId"]: item["publishedRevision"] for item in state["productUpdates"]}
                    for entry in canonical:
                        identity = entry["graph"]["entities"][entry["path"]]["components"][demo.IDENTITY_SCHEMA]
                        if identity["recordId"] in published:
                            self.assertEqual(identity["revision"], published[identity["recordId"]])
                    update = state["pendingUpdates"][0]
                    incoming = demo.request(
                        update["recipient"], "GET", f"/clip/v1/supply-chain/submissions/{update['submissionId']}",
                    )
                    incoming.raise_for_status()
                    decision = demo.post(
                        update["recipient"], f"/clip/v1/supply-chain/submissions/{update['submissionId']}/decision",
                        {"decision": "accept", "reason": "Integration: reviewed catalogue update",
                         "expectedRevision": incoming.json()["revision"], "idempotencyKey": "test-accept-update"},
                    )
                    self.assertEqual(decision["status"], "accepted")
                    self.assertEqual(len(decision["acceptedRecords"]), 1)
                    accepted = demo.request(
                        update["recipient"], "GET", f"/clip/v1/supply-chain/records/{update['acceptedRecordId']}",
                    )
                    accepted.raise_for_status()
                    original_revision = next(item["installedRevision"] for item in state["productUpdates"]
                                             if item["recordId"] == update["recordId"])
                    self.assertEqual(accepted.json()["acceptedFrom"]["snapshot"]["revision"], original_revision)
                except Exception as error:
                    if isinstance(error, httpx.HTTPStatusError):
                        print(f"\nDemo API rejection: {error.response.text}")
                    for role, log in zip(nodes, logs):
                        log.flush()
                        log.seek(0)
                        print(f"\n{role} server log:\n{log.read()[-6000:]}")
                    raise
                finally:
                    for process in processes:
                        if process.poll() is None:
                            if os.name == "nt":
                                process.send_signal(signal.CTRL_BREAK_EVENT)
                            else:
                                process.terminate()
                    for process in processes:
                        process.wait(timeout=15)
                    for log in logs:
                        log.close()
