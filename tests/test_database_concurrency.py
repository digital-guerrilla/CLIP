"""File-backed SQLite regressions for catalogue cache writes beside readers."""

import asyncio
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from node.app.core.supply_chain import cache_snapshot
from node.app.db import database
from node.app.db.orm_models import SupplyChainRevision


class SQLiteConcurrencyTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "concurrency.db"
        await database.init_db(f"sqlite+aiosqlite:///{self.path.as_posix()}")
        self.snapshot = {
            "authorityDid": "did:web:manufacturer.example", "id": "pump", "revision": 1,
            "kind": "product", "status": "published", "projectId": None,
            "documents": [], "dependencies": [],
        }
        async with database.AsyncSessionLocal() as session:
            await cache_snapshot(session, self.snapshot)
            await session.commit()

    async def asyncTearDown(self):
        await database.close_db()
        self.directory.cleanup()

    async def test_wal_and_busy_timeout_apply_to_multiple_connections(self):
        async with database.get_engine().connect() as first, database.get_engine().connect() as second:
            for connection in (first, second):
                self.assertEqual((await connection.execute(text("PRAGMA journal_mode"))).scalar_one(), "wal")
                self.assertEqual((await connection.execute(text("PRAGMA busy_timeout"))).scalar_one(), 30000)

    async def test_reader_does_not_block_public_cache_promotion_commit(self):
        async with database.AsyncSessionLocal() as reader:
            await reader.execute(text("BEGIN"))
            await reader.execute(text("SELECT * FROM clip_supply_chain_revisions"))
            async def promote():
                async with database.AsyncSessionLocal() as writer:
                    await cache_snapshot(writer, self.snapshot, public=True)
                    await writer.commit()
            await asyncio.wait_for(promote(), timeout=3)
            await reader.rollback()
        async with database.AsyncSessionLocal() as session:
            revision = await session.get(SupplyChainRevision, ("did:web:manufacturer.example", "pump", 1))
            self.assertIsNotNone(revision)
            assert revision is not None
            self.assertTrue(revision.public)
            self.assertEqual(revision.snapshot_json, self.snapshot)

    async def test_rollback_journal_reproduces_reported_public_promotion_lock(self):
        async with database.get_engine().connect() as connection:
            self.assertEqual((await connection.execute(text("PRAGMA journal_mode=DELETE"))).scalar_one(), "delete")
        async with database.AsyncSessionLocal() as reader:
            await reader.execute(text("BEGIN"))
            await reader.execute(text("SELECT * FROM clip_supply_chain_revisions"))
            async with database.AsyncSessionLocal() as writer:
                await writer.execute(text("PRAGMA busy_timeout=25"))
                await cache_snapshot(writer, self.snapshot, public=True)
                with self.assertRaisesRegex(OperationalError, "database is locked"):
                    await writer.commit()
                await writer.rollback()
            await reader.rollback()

    async def test_concurrent_public_promotions_wait_and_preserve_revision(self):
        async with database.AsyncSessionLocal() as first:
            await cache_snapshot(first, self.snapshot, public=True)
            await first.flush()
            started = asyncio.Event()
            async def promote():
                async with database.AsyncSessionLocal() as second:
                    await cache_snapshot(second, self.snapshot, public=True)
                    started.set()
                    await second.commit()
            task = asyncio.create_task(promote())
            try:
                await asyncio.wait_for(started.wait(), timeout=3)
                await first.commit()
                await asyncio.wait_for(task, timeout=3)
            finally:
                if not task.done():
                    task.cancel()
                    await asyncio.gather(task, return_exceptions=True)
        async with database.AsyncSessionLocal() as session:
            revision = await session.get(SupplyChainRevision, ("did:web:manufacturer.example", "pump", 1))
            assert revision is not None
            self.assertTrue(revision.public)
            self.assertEqual(revision.snapshot_json, self.snapshot)
