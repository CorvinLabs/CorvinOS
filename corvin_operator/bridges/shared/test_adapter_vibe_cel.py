"""E2E-wiring — Vibe Engineering CEL in the BRIDGE path (ADR-0275/0278).

Bridge parity with the web-chat wiring: proves the CEL brief is reached from the
real `_resolve_spawn_inputs` composition point (the single one both bridge spawn
paths share) behind the `vibe_engineering` flag, lands in the returned system
prompt, and drives persist_trace + the audit Decision Record. A unit test on
build_brief alone would not show that the bridge turn calls it.

Cases:
  * flag ON  → build_brief reached with the prompt; brief in system; trace +
    record emitted with the bridge session workdir + a turn id from msg_id.
  * flag OFF → build_brief NOT called; system prompt carries no brief.
  * CEL raises → turn still composes (fail-safe), no brief, no crash.

Run: python3 operator/bridges/shared/test_adapter_vibe_cel.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent.parent / "forge",
          _HERE.parent.parent.parent / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

_MARKER = "TEST-BRIDGE-CEL-MARKER-77"


class BridgeCelWiringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-vibe-"))
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["CORVIN_TENANT_ID"] = "_default"
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)
        for mod in ("adapter",):
            sys.modules.pop(mod, None)
        import adapter as ad  # noqa: E402
        self.ad = ad

    def tearDown(self) -> None:
        for k in ("CORVIN_HOME", "CORVIN_TENANT_ID", "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, *, flag_on, build_brief_spy, persist=None, emit=None):
        def _flag(fid, tid="_default"):
            return flag_on if fid == "vibe_engineering" else False
        with (
            patch.object(self.ad, "_CEL_AVAILABLE", True),
            patch.object(self.ad, "_cel_build_brief", build_brief_spy),
            patch.object(self.ad, "_cel_render",
                         return_value=f"## Context brief\n{_MARKER}"),
            patch.object(self.ad, "_cel_persist_trace", persist or MagicMock()),
            patch.object(self.ad, "_cel_emit_record", emit or MagicMock()),
            patch("corvin_console.feature_flags.is_enabled", side_effect=_flag),
        ):
            return self.ad._resolve_spawn_inputs(
                "erklär mir postgres indexes", "unrestricted", profile={},
                add_dir=None, channel="discord", chat_key="chatX", msg_id="msg_99")

    def test_flag_on_injects_brief_and_emits(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": [], "task_preview": "x"}))
        persist, emit = MagicMock(), MagicMock()
        out = self._resolve(flag_on=True, build_brief_spy=spy, persist=persist, emit=emit)
        self.assertIn(_MARKER, out["system"], "CEL brief must land in the bridge system prompt")
        self.assertTrue(spy.called, "build_brief must be reached from _resolve_spawn_inputs")
        self.assertIn("postgres", spy.call_args[0][0])
        self.assertTrue(persist.called, "trace must be persisted")
        self.assertTrue(emit.called, "audit Decision Record must be emitted")
        # turn id derives from msg_id; workdir is the bridge session dir
        self.assertEqual(emit.call_args.kwargs.get("turn_id"), "turn-msg_99")

    def test_flag_off_no_brief(self):
        spy = MagicMock(return_value=(MagicMock(), {"stages": []}))
        out = self._resolve(flag_on=False, build_brief_spy=spy)
        self.assertNotIn(_MARKER, out["system"])
        self.assertFalse(spy.called, "flag off: build_brief must not be called")

    def test_cel_error_is_fail_safe(self):
        def _boom(*a, **k):
            raise RuntimeError("CEL exploded")
        out = self._resolve(flag_on=True, build_brief_spy=_boom)
        self.assertNotIn(_MARKER, out["system"])
        self.assertIsInstance(out["system"], str, "turn still composes despite CEL error")


if __name__ == "__main__":
    unittest.main()
