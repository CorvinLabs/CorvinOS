"""The models chosen on the Console Models page are the ones the runtime uses.

Operator rule (2026-10-13): a model selected in the Console is ALWAYS the one
used - never a constant in a module and never the newest-of-family default.
This suite saves the choice through the REAL route (``PUT /v1/engine/config``,
the same call the Models page makes) and the worker pin through the tenant YAML
the Settings route writes, then asks every runtime path what it would run.

Every case first records the UNSAVED answer (the newest model) as a positive
control: a choice that happens to equal the default would otherwise prove
nothing, and a runtime path that ignores the Console would pass vacuously.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from corvin_console import auth as session_auth  # noqa: E402
from corvin_console import deps as console_deps  # noqa: E402
from corvin_console.routes import engine_api as EA  # noqa: E402

# Deliberately NOT the newest of their family (newest: haiku 5.5, sonnet 5.5).
HAIKU_45 = "claude-haiku-4-5-20251001"
SONNET_5 = "claude-sonnet-5"
OPUS_5 = "claude-opus-5"


def _fake_record(tenant_id: str = "_default") -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


class ConsoleSelectionRuntimeTests(unittest.TestCase):
    _ENV = ("CORVIN_HOME", "CORVIN_TENANT_ID", "CORVIN_HELPER_MODEL", "ANTHROPIC_MODEL",
            "CORVIN_ACS_WORKER_MODEL", "CORVIN_OS_MODEL_LOW", "CORVIN_OS_MODEL_HIGH",
            "CORVIN_OS_MODEL_ALLOW_HAIKU", "CORVIN_ENGINE_ID")

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._saved_env = {k: os.environ.get(k) for k in self._ENV}
        for k in self._ENV:
            os.environ.pop(k, None)
        for k in [k for k in os.environ if k.startswith("CORVIN_HELPER_MODEL_")]:
            os.environ.pop(k)
        os.environ["CORVIN_HOME"] = self._tmp.name

        import console_selection  # noqa: PLC0415
        import engine_models  # noqa: PLC0415
        import helper_model  # noqa: PLC0415
        import model_lineage  # noqa: PLC0415
        import model_selector  # noqa: PLC0415

        self.cs, self.em, self.hm = console_selection, engine_models, helper_model
        self.ml, self.ms = model_lineage, model_selector
        app = FastAPI()
        app.include_router(EA.router)
        app.dependency_overrides[console_deps.require_session] = lambda: _fake_record()
        app.dependency_overrides[console_deps.require_csrf] = lambda: None
        app.dependency_overrides[console_deps.require_session_csrf_on_mutation] = (
            lambda: _fake_record())
        self.client = TestClient(app)

    def tearDown(self) -> None:
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self._tmp.cleanup()

    # ── helpers ──────────────────────────────────────────────────────────

    def _put(self, tier: str, model: str, provider: str | None = None) -> None:
        r = self.client.put("/v1/engine/config", json={"models": {tier: {
            "task_type": tier, "selected_model": model, "provider": provider,
            "alternatives": []}}})
        self.assertEqual(r.status_code, 200, r.text)

    def _write_pin(self, **pins: str) -> None:
        d = Path(self._tmp.name) / "tenants" / "_default" / "global"
        d.mkdir(parents=True, exist_ok=True)
        lines = ["spec:", "  engine_models:", "    claude_code:"]
        lines += [f"      {k}: {v}" for k, v in pins.items()]
        f = d / "tenant.corvin.yaml"
        f.write_text("\n".join(lines) + "\n", encoding="utf-8")
        f.chmod(0o600)

    # ── helpers: the corvinOS row ────────────────────────────────────────

    def test_helper_model_follows_the_corvinos_row(self) -> None:
        newest = self.ml.latest("haiku")
        self.assertNotEqual(newest, HAIKU_45, "control needs a newer Haiku in the registry")
        self.assertEqual(self.hm.resolve_helper_model("voice_summary"), newest)  # unsaved
        self._put("corvinOS", f"anthropic/{HAIKU_45}")
        self.assertEqual(self.hm.resolve_helper_model("voice_summary"), HAIKU_45)
        self.assertEqual(self.hm.default_helper_model(), HAIKU_45)

    def test_console_choice_outranks_a_model_valued_env_pin(self) -> None:
        self._put("corvinOS", HAIKU_45)
        os.environ["CORVIN_HELPER_MODEL"] = "claude-sonnet-4-6"
        os.environ["CORVIN_HELPER_MODEL_VOICE_SUMMARY"] = "claude-opus-4-7"
        try:
            self.assertEqual(self.hm.resolve_helper_model("voice_summary"), HAIKU_45)
        finally:
            os.environ.pop("CORVIN_HELPER_MODEL_VOICE_SUMMARY", None)

    def test_env_opt_out_is_still_the_escape_hatch(self) -> None:
        self._put("corvinOS", HAIKU_45)
        os.environ["CORVIN_HELPER_MODEL"] = "none"
        self.assertIsNone(self.hm.resolve_helper_model("voice_summary"))

    def test_a_console_change_applies_to_the_next_call_without_restart(self) -> None:
        self._put("corvinOS", HAIKU_45)
        self.assertEqual(self.hm.default_helper_model(), HAIKU_45)
        self._put("corvinOS", "claude-haiku-5-5")
        self.assertEqual(self.hm.default_helper_model(), "claude-haiku-5-5")

    # ── OS turn: Tier 3 default ──────────────────────────────────────────

    def test_tier3_low_high_top_follow_simple_medium_complex(self) -> None:
        newest = {f: self.ml.latest(f) for f in ("haiku", "sonnet", "opus")}
        self.assertEqual(self.ms.low_model(), newest["haiku"])  # unsaved controls
        self.assertEqual(self.ms.high_model(), newest["sonnet"])
        self.assertEqual(self.ms.top_model(), newest["opus"])
        self._put("SIMPLE", HAIKU_45)
        self._put("MEDIUM", f"anthropic/{SONNET_5}")
        self._put("COMPLEX", OPUS_5)
        self.assertEqual(self.ms.low_model(), HAIKU_45)
        self.assertEqual(self.ms.high_model(), SONNET_5)
        self.assertEqual(self.ms.top_model(), OPUS_5)
        # the default turn (Tier 3) serves the MEDIUM choice, not "newest Sonnet"
        self.assertEqual(self.ms.autoselect_os_model(100), SONNET_5)

    # ── workload tier map (ADR-2104) ─────────────────────────────────────

    def test_workload_fast_and_full_follow_simple_and_medium(self) -> None:
        before = self.em.get_model_tier_mapping()["claude_code"]
        self.assertEqual(before["fast"], self.ml.latest("haiku"))
        self._put("SIMPLE", HAIKU_45)
        self._put("MEDIUM", SONNET_5)
        after = self.em.get_model_tier_mapping()["claude_code"]
        self.assertEqual(after, {"fast": HAIKU_45, "full": SONNET_5})

    # ── ACS ──────────────────────────────────────────────────────────────

    def test_acs_worker_pin_outranks_anthropic_model_env(self) -> None:
        import acs_runtime  # noqa: PLC0415

        self._write_pin(os_model="anthropic/claude-sonnet-5-5", worker_model=f"anthropic/{OPUS_5}")
        os.environ["ANTHROPIC_MODEL"] = "claude-sonnet-4-6"  # the parent turn's model
        self.assertEqual(acs_runtime._resolve_worker_model(None, "_default"), OPUS_5)

    def test_acs_worker_without_pin_uses_simple_row_then_newest_haiku(self) -> None:
        import acs_runtime  # noqa: PLC0415

        self.assertEqual(acs_runtime._resolve_worker_model(None, "_default"), self.ml.latest("haiku"))
        self._put("SIMPLE", HAIKU_45)
        self.assertEqual(acs_runtime._resolve_worker_model(None, "_default"), HAIKU_45)

    def test_acs_manager_follows_the_os_pin_then_the_medium_row(self) -> None:
        import acs_runtime  # noqa: PLC0415

        self.assertEqual(acs_runtime._default_manager_model("_default"), self.ml.latest("sonnet"))
        self._put("MEDIUM", SONNET_5)
        self.assertEqual(acs_runtime._default_manager_model("_default"), SONNET_5)
        self._write_pin(os_model="anthropic/claude-opus-5-5")
        self.assertEqual(acs_runtime._default_manager_model("_default"), "claude-opus-5-5")

    # ── guard rails ──────────────────────────────────────────────────────

    def test_an_external_provider_row_is_ignored_not_passed_to_the_cli(self) -> None:
        self._put("MEDIUM", "z-ai/glm-4.6", provider="openrouter")
        self.assertEqual(self.ms.high_model(), self.ml.latest("sonnet"))
        self.assertIsNone(self.cs.saved_tier_model("MEDIUM"))

    def test_unreadable_config_never_raises(self) -> None:
        d = Path(self._tmp.name) / "tenants" / "_default" / "global"
        d.mkdir(parents=True, exist_ok=True)
        (d / "model_selection_config.json").write_text("{not json", encoding="utf-8")
        self.assertIsNone(self.cs.saved_tier_model("SIMPLE"))
        self.assertEqual(self.hm.default_helper_model(), self.ml.latest("haiku"))


if __name__ == "__main__":
    unittest.main()
