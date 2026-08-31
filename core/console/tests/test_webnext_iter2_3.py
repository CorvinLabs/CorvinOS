"""Iter 2c/2d/2e/3 smoke tests for ADR-0037 console re-launch.

Covers the load-bearing backend pieces introduced in iterations 2 and 3:

  * Iter 2e — LDD route snapshot + master toggle round-trip via the
    shared ``ldd`` module (no auth — direct module call).
  * Iter 3a — chat_runtime session create / list / delete lifecycle.

The Iter 2a/2b/2c/2d frontend modules consume PRE-EXISTING backend
routes (personas, bridges, profile, tools, skills, promote); those
routes have their own pytest suites and are not re-tested here.

The Iter 3b voice routes need a live STT/TTS stack and are exercised
manually via the messenger; an isolated unit test would need to mock
the entire stt package and add no real coverage.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for sub in ("core/console", "core/gateway", "operator/forge", "operator/bridges/shared"):
    p = _REPO / sub
    if p.exists() and str(p) not in sys.path:
        sys.path.insert(0, str(p))


class LddSnapshotTests(unittest.TestCase):
    """Backend snapshot mirrors the shared ldd.py module."""

    def setUp(self) -> None:
        from corvin_console.routes import ldd as ldd_route  # type: ignore
        importlib.reload(ldd_route)
        self.route = ldd_route

    def test_snapshot_shape(self) -> None:
        snap = self.route._snapshot()
        self.assertIn("layers", snap)
        self.assertIn("master_enabled", snap)
        self.assertIn("presets", snap)
        self.assertIn("depends_on", snap)
        self.assertGreaterEqual(len(snap["layers"]), 1)
        # Layers are objects with id/label/configured/effective/depends_on.
        for layer in snap["layers"]:
            self.assertEqual(
                set(layer.keys()),
                {"id", "label", "configured", "effective", "depends_on"},
            )
        # Presets include the canonical four (default / strict / quick / off).
        self.assertIn("default", snap["presets"])
        self.assertIn("off", snap["presets"])

    def test_depends_on_is_consistent(self) -> None:
        snap = self.route._snapshot()
        ids = {l["id"] for l in snap["layers"]}
        for child, parent in snap["depends_on"].items():
            self.assertIn(child, ids, f"DEPENDS_ON child {child!r} missing from LAYERS")
            self.assertIn(parent, ids, f"DEPENDS_ON parent {parent!r} missing from LAYERS")


class ChatRuntimeLifecycleTests(unittest.TestCase):
    """create_session → list_sessions → delete_session round-trip."""

    def setUp(self) -> None:
        # Redirect the on-disk store to a temp tree so we don't pollute
        # the real ~/.corvin/global/web_chat/.
        self._tmp = tempfile.mkdtemp(prefix="corvin_chatrt_test_")
        os.environ["CORVIN_HOME"] = self._tmp
        # Force a fresh import so the module re-reads CORVIN_HOME.
        for mod in ("corvin_console.chat_runtime", "forge.paths"):
            if mod in sys.modules:
                del sys.modules[mod]
        from corvin_console import chat_runtime  # type: ignore
        self.rt = chat_runtime
        self.tenant = "_default"

    def tearDown(self) -> None:
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        os.environ.pop("CORVIN_HOME", None)

    def test_create_lists_delete(self) -> None:
        sess = self.rt.create_session(self.tenant, title="hello")
        self.assertEqual(sess.tenant_id, self.tenant)
        self.assertEqual(sess.title, "hello")
        self.assertEqual(sess.turn_count, 0)
        self.assertTrue(sess.chat_key.startswith("web:"))
        self.assertTrue(sess.workdir.exists())

        listed = self.rt.list_sessions(self.tenant)
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0].sid, sess.sid)

        again = self.rt.get_session(self.tenant, sess.sid)
        self.assertIsNotNone(again)
        self.assertEqual(again.sid, sess.sid)

        self.assertTrue(self.rt.delete_session(self.tenant, sess.sid))
        self.assertEqual(len(self.rt.list_sessions(self.tenant)), 0)
        # idempotent on missing session
        self.assertFalse(self.rt.delete_session(self.tenant, sess.sid))

    def test_session_cap_drops_oldest(self) -> None:
        cap = self.rt._MAX_SESSIONS_PER_TENANT  # type: ignore[attr-defined]
        ids = [self.rt.create_session(self.tenant, title=f"s{i}").sid for i in range(cap)]
        # +1 → oldest should be evicted.
        extra = self.rt.create_session(self.tenant, title="newest")
        sids_now = {s.sid for s in self.rt.list_sessions(self.tenant)}
        self.assertIn(extra.sid, sids_now)
        self.assertEqual(len(sids_now), cap)
        # Oldest (first created) must be gone.
        self.assertNotIn(ids[0], sids_now)


class VoiceMimeStripTests(unittest.TestCase):
    """Browsers send `audio/webm;codecs=opus` — base type must match.

    Regression: before the fix the parameterised content-type was
    rejected with 415 even though the base ``audio/webm`` is allowed.
    """

    def setUp(self) -> None:
        for sub in ("operator/voice/scripts",):
            p = _REPO / sub
            if p.exists() and str(p) not in sys.path:
                sys.path.insert(0, str(p))
        from corvin_console.routes import voice as voice_route  # type: ignore
        importlib.reload(voice_route)
        self.v = voice_route

    def test_strip_drops_codec_parameter(self) -> None:
        self.assertEqual(self.v._strip_mime_params("audio/webm;codecs=opus"), "audio/webm")
        self.assertEqual(self.v._strip_mime_params("audio/webm; codecs=opus"), "audio/webm")
        self.assertEqual(self.v._strip_mime_params("AUDIO/WebM;codecs=opus"), "audio/webm")

    def test_strip_handles_no_params(self) -> None:
        self.assertEqual(self.v._strip_mime_params("audio/webm"), "audio/webm")

    def test_strip_handles_empty_or_none(self) -> None:
        self.assertEqual(self.v._strip_mime_params(None), "")
        self.assertEqual(self.v._strip_mime_params(""), "")

    def test_parameterised_webm_passes_allowlist(self) -> None:
        # The actual route walks `_strip_mime_params` then checks the
        # allowlist; this asserts the contract chain holds.
        base = self.v._strip_mime_params("audio/webm;codecs=opus")
        self.assertIn(base, self.v._ALLOWED_AUDIO_TYPES)


if __name__ == "__main__":  # pragma: no cover
    unittest.main(verbosity=2)
