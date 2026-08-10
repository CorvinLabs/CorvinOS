"""E2E wiring proof — the ACTIVE Context Brain in the bridge spawn path.

Drives the REAL entry point ``_resolve_spawn_inputs`` (both live spawn paths
compose their system prompt through it) and proves:

  * flag ON  → the active brain runs ``run_full_pipeline``; its synthesised prompt
    is injected into the turn's system prompt AND its class-re-validated forged
    tools are merged into ``allowed_tools`` (persona tools preserved).
  * flag OFF → run_full_pipeline is NEVER called; allowed_tools is unchanged
    (the deterministic-brief path, byte-identical to before this feature).
  * L35 egress denied → the active brain degrades (run_full_pipeline not called)
    even with the flag on — no cloud LLM synthesis under a zero-egress policy.

The run_full_pipeline result is mocked (it has its own hermetic E2E in
operator/context_engineering/tests/test_full_pipeline_e2e.py); this test's job is
the BRIDGE wiring, not the pipeline internals.

Run: python3 operator/bridges/shared/test_adapter_active_brain.py
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

_HERE = Path(__file__).resolve().parent
for p in (_HERE, _HERE.parent.parent / "forge",
          _HERE.parent.parent.parent / "core" / "console"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))


class _Tool:
    def __init__(self, name):
        self.name = name


class _Bundle:
    def __init__(self, synth=None, tools=None):
        self.synthesised_prompt = synth
        self.brief = None
        self.tools_to_bind = tools or []
        self.skills_to_bind = []


class ActiveBrainWiring(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="adapter-brain-"))
        os.environ["CORVIN_HOME"] = str(self.tmp)
        os.environ["ADAPTER_INBOX"] = str(self.tmp / "inbox")
        os.environ["ADAPTER_OUTBOX"] = str(self.tmp / "outbox")
        (self.tmp / "inbox").mkdir(exist_ok=True)
        (self.tmp / "outbox").mkdir(exist_ok=True)
        for mod in ("personal_tools", "user_style", "adapter"):
            sys.modules.pop(mod, None)
        import adapter as ad  # noqa: E402
        self.ad = ad
        import corvin_console.feature_flags as ff  # noqa: E402
        self.ff = ff

    def tearDown(self):
        for k in ("CORVIN_HOME", "ADAPTER_INBOX", "ADAPTER_OUTBOX"):
            os.environ.pop(k, None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _resolve(self, profile):
        return self.ad._resolve_spawn_inputs(
            "analysiere sales_2026.csv und zaehle pro Region", "unrestricted",
            profile=profile, add_dir=None, channel="discord",
            chat_key="chat_X", msg_id="m_1")

    def _flags(self, active: bool):
        on = {"vibe_engineering"} | ({"vibe_engineering_active"} if active else set())
        return lambda fid, tid=None: fid in on

    def test_flag_on_injects_synthesis_and_merges_forged_tools(self):
        bundle = _Bundle(synth="SYNTH: count orders per region from sales_2026.csv",
                         tools=[_Tool("mcp__forge__csv_region_count")])
        with patch.object(self.ad, "_cel_run_full",
                          return_value=(bundle, {"stages": []})) as rf, \
             patch.object(self.ad, "_check_house_rules_or_fail", return_value=None), \
             patch.object(self.ad, "_house_rules_cloud_egress_allowed", return_value=True), \
             patch.object(self.ff, "is_enabled", self._flags(active=True)):
            out = self._resolve(profile={"allowed_tools": ["Read", "Bash"]})
        self.assertTrue(rf.called, "active brain ran run_full_pipeline")
        self.assertIn("SYNTH: count orders per region", out["system"])
        self.assertIn("mcp__forge__csv_region_count", out["allowed_tools"])
        self.assertIn("Read", out["allowed_tools"], "persona tools preserved")
        self.assertIn("Bash", out["allowed_tools"])

    def test_flag_off_is_deterministic_no_forged_tools(self):
        with patch.object(self.ad, "_cel_run_full") as rf, \
             patch.object(self.ad, "_cel_build_brief", return_value=(None, {"stages": []})), \
             patch.object(self.ff, "is_enabled", self._flags(active=False)):
            out = self._resolve(profile={"allowed_tools": ["Read"]})
        self.assertFalse(rf.called, "active brain NOT run with flag off")
        self.assertEqual(out["allowed_tools"], ["Read"], "no forged tools merged")

    def test_egress_denied_degrades_even_with_flag_on(self):
        with patch.object(self.ad, "_cel_run_full") as rf, \
             patch.object(self.ad, "_cel_build_brief", return_value=(None, {"stages": []})), \
             patch.object(self.ad, "_house_rules_cloud_egress_allowed", return_value=False), \
             patch.object(self.ff, "is_enabled", self._flags(active=True)):
            out = self._resolve(profile={"allowed_tools": ["Read"]})
        self.assertFalse(rf.called, "L35 egress denied → active brain degraded")
        self.assertEqual(out["allowed_tools"], ["Read"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
