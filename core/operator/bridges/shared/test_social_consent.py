"""Tests for social_consent.py — Layer 39 CorvinFed."""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))


def _reload_social():
    for mod in list(sys.modules.keys()):
        if mod.startswith("social_"):
            del sys.modules[mod]


class TestIsConsented(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_not_consented_by_default(self):
        import social_consent
        self.assertFalse(social_consent.is_consented())

    def test_consented_after_join(self):
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        self.assertTrue(social_consent.is_consented())

    def test_not_consented_after_leave(self):
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        social_consent.leave()
        self.assertFalse(social_consent.is_consented())


class TestRequireConsent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_require_consent_raises_when_not_joined(self):
        import social_consent
        with self.assertRaises(social_consent.ConsentRequired):
            social_consent.require_consent()

    def test_require_consent_passes_when_joined(self):
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        # Should not raise
        social_consent.require_consent()


class TestJoinFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_join_returns_joined_status(self):
        import social_consent
        result = social_consent.join("TestNode", "localhost", "eu")
        self.assertEqual(result["status"], "joined")

    def test_join_creates_keypair_file(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        self.assertTrue(social_actor.keypair_path().exists())

    def test_join_creates_actor_doc(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        self.assertTrue(social_actor.actor_doc_path().exists())

    def test_join_idempotent(self):
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        result2 = social_consent.join("TestNode", "localhost", "eu")
        self.assertEqual(result2["status"], "already_joined")

    def test_join_actor_is_ai_true(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        doc = social_actor.load_actor_document()
        self.assertTrue(doc.get("is_ai"))

    def test_join_compliance_zone(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        doc = social_actor.load_actor_document()
        self.assertEqual(doc.get("compliance_zone"), "eu")


class TestLeaveFlow(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_leave_returns_left_status(self):
        import social_consent
        social_consent.join("TestNode", "localhost", "eu")
        result = social_consent.leave()
        self.assertEqual(result["status"], "left")

    def test_leave_when_not_joined(self):
        import social_consent
        result = social_consent.leave()
        self.assertEqual(result["status"], "not_joined")

    def test_leave_deletes_keypair(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        kp = social_actor.keypair_path()
        self.assertTrue(kp.exists())
        social_consent.leave()
        self.assertFalse(kp.exists())

    def test_leave_keeps_posts_db_intact(self):
        import social_consent
        import social_actor
        social_consent.join("TestNode", "localhost", "eu")
        # Create a fake posts.db in the social dir
        social_d = social_actor.social_dir()
        fake_db = social_d / "posts.db"
        fake_db.parent.mkdir(parents=True, exist_ok=True)
        fake_db.write_text("fake db content")
        social_consent.leave()
        # posts.db must still exist — L36 handler deletes it separately
        self.assertTrue(fake_db.exists())


class TestGetStatus(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        os.environ["CORVIN_HOME"] = self.tmp
        _reload_social()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_status_not_joined(self):
        import social_consent
        status = social_consent.get_status()
        self.assertFalse(status["is_enabled"])
        self.assertIsNone(status["actor_id"])


if __name__ == "__main__":
    unittest.main()
