"""Tests for vault.py — set/get/forget, locked items, audit log, prompt block.

Encryption tests are gated on `gpg` being installed (and a default key
being available); otherwise they're skipped.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

_SANDBOX = tempfile.mkdtemp(prefix="vault_test_")
os.environ["XDG_CONFIG_HOME"] = _SANDBOX
os.environ["XDG_RUNTIME_DIR"] = _SANDBOX

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vault as v  # noqa: E402


def _wipe():
    vault_dir = v._vault_dir()
    if vault_dir.exists():
        for p in vault_dir.iterdir():
            try:
                p.unlink()
            except IsADirectoryError:
                shutil.rmtree(p)
    unlock_file = v._unlock_file()
    if unlock_file.exists():
        unlock_file.unlink()
    log_file = v._log_file()
    if log_file.exists():
        log_file.unlink()


class NameValidation(unittest.TestCase):
    def test_normalise(self):
        self.assertEqual(v._normalise_name("Visa_Main"), "visa_main")
        self.assertEqual(v._normalise_name("home-address"), "home-address")

    def test_rejects_bad_names(self):
        for bad in ("", "../etc/passwd", "a/b", "a b", "X" * 50):
            with self.assertRaises(ValueError, msg=f"should reject {bad!r}"):
                v._normalise_name(bad)


class PlainItemTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_set_get(self):
        v.set_item("home_addr", {"street": "Foo 1", "city": "Berlin"},
                   kind="address", tags=["personal"])
        out = v.get_item("home_addr")
        self.assertEqual(out["street"], "Foo 1")

    def test_set_overwrites(self):
        v.set_item("k", "first")
        v.set_item("k", "second")
        self.assertEqual(v.get_item("k"), "second")

    def test_list_items_no_values(self):
        v.set_item("a", "secret-a", tags=["t1"])
        v.set_item("b", {"hidden": "secret-b"})
        items = v.list_items()
        # No values must be exposed in the listing.
        names = [i["name"] for i in items]
        self.assertEqual(sorted(names), ["a", "b"])
        for i in items:
            self.assertNotIn("value", i)

    def test_get_missing(self):
        with self.assertRaises(KeyError):
            v.get_item("nope")

    def test_forget(self):
        v.set_item("scratch", "x")
        self.assertTrue(v.forget_item("scratch"))
        self.assertFalse(v.forget_item("scratch"))
        with self.assertRaises(KeyError):
            v.get_item("scratch")


class LockedItemTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_locked_item_blocked_until_unlock(self):
        v.set_item("visa", "4242 4242 4242 4242", kind="credit-card",
                   auto_unlock=False)
        with self.assertRaises(PermissionError):
            v.get_item("visa")
        v.unlock("visa")
        out = v.get_item("visa")
        self.assertEqual(out, "4242 4242 4242 4242")

    def test_unlock_expires(self):
        v.set_item("k", "x", auto_unlock=False)
        # Unlock for 0 seconds → already expired by the time we check.
        v.unlock("k", ttl=0)
        time.sleep(0.05)
        self.assertFalse(v.is_unlocked("k"))

    def test_unlock_unknown(self):
        with self.assertRaises(KeyError):
            v.unlock("does-not-exist")


class AuditTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_get_logs_event(self):
        v.set_item("x", "y")
        v.get_item("x", source="discord/123")
        log = v.read_audit(10)
        self.assertTrue(any("get name=x from=discord/123" in line for line in log))

    def test_failed_get_logs_fail(self):
        try:
            v.get_item("nope", source="t")
        except KeyError:
            pass
        log = v.read_audit(10)
        self.assertTrue(any("FAIL get name=nope" in line for line in log))


class SystemPromptTests(unittest.TestCase):
    def setUp(self):
        _wipe()

    def test_empty_yields_empty(self):
        self.assertEqual(v.for_system_prompt(), "")

    def test_with_items_inventories_only(self):
        v.set_item("home_addr", {"city": "Berlin"}, kind="address",
                   tags=["personal"])
        v.set_item("visa", "4242", kind="credit-card", auto_unlock=False)
        out = v.for_system_prompt()
        self.assertIn("home_addr", out)
        self.assertIn("visa", out)
        self.assertIn("locked", out)
        # MUST NOT leak values.
        self.assertNotIn("4242", out)
        self.assertNotIn("Berlin", out)


class GpgEncryptionTests(unittest.TestCase):
    """Only runs when gpg is installed and a default key works."""

    @classmethod
    def setUpClass(cls):
        if not v._has_gpg():
            raise unittest.SkipTest("gpg not installed")
        # Quick liveness: can we encrypt an empty payload to default-self?
        import subprocess
        try:
            r = subprocess.run(
                ["gpg", "--batch", "--default-recipient-self",
                 "--encrypt", "--armor"],
                input=b"x", capture_output=True, timeout=5,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired):
            raise unittest.SkipTest("gpg cannot encrypt to default recipient")
        if r.returncode != 0:
            raise unittest.SkipTest(
                "gpg has no usable default key in this environment"
            )

    def setUp(self):
        _wipe()

    def test_encrypted_roundtrip(self):
        v.set_item("aws_dev", "AKIA-test-key", encrypted=True)
        out = v.get_item("aws_dev")
        self.assertEqual(out, "AKIA-test-key")
        # The on-disk file must NOT contain plaintext.
        p = v._item_path("aws_dev", encrypted=True)
        raw = p.read_bytes()
        self.assertNotIn(b"AKIA-test-key", raw)


if __name__ == "__main__":
    unittest.main(verbosity=2)
