import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import httpx

from node.app.core.guid import parse_clip


@unittest.skipUnless(os.getenv("CLIP_INTEGRATION") == "1", "set CLIP_INTEGRATION=1")
class NetworkIntegrationTest(unittest.TestCase):
    def test_six_authority_lifecycle_graph(self) -> None:
        roles = ["manufacturer", "supplier", "main_contractor", "owner", "inspector", "relay"]
        ports = range(8201, 8207)
        processes: list[subprocess.Popen] = []
        logs = []
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            root = Path(directory)
            try:
                for role, port in zip(roles, ports):
                    log = open(root / f"{role}.log", "w+", encoding="utf-8")
                    logs.append(log)
                    environment = os.environ.copy()
                    environment.update({
                        "PYTHONUNBUFFERED": "1",
                        "NODE_DOMAIN": f"localhost:{port}",
                        "NODE_API_BASE": f"http://localhost:{port}",
                        "API_KEY": f"{role}-key",
                        "DATABASE_URL": f"sqlite+aiosqlite:///{(root / f'{role}.db').as_posix()}",
                        "PRIVATE_KEY_FILE": str(root / f"{role}.key"),
                        "GOSSIP_SEEDS": "",
                        "NODE_ROLE": role,
                        "DID_WEB_ID": f"did:web:localhost%3A{port}",
                    })
                    processes.append(subprocess.Popen(
                        [sys.executable, "-m", "uvicorn", "node.app.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"],
                        cwd=Path(__file__).resolve().parents[1],
                        env=environment,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    ))

                self._wait_for_nodes(list(ports), processes)
                records = self._publish_records()
                self._link_records(records)

                graph = self._post(8206, "/v3/resolve-graph", {
                    "root": records["instance"]["id"],
                    "depth": 2,
                    "max_nodes": 50,
                })
                self.assertTrue(graph["complete"], graph["failures"])
                self.assertEqual(len(graph["nodes"]), 5)
                self.assertEqual(len(graph["edges"]), 4)
                self.assertTrue(all(node["status"] == "verified_current" for node in graph["nodes"].values()))
                self.assertTrue(all(edge["state"] == "accepted" for edge in graph["edges"]))
                self.assertTrue(all(len(edge["proofs"]) == 2 for edge in graph["edges"]))
                baseline = next(edge for edge in graph["edges"] if edge["relation_type"] == "defines_type")
                self.assertEqual(baseline["target_integrity"]["mode"], "snapshot")
                self.assertEqual(len(baseline["target_integrity"]["sha256"]), 64)

                parsed = parse_clip(records["instance"]["id"])
                owner = httpx.get(
                    f"http://localhost:8204/v3/records/{parsed.authority_key_fingerprint}/{parsed.record_uuid}",
                    timeout=5,
                )
                owner.raise_for_status()
                self.assertEqual(owner.json()["version"], 5)
                print(
                    f"verified root={records['instance']['id']} "
                    f"nodes={len(graph['nodes'])} edges={len(graph['edges'])} version=5"
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

    def _wait_for_nodes(self, ports: list[int], processes: list[subprocess.Popen]) -> None:
        deadline = time.monotonic() + 45
        for port, process in zip(ports, processes):
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError(f"Node {port} exited with {process.returncode}")
                try:
                    response = httpx.get(f"http://localhost:{port}/v3/node/info", timeout=1)
                    if response.status_code == 200:
                        break
                except httpx.RequestError:
                    pass
                time.sleep(0.1)
            else:
                raise RuntimeError(f"Node {port} did not become ready")

    def _publish_records(self) -> dict[str, dict]:
        definitions = {
            "type": (8201, "manufacturer", "type", {"name": "AHU-100", "manufacturer": "Global HVAC", "model_number": "AHU-100"}),
            "custody": (8202, "supplier", "assertion", {"name": "Delivery", "event_type": "delivered"}),
            "procurement": (8203, "main_contractor", "assertion", {"name": "Procurement", "event_type": "procured"}),
            "instance": (8204, "owner", "instance", {"name": "North Wing AHU", "serial_number": "AHU-009184", "asset_owner": "Hospital Trust"}),
            "inspection": (8205, "inspector", "assertion", {"name": "Commissioning inspection", "event_type": "inspected", "result": "pass"}),
        }
        return {
            name: self._post(port, "/v3/records", {"record_kind": kind, "subject": subject}, f"{role}-key")
            for name, (port, role, kind, subject) in definitions.items()
        }

    def _link_records(self, records: dict[str, dict]) -> None:
        relationships = [
            (8201, "manufacturer", "type", "manufacturer", "defines_type"),
            (8202, "supplier", "custody", "supplier", "custody_event"),
            (8203, "main_contractor", "procurement", "main_contractor", "procured_under"),
            (8205, "inspector", "inspection", "inspector", "inspected_by"),
        ]
        for port, key, target, role, relation_type in relationships:
            proposal = self._post(port, "/v3/relationships/proposals", {
                "source": records["instance"]["id"],
                "target": records[target]["id"],
                "role": role,
                "relation_type": relation_type,
                "claims": {"demo": True},
            }, f"{key}-key")
            self._post(8204, "/v3/relationships/accept", proposal, "owner-key")

    @staticmethod
    def _post(port: int, path: str, body: dict, api_key: str | None = None) -> dict:
        headers = {"x-api-key": api_key} if api_key else None
        response = httpx.post(f"http://localhost:{port}{path}", json=body, headers=headers, timeout=15)
        response.raise_for_status()
        return response.json()


if __name__ == "__main__":
    unittest.main()