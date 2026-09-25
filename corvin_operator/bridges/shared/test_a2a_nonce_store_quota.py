"""Regression tests — a2a_nonce_store never evicts a LIVE nonce (2026-09-25).

Before: over 10 000 rows the SQLite store deleted the oldest rows even when
they were still inside their replay window, and had no per-origin quota — one
authenticated peer could push 10 000 nonces and re-open replay of every other
peer's captured envelope. The in-memory fallback did the same (popitem).
Now: per-origin cap, keyed (origin_id, nonce), and a full store REFUSES.
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a2a_nonce_store as ns  # noqa: E402


class _Contract:
    """Shared admission contract for both store implementations."""

    def make(self):  # pragma: no cover - overridden
        raise NotImplementedError

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-nonce-")
        self.dir = Path(self._tmp.name)
        # Small caps keep the tests fast; the logic is cap-agnostic.
        # Zero persist-buffer so ttl_s alone decides expiry (SQLite store).
        self._caps = [mock.patch.object(ns, "_NONCE_MAX", 40),
                      mock.patch.object(ns, "_PER_ORIGIN_MAX", 10),
                      mock.patch.object(ns, "_NONCE_PERSIST_BUFFER_S", 0.0)]
        for c in self._caps:
            c.start()
        self.store = self.make()

    def tearDown(self):
        for c in self._caps:
            c.stop()
        self._tmp.cleanup()

    def test_one_origin_cannot_exceed_its_quota(self):
        got = [self.store.check_and_add_ex(f"a{i}", origin_id="A") for i in range(15)]
        self.assertEqual(got.count(ns.OK), 10)
        self.assertEqual(got[10:], [ns.ORIGIN_QUOTA] * 5)
        # Another origin is unaffected.
        self.assertTrue(self.store.check_and_add("b0", origin_id="B"))

    def test_flood_never_reopens_replay_of_a_live_nonce(self):
        self.assertTrue(self.store.check_and_add("VICTIM", origin_id="victim"))
        for o in "CDEFG":  # 5 origins x 10 = 50 attempts > _NONCE_MAX (40)
            for i in range(10):
                self.store.check_and_add(f"{o}{i}", origin_id=o)
        self.assertFalse(self.store.check_and_add("VICTIM", origin_id="victim"),
                         "live nonce was evicted — replay re-opened")

    def test_full_store_refuses_instead_of_evicting(self):
        for o in "ABCD":
            for i in range(10):
                self.store.check_and_add(f"{o}{i}", origin_id=o)
        self.assertEqual(self.store.check_and_add_ex("new", origin_id="E"),
                         ns.STORE_FULL)
        # Every original row still counts as seen.
        self.assertEqual(self.store.check_and_add_ex("A0", origin_id="A"), ns.REPLAY)

    def test_expired_rows_free_capacity(self):
        store = self.make(ttl_s=0.05)
        for i in range(10):
            self.assertTrue(store.check_and_add(f"x{i}", origin_id="A"))
        self.assertEqual(store.check_and_add_ex("x10", origin_id="A"), ns.ORIGIN_QUOTA)
        time.sleep(0.2)
        self.assertTrue(store.check_and_add("x11", origin_id="A"))

    def test_remove_frees_quota_slot_and_allows_retry(self):
        for i in range(10):
            self.store.check_and_add(f"r{i}", origin_id="A")
        self.store.remove("r3", origin_id="A")
        self.assertTrue(self.store.check_and_add("r3", origin_id="A"))

    def test_remove_is_origin_scoped(self):
        self.store.check_and_add("same", origin_id="A")
        self.store.check_and_add("same", origin_id="B")
        self.store.remove("same", origin_id="A")
        self.assertTrue(self.store.check_and_add("same", origin_id="A"))
        self.assertFalse(self.store.check_and_add("same", origin_id="B"))

    def test_empty_origin_refused(self):
        self.assertEqual(self.store.check_and_add_ex("n", origin_id=""), ns.INVALID)


class TestPersistentStoreQuota(_Contract, unittest.TestCase):
    def make(self, ttl_s=None):
        store = ns.PersistentNonceStore(self.dir / f"n{time.monotonic_ns()}.db",
                                        ttl_s=ttl_s)
        self.assertIsNone(store._fallback)
        return store

    def test_survives_restart(self):
        self.store.check_and_add("keep", origin_id="A")
        store2 = ns.PersistentNonceStore(self.store._db_path)
        self.assertFalse(store2.check_and_add("keep", origin_id="A"))

    def test_legacy_schema_is_migrated_and_old_rows_still_block(self):
        db = self.dir / "legacy.db"
        con = sqlite3.connect(db)
        con.executescript(
            "CREATE TABLE nonces (nonce TEXT NOT NULL PRIMARY KEY,"
            " expires_at REAL NOT NULL);")
        con.execute("INSERT INTO nonces VALUES (?, ?)", ("old", time.time() + 600))
        con.commit()
        con.close()
        store = ns.PersistentNonceStore(db)
        self.assertIsNone(store._fallback)
        cols = [r[1] for r in sqlite3.connect(db).execute(
            "PRAGMA table_info(nonces)")]
        self.assertIn("origin_id", cols)
        self.assertEqual(store.check_and_add_ex("old", origin_id="A"), ns.REPLAY)
        self.assertTrue(store.check_and_add("fresh", origin_id="A"))


class TestInMemoryFallbackQuota(_Contract, unittest.TestCase):
    def make(self, ttl_s=None):
        return ns._InMemoryNonceStore(ttl_s=ttl_s)

    def test_persistent_store_uses_it_on_bad_path(self):
        store = ns.PersistentNonceStore("/proc/definitely/not/writable/n.db")
        self.assertIsNotNone(store._fallback)
        for i in range(10):
            store.check_and_add(f"f{i}", origin_id="A")
        self.assertEqual(store.check_and_add_ex("f10", origin_id="A"), ns.ORIGIN_QUOTA)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestPerOriginCapFollowsRateLimit(unittest.TestCase):
    """Round 2: a fixed 2 500 cap throttled every origin to ~214 req/min,
    ignoring rate_limit_rpm (incl. the documented 0 = unlimited)."""

    def test_cap_scales_with_rpm_and_unlimited_uses_the_global_cap(self):
        import remote_trigger_receiver as rtr
        cap = rtr.RemoteTriggerReceiver._per_origin_nonce_cap
        self.assertEqual(cap({"rate_limit_rpm": 60}), rtr.NonceStore._PER_ORIGIN_MAX)
        self.assertGreater(cap({"rate_limit_rpm": 600}), 600 * 11)
        # Never the whole store: other origins keep a quarter (round 3).
        self.assertEqual(cap({"rate_limit_rpm": 0}), rtr._NONCE_MAX * 3 // 4)
        self.assertEqual(cap({"rate_limit_rpm": None}), rtr._NONCE_MAX * 3 // 4)
        self.assertLessEqual(cap({"rate_limit_rpm": 100000}), rtr._NONCE_MAX * 3 // 4)

    def test_store_honours_an_explicit_per_origin_max(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as d:
            store = ns.PersistentNonceStore(Path(d) / "n.db")
            for i in range(3000):
                self.assertEqual(store.check_and_add_ex(f"n{i}", origin_id="fast",
                                                        per_origin_max=5000), "ok")
            self.assertEqual(store.check_and_add_ex("x", origin_id="slow", per_origin_max=0 or None), "ok")
