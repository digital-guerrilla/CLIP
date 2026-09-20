"""Negative controls for record proof verification on the replication path.

Scope first, because it bounds every claim below: these tests cover
``POST /v3/federation/sync``. They do not cover the other ``NodeKeyManager.verify``
call sites in ``federation/resolver.py``, ``api/relationships.py`` and
``api/document_store.py``, which remain a separate coverage gap.

Why the file exists: the existing proof assertions in ``tests/test_v3_api.py`` are
*positive* ones -- they check that a valid signature verifies. A positive assertion
cannot fail when verification is removed. Replacing the body of
``NodeKeyManager.verify`` with ``return True`` leaves the default suite fully green
(16 tests) and the six-authority integration test fully green (1 test), so without
this file neither of those suites can tell the difference between "signatures are
checked" and "signatures are ignored" on this path.

These tests are the refusal half. Each publishes a correctly signed record from a
*foreign* authority, breaks exactly one thing, and asserts that ``/v3/federation/sync``
refuses it -- and refuses it for the stated reason, not incidentally because the
fixture was malformed.

``test_correctly_signed_foreign_record_is_accepted`` is the positive control. Without
it, a file that refused everything would look identical to one that refuses the right
things.

The fixtures deliberately reuse ``node.app.api.assets._signed_record`` rather than
hand-building a record dict, so the fixture cannot drift from the shape a real node
publishes.
"""

import contextlib
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from node.app import dependencies
from node.app.api.assets import _signed_record
from node.app.config import settings
from node.app.core.crypto import NodeKeyManager
from node.app.core.guid import generate_daid
from node.app.core.models import (
    AssetSubject,
    AvailabilityPolicy,
    VerificationMethod,
    WellKnownResponse,
)
from node.app.main import app

FOREIGN_HOST = "localhost:8901"
LOCAL_HOST = "localhost:8999"
DEFAULT_PURPOSES = ("record", "relationship-assertion", "relationship-acceptance")


_OVERRIDDEN_SETTINGS = (
    "DATABASE_URL",
    "PRIVATE_KEY_FILE",
    "GOSSIP_SEEDS",
    "NODE_DOMAIN",
    "NODE_API_BASE",
    "API_KEY",
)


@contextlib.contextmanager
def _local_node():
    """A fresh local node, distinct from FOREIGN_HOST so replication is not self-directed.

    Process-global state is restored on the way out. Without that, a module sorting
    after this one would inherit a DATABASE_URL pointing into a deleted temp
    directory, and would fail for a reason having nothing to do with its own subject.
    """
    saved = {name: getattr(settings, name) for name in _OVERRIDDEN_SETTINGS}
    saved_key_manager = dependencies._key_manager
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            settings.DATABASE_URL = f"sqlite+aiosqlite:///{root / 'node.db'}"
            settings.PRIVATE_KEY_FILE = str(root / "node.key")
            settings.GOSSIP_SEEDS = ""
            settings.NODE_DOMAIN = LOCAL_HOST
            settings.NODE_API_BASE = f"http://{LOCAL_HOST}"
            settings.API_KEY = "test-key"
            dependencies._key_manager = None
            with TestClient(app) as client:
                yield client
    finally:
        for name, value in saved.items():
            setattr(settings, name, value)
        dependencies._key_manager = saved_key_manager


def _foreign_authority(key_manager: NodeKeyManager, purposes=DEFAULT_PURPOSES):
    """A correctly signed record and matching signed descriptor for a remote authority."""
    now = datetime.now(timezone.utc).replace(microsecond=0)
    controller = f"did:web:{FOREIGN_HOST.replace(':', '%3A')}"
    record = _signed_record(
        record_id=generate_daid(FOREIGN_HOST, key_manager.public_key_multibase),
        record_kind="type",
        subject=AssetSubject(
            name="Fire Door FD60",
            manufacturer="Example Manufacturing",
            model_number="FD60-01",
        ),
        relationships=[],
        availability=AvailabilityPolicy(),
        controller=controller,
        created_at=now,
        updated_at=now,
        version=1,
        key_manager=key_manager,
    )
    method = VerificationMethod(
        id=f"{controller}#daid-record-signing",
        public_key_multibase=key_manager.public_key_multibase,
        public_key_base64=key_manager.public_key_b64,
        purposes=list(purposes),
        valid_from=now,
    )
    descriptor = WellKnownResponse(
        authority=key_manager.public_key_multibase,
        genesis_public_key_multibase=key_manager.public_key_multibase,
        endpoints=[f"http://{FOREIGN_HOST}"],
        verification_methods=[method],
        sequence=1,
        expires_at=now + timedelta(days=30),
        proof="unsigned",
    )
    document = descriptor.model_dump(mode="json")
    document["proof"] = key_manager.sign_record(document)
    return record.model_dump(mode="json"), WellKnownResponse.model_validate(document)


def _sync(client, descriptor, document):
    """POST a record to the replication endpoint with discovery pinned to `descriptor`."""
    with patch(
        "node.app.api.federation.fetch_well_known",
        new=AsyncMock(return_value=descriptor),
    ):
        return client.post("/v3/federation/sync", json=document)


class VerificationNegativeControlTest(unittest.TestCase):
    def test_correctly_signed_foreign_record_is_accepted(self) -> None:
        """POSITIVE CONTROL. If this cannot pass, the refusals below prove nothing."""
        with _local_node() as client:
            document, descriptor = _foreign_authority(NodeKeyManager())
            response = _sync(client, descriptor, document)
            self.assertEqual(response.status_code, 202, response.text)

    def test_altered_record_body_is_refused(self) -> None:
        """A single changed field after signing must invalidate the proof."""
        with _local_node() as client:
            document, descriptor = _foreign_authority(NodeKeyManager())
            self.assertEqual(document["subject"]["name"], "Fire Door FD60")
            document["subject"]["name"] = "Fire Door FD30"

            response = _sync(client, descriptor, document)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("proof verification failed", response.text.lower())

    def test_record_signed_by_a_different_key_is_refused(self) -> None:
        """Identity claims the real authority; only the signature is the impostor's."""
        with _local_node() as client:
            authority = NodeKeyManager()
            impostor = NodeKeyManager()
            self.assertNotEqual(
                authority.public_key_multibase, impostor.public_key_multibase
            )
            document, descriptor = _foreign_authority(authority)
            document["proof"]["proof_value"] = impostor.sign_record(document)

            response = _sync(client, descriptor, document)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("proof verification failed", response.text.lower())

    def test_verification_method_without_record_purpose_is_refused(self) -> None:
        """A correctly signed record, presented under a method not scoped to records."""
        with _local_node() as client:
            document, descriptor = _foreign_authority(
                NodeKeyManager(), purposes=("relationship-assertion",)
            )
            self.assertNotIn(
                "record", descriptor.verification_methods[0].purposes
            )

            response = _sync(client, descriptor, document)
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("proof verification failed", response.text.lower())


if __name__ == "__main__":
    unittest.main()
