"""S4 of PLAN-0955 / ADR-2259: the cap is wired into the ONE resolver both surfaces call.

Three layers of proof (CLAUDE.md § E2E Wiring Proof):

1. Reachability guard — nothing in production code reaches the automatic tiers
   except through ``resolve_os_model`` (no caller of ``resolve_os_model_with_source``,
   ``autoselect_os_model`` or ``classify_os_model`` outside ``model_selector.py``),
   and BOTH surfaces call ``resolve_os_model``.
2. Behaviour through the real bridge entry point ``adapter._resolve_spawn_inputs``
   (the function both live spawn paths compose their inputs through) and through the
   resolver the console holds, over a REAL chain under a sandboxed CORVIN_HOME.
3. Every capped case has an UNCAPPED positive control: without it a resolver that
   ignores the cap, or a measurement that never reaches it, would pass vacuously.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path

import pytest
import yaml

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO), str(_HERE), str(_HERE.parent.parent / "forge"),
           str(_REPO / "core" / "console")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import model_selector as ms  # noqa: E402
from core.learning import savings_meter as sm  # noqa: E402
import savings_cap as sc  # noqa: E402

HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"
NOW = time.time()


# ── 1. reachability guard ────────────────────────────────────────────────────

_AUTOMATIC_ENTRYPOINTS = ("resolve_os_model_with_source(", "autoselect_os_model(", "classify_os_model(")


def _production_py_files():
    for root in ("corvin_operator", "core"):
        for p in (_REPO / root).rglob("*.py"):
            parts = set(p.parts)
            if parts & {"tests", "test", "node_modules", ".venv", "__pycache__"}:
                continue
            if p.name.startswith("test_"):
                continue
            yield p


def test_no_production_path_reaches_the_automatic_tiers_around_the_cap():
    offenders = []
    for p in _production_py_files():
        if p.name == "model_selector.py":
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for ep in _AUTOMATIC_ENTRYPOINTS:
            for m in re.finditer(re.escape(ep), text):
                line_start = text.rfind("\n", 0, m.start()) + 1
                line = text[line_start:text.find("\n", m.start())]
                if line.lstrip().startswith(("def ", "#", "'", '"')):
                    continue
                offenders.append(f"{p.relative_to(_REPO)}: {line.strip()[:90]}")
    assert offenders == [], offenders


def test_both_surfaces_call_the_capped_resolver():
    for rel, needle in (("corvin_operator/bridges/shared/adapter.py", "_ms.resolve_os_model("),
                        ("core/console/corvin_console/chat_runtime.py", "_model_selector.resolve_os_model(")):
        text = (_REPO / rel).read_text(encoding="utf-8")
        assert needle in text, rel
        assert "resolve_os_model_with_source" not in text, rel


# ── 2. behaviour over a real chain ───────────────────────────────────────────

@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    for k in ("CORVIN_OS_MODEL_OVERRIDE", "CORVIN_OS_MODEL_AUTOSELECT"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_OS_MODEL_ALLOW_HAIKU", "1")      # Tier 3 may pick Haiku
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: 0.0)
    monkeypatch.setattr(sc, "_tier", lambda: "free")
    sm.reset_cache()
    (tmp_path / "tenants" / "_default" / "global").mkdir(parents=True)
    yield tmp_path
    sm.reset_cache()


def _seed_saving(home: Path, haiku_turns: int = 10):
    from core.paths.tenant import tenant_audit_chain
    p = tenant_audit_chain("_default")
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for n in range(haiku_turns):
            fh.write(json.dumps({"ts": NOW, "event_type": "os_turn.completed", "details": {
                "turn_id": f"h{n}", "model": HAIKU, "exit_code": 0,
                "input_tokens": 1000, "output_tokens": 1000}}) + "\n")
    sm.reset_cache()
    # prime the in-memory aggregate the way a warm process has it
    sm.read("_default", ms.high_model("_default"), block=True)


def _settings(home: Path, **kw):
    (home / "tenants" / "_default" / "global" / "cost_optimize.json").write_text(json.dumps(kw))


def _pin_tenant_os_model(home: Path, model: str):
    (home / "tenants" / "_default" / "global" / "tenant.corvin.yaml").write_text(
        yaml.dump({"spec": {"engine_models": {"claude_code": {"os_model": model}}}}))


def test_positive_control_automatic_tier_picks_haiku_when_uncapped(env):
    assert ms.resolve_os_model({}, payload_chars=1000) == ms.low_model("_default")


def test_cap_reached_turns_the_downgrade_into_the_default_model(env):
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    capped = ms.resolve_os_model({}, payload_chars=1000)
    assert capped == ms.high_model("_default") != ms.low_model("_default")


def test_a_pinned_model_is_never_capped(env):
    _pin_tenant_os_model(env, HAIKU)
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    assert sc.evaluate("_default", default_model=ms.high_model("_default"), block=True).capped is True
    assert ms.resolve_os_model({}, payload_chars=1000) == HAIKU         # tenant pin wins
    assert ms.resolve_os_model({"model": HAIKU}, payload_chars=1000) == HAIKU   # profile pin wins


def test_upgrades_pass_through_the_cap(env):
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    hint = {"recommended_model": "opus", "confidence": 0.9, "task_type": "x"}
    got = ms.resolve_os_model({}, payload_chars=1000, ato_plan_hint=hint)
    assert got == ms.top_model("_default")                              # Opus is not a saving


def test_automation_off_choice_applies_without_any_measurement(env):
    _settings(env, cap_ratio=0.0)
    assert ms.resolve_os_model({}, payload_chars=1000) == ms.high_model("_default")


def test_member_is_not_capped_by_default(env, monkeypatch):
    monkeypatch.setattr(sc, "_tier", lambda: "member")
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    assert ms.resolve_os_model({}, payload_chars=1000) == ms.low_model("_default")


def test_a_cap_fault_never_changes_the_turn(env, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("cap broke")
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    monkeypatch.setattr(sc, "evaluate", boom)
    assert ms.resolve_os_model({}, payload_chars=1000) == ms.low_model("_default")


def test_the_bridge_entry_point_spawn_inputs_carries_the_capped_model(env, monkeypatch):
    """Real bridge entry: adapter._resolve_spawn_inputs (both live spawn paths use it)."""
    monkeypatch.setenv("ADAPTER_INBOX", str(env / "inbox"))
    monkeypatch.setenv("ADAPTER_OUTBOX", str(env / "outbox"))
    (env / "inbox").mkdir(exist_ok=True)
    (env / "outbox").mkdir(exist_ok=True)
    for mod in ("personal_tools", "user_style", "adapter"):
        sys.modules.pop(mod, None)
    import adapter as ad

    def spawn():
        return ad._resolve_spawn_inputs(
            "say hi", "unrestricted", profile={"allowed_tools": ["Read"]},
            add_dir=None, channel="discord", chat_key="chat_cap", msg_id="m1")

    assert spawn()["model"] == ms.low_model("_default")                 # positive control
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    assert spawn()["model"] == ms.high_model("_default")                # capped, same call


def test_the_console_holds_the_same_capped_resolver(env):
    from corvin_console import chat_runtime as cr
    assert cr._model_selector is not None
    assert cr._model_selector.resolve_os_model is ms.resolve_os_model or \
        cr._model_selector.resolve_os_model.__module__.endswith("model_selector")
    assert cr._model_selector.resolve_os_model({}, payload_chars=1000) == ms.low_model("_default")  # control
    _settings(env, floor_usd=0.01)
    _seed_saving(env)
    assert cr._model_selector.resolve_os_model({}, payload_chars=1000) == ms.high_model("_default")
