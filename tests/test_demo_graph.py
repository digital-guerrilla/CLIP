"""Exercise the actual demo seeder without touching the running demo's data."""

import json
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
from unittest.mock import patch

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
                            "CLIP_GOSSIP_INTERVAL": "1",
                            "CLIP_TRUSTED_PUBLISHERS": seeds,
                            "CLIP_ALLOW_HTTP_LOOPBACK": "true",
                            "NODE_ROLE": "manufacturer" if role == "component_manufacturer" else role,
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
                                f"{demo.quote(dataset['datasetId'], safe='')}/graph"
                            )
                            response.raise_for_status()
                            graphs.append(response.json())
                    script = r"""
const fs=require('node:fs'),vm=require('node:vm');
const dashboard=fs.readFileSync('node\\app\\static\\dashboard.js','utf8');
const context=vm.createContext();
vm.runInContext(dashboard.slice(0,dashboard.indexOf('function activateView')),context);
context.graphs=JSON.parse(fs.readFileSync(0,'utf8'));
console.log(vm.runInContext(`state.graphs=graphs;
const network=entityNetworkData(),pump=network.entries.find(entry=>entry.label==='Primary Pump Installation');
const project=graphs.find(graph=>graph.datasetId.includes('project:'));
state.scope=project.datasetId;
const scoped=entityNetworkData();
JSON.stringify({...network,lineage:lineageNetworkData(network,pump.key),
  flow:network.links.map(entityFlowLink),scoped:scoped.entries.map(entry=>entry.label)})`,context));
"""
                    result = subprocess.run(
                        ["node", "-e", script], cwd=root, input=json.dumps(graphs),
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
                        self.assertEqual(len(links), 3)  # Two Renewal assets and one imported IFC asset.
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
                    self.assertEqual(labels.count("Allocated from"), 4)
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
