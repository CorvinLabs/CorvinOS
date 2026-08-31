"""Tests for RemoteEndpointRegistry.resolve — name/label → endpoint_id."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import remote_trigger_sender as rts  # noqa: E402


def _write_endpoint(dirpath: Path, endpoint_id: str, label: str = "") -> None:
    cfg = {
        "endpoint_id": endpoint_id,
        "url": "https://example.invalid/a2a",
        "hmac_key": "a" * 64,
        "recv_key": "b" * 64,
        "enabled": True,
    }
    if label:
        cfg["label"] = label
    path = dirpath / f"{endpoint_id}.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    path.chmod(0o600)


class TestResolve(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="corvin-resolve-")
        self.dir = Path(self._tmp.name)
        # env override would win over the constructor arg — neutralize it
        self._env = mock.patch.dict(
            os.environ, {"REMOTE_ENDPOINTS_DIR": self._tmp.name}
        )
        self._env.start()
        self.reg = rts.RemoteEndpointRegistry()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_exact_id_clean_returns_id(self):
        # exact id, no other peer's label collides → returns the id
        _write_endpoint(self.dir, "alpha", label="Alpha Box")
        _write_endpoint(self.dir, "beta", label="Beta Box")
        self.assertEqual(self.reg.resolve("beta"), "beta")

    def test_exact_id_wins_over_foreign_label(self):
        # A3 (2026-07-20): "beta" is an exact id AND a DIFFERENT peer's
        # (peer-controlled) label. The endpoint_id is operator-assigned and
        # unique, so the exact-id reference MUST deterministically resolve to
        # the id owner — otherwise a peer that sets its label equal to another
        # peer's id makes that victim unaddressable via CLI and MCP
        # (peer-triggerable availability DoS).
        _write_endpoint(self.dir, "alpha", label="beta")
        _write_endpoint(self.dir, "beta", label="Gamma")
        self.assertEqual(self.reg.resolve("beta"), "beta")

    def test_label_only_collision_without_id_match_stays_ambiguous(self):
        # A3 companion: with NO exact-id match, colliding labels remain
        # fail-closed ambiguous — never guess between two labelled peers.
        _write_endpoint(self.dir, "ep-1", label="Shared Name")
        _write_endpoint(self.dir, "ep-2", label="shared name")
        with self.assertRaises(rts.EndpointError):
            self.reg.resolve("Shared Name")

    def test_label_shadowing_prefix_is_ambiguous(self):
        # long-used prefix "prod" for prod-hetzner; a new peer labelled "prod"
        # must not silently steal every "prod" reference
        _write_endpoint(self.dir, "prod-hetzner", label="")
        _write_endpoint(self.dir, "xyz-9", label="prod")
        with self.assertRaises(rts.EndpointError):
            self.reg.resolve("prod")

    def test_unicode_nfd_twin_label_collides(self):
        # NFC vs NFD form of the same name must be detected as the same label
        import unicodedata as ud
        nfc = ud.normalize("NFC", "B\u00fcro")   # u-umlaut single code point
        nfd = ud.normalize("NFD", "B\u00fcro")   # u + combining diaeresis
        self.assertNotEqual(nfc, nfd)
        _write_endpoint(self.dir, "ep-1", label=nfc)
        _write_endpoint(self.dir, "ep-2", label=nfd)
        with self.assertRaises(rts.EndpointError):
            self.reg.resolve(nfc)

    def test_label_match_case_insensitive(self):
        _write_endpoint(self.dir, "ep-1", label="Papa Laptop")
        self.assertEqual(self.reg.resolve("papa laptop"), "ep-1")

    def test_ambiguous_label_raises(self):
        _write_endpoint(self.dir, "ep-1", label="Papa")
        _write_endpoint(self.dir, "ep-2", label="papa")
        with self.assertRaises(rts.EndpointError):
            self.reg.resolve("PAPA")

    def test_unique_prefix_match(self):
        _write_endpoint(self.dir, "0fb26896-1a77", label="")
        _write_endpoint(self.dir, "d7f7aeed-2b88", label="")
        self.assertEqual(self.reg.resolve("0fb2"), "0fb26896-1a77")

    def test_non_unique_prefix_falls_through(self):
        _write_endpoint(self.dir, "aa-1")
        _write_endpoint(self.dir, "aa-2")
        self.assertEqual(self.reg.resolve("aa"), "aa")

    def test_unknown_name_falls_through(self):
        self.assertEqual(self.reg.resolve("nope"), "nope")

    def test_empty_name_falls_through(self):
        self.assertEqual(self.reg.resolve(""), "")

    def test_peek_label_reads_disabled_endpoint(self):
        _write_endpoint(self.dir, "ep-1", label="Papa")
        cfg_path = self.dir / "ep-1.json"
        cfg = json.loads(cfg_path.read_text("utf-8"))
        cfg["enabled"] = False
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.reg.peek_label("ep-1"), "Papa")
        # load() itself must still refuse disabled endpoints
        with self.assertRaises(rts.EndpointError):
            self.reg.load("ep-1")

    def test_peek_label_rejects_traversal(self):
        self.assertEqual(self.reg.peek_label("../etc/passwd"), "")

    def test_peek_label_sanitizes_legacy_stored_label(self):
        # A4 (2026-07-20, defense-in-depth): labels stored BEFORE the
        # ingestion sanitizer existed may carry ANSI escapes / bidi overrides.
        # peek_label is a delivery point (MCP a2a_list_endpoints, resolve())
        # and must sanitize read-side too.
        rlo = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE (bidi spoofing)
        _write_endpoint(self.dir, "ep-1", label="Ok\x1b[31mRed" + rlo + "evil")
        label = self.reg.peek_label("ep-1")
        self.assertNotIn("\x1b", label)
        self.assertNotIn(rlo, label)
        self.assertIn("Ok", label)

    def test_resolve_of_disabled_labelled_endpoint_still_resolves(self):
        # resolve() maps the name; the enabled gate stays in load()/send()
        _write_endpoint(self.dir, "ep-1", label="Papa")
        cfg_path = self.dir / "ep-1.json"
        cfg = json.loads(cfg_path.read_text("utf-8"))
        cfg["enabled"] = False
        cfg_path.write_text(json.dumps(cfg), encoding="utf-8")
        self.assertEqual(self.reg.resolve("Papa"), "ep-1")


if __name__ == "__main__":
    unittest.main()
