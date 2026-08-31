"""Regression test: console (profile=None) and bridge (profile={}) must
resolve the SAME OS model from the SAME tenant config (Tier 2.5, ADR-0119).

Before this fix, ``chat_runtime.py`` (console web-chat) never consulted
``spec.engine_models.<engine_id>.os_model`` at all — only the bridge adapter
did. The console's own "OS Model" setting under Settings -> AI Engines had
no effect on the console's own chat. Both surfaces now call the same
``model_selector.resolve_os_model()`` (Tier 2.5), so they cannot diverge.
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

THIS = Path(__file__).resolve()
SHARED = THIS.parent
if str(SHARED) not in sys.path:
    sys.path.insert(0, str(SHARED))

import model_selector  # type: ignore  # noqa: E402

_ADR24_VARS = (
    "CORVIN_OS_MODEL_OVERRIDE",
    "CORVIN_OS_MODEL_AUTOSELECT",
    "CORVIN_OS_MODEL_ALLOW_HAIKU",
    "CORVIN_HOME",
)


class TierTwoFiveSingleSourceOfTruthTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {k: os.environ.pop(k, None) for k in _ADR24_VARS}
        self._tmp_home = tempfile.mkdtemp(prefix="os-model-sos-home-")
        os.environ["CORVIN_HOME"] = self._tmp_home
        tenant_dir = Path(self._tmp_home) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True, exist_ok=True)
        yaml_path = tenant_dir / "tenant.corvin.yaml"
        yaml_path.write_text(yaml.dump({
            "spec": {
                "engine_models": {
                    "claude_code": {
                        "os_model": "claude-haiku-4-5-20251001",
                        "worker_model": "claude-opus-5",
                    }
                }
            }
        }))

    def tearDown(self) -> None:
        for k, v in self._saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import shutil
        shutil.rmtree(self._tmp_home, ignore_errors=True)

    def test_console_profile_none_reaches_tier_2_5(self) -> None:
        """The console call site (profile=None) must honor the tenant's
        per-engine os_model setting — this is the exact bug being fixed."""
        result = model_selector.resolve_os_model(
            None, payload_chars=0, engine_id="claude_code", tenant_id="_default",
        )
        self.assertEqual(result, "claude-haiku-4-5-20251001")

    def test_bridge_profile_dict_reaches_tier_2_5(self) -> None:
        """The bridge call site (profile={}) resolves identically."""
        result = model_selector.resolve_os_model(
            {}, payload_chars=0, engine_id="claude_code", tenant_id="_default",
        )
        self.assertEqual(result, "claude-haiku-4-5-20251001")

    def test_console_and_bridge_agree(self) -> None:
        """Both surfaces, same config, same answer — the actual parity claim."""
        console = model_selector.resolve_os_model(
            None, payload_chars=0, engine_id="claude_code", tenant_id="_default",
        )
        bridge = model_selector.resolve_os_model(
            {}, payload_chars=0, engine_id="claude_code", tenant_id="_default",
        )
        self.assertEqual(console, bridge)


if __name__ == "__main__":
    unittest.main()
