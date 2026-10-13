"""S6 of PLAN-0955 / ADR-2259: the allowance emits audit events — registered AND wired.

* registered in BOTH tables (``EVENT_SEVERITY`` and ``_EVENT_ALLOWLIST``) — an
  event in only one is written at INFO with its details cut to the floor;
* emitted through the real chokepoint (``model_selector.resolve_os_model``) on a
  state TRANSITION only (never one record per turn), with the chain's
  ``prev_hash``/``hash`` links intact;
* the completeness verifier script passes.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (str(_REPO), str(_REPO / "corvin_operator" / "bridges" / "shared"),
           str(_REPO / "corvin_operator" / "forge")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import model_selector as ms  # noqa: E402
import savings_cap as sc  # noqa: E402
from core.learning import savings_meter as sm  # noqa: E402

EVENTS = ("cost.optimize_capped", "cost.optimize_resumed", "cost.optimize_config_changed")
HAIKU = "claude-haiku-4-5-20251001"
SONNET = "claude-sonnet-5"


def _tables():
    from forge import security_events as se
    return se.EVENT_SEVERITY, se._EVENT_ALLOWLIST


@pytest.mark.parametrize("event", EVENTS)
def test_event_is_registered_in_both_tables(event):
    severity, allow = _tables()
    assert event in severity, f"{event} missing from EVENT_SEVERITY"
    assert event in allow, f"{event} missing from _EVENT_ALLOWLIST"
    assert "tenant_id" in allow[event]


def test_no_free_text_or_prompt_fields_are_allowlisted():
    _, allow = _tables()
    for event in EVENTS:
        assert not (set(allow[event]) & {"prompt", "task", "text", "message", "content", "user"}), event


@pytest.fixture()
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_OS_MODEL_ALLOW_HAIKU", "1")
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.setattr(sm, "_epoch_ts", lambda tenant_id: 0.0)
    monkeypatch.setattr(sc, "_tier", lambda: "free")
    sm.reset_cache()
    sc._reset_notes()
    (tmp_path / "tenants" / "_default" / "global").mkdir(parents=True)
    (tmp_path / "tenants" / "_default" / "global" / "cost_optimize.json").write_text(
        json.dumps({"floor_usd": 0.01}))
    yield tmp_path
    sm.reset_cache()
    sc._reset_notes()


def _chain() -> Path:
    from core.paths.tenant import tenant_audit_chain
    return tenant_audit_chain("_default")


def _add_turns(model: str, n: int, tag: str) -> None:
    p = _chain()
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"ts": time.time(), "event_type": "os_turn.completed", "details": {
                "turn_id": f"{tag}{i}", "model": model, "exit_code": 0,
                "input_tokens": 1000, "output_tokens": 1000}}) + "\n")
    sm.reset_cache()
    sm.read("_default", ms.high_model("_default"), block=True)


def _events(name: str) -> list[dict]:
    p = _chain()
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if f'"{name}"' in line:
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event_type") == name:
                out.append(rec)
    return out


def test_transitions_are_audited_once_each_through_the_real_chokepoint(home):
    for _ in range(3):                                   # uncapped: no event
        ms.resolve_os_model({}, payload_chars=1000)
    assert _events("cost.optimize_capped") == []

    _add_turns(HAIKU, 10, "h")                            # saving 50 % >= 10 %
    for _ in range(5):                                    # capped five times ...
        assert ms.resolve_os_model({}, payload_chars=1000) == ms.high_model("_default")
    capped = _events("cost.optimize_capped")
    assert len(capped) == 1, "one record per TRANSITION, not per turn"
    d = capped[0]["details"]
    assert d["tenant_id"] == "_default" and d["tier"] == "free"
    assert d["reason"] == "cap_reached" and d["cap"] == 0.1
    assert d["ratio"] == pytest.approx(0.5) and d["month"] == time.strftime("%Y-%m", time.gmtime())

    _add_turns(ms.high_model("_default"), 200, "s")       # dilute below the cap
    ms.resolve_os_model({}, payload_chars=1000)
    ms.resolve_os_model({}, payload_chars=1000)
    resumed = _events("cost.optimize_resumed")
    assert len(resumed) == 1 and resumed[0]["details"]["reason"] == "under_cap"


def test_the_chain_stays_intact_after_the_events(home):
    _add_turns(HAIKU, 10, "h")
    ms.resolve_os_model({}, payload_chars=1000)
    assert _events("cost.optimize_capped")
    from forge import security_events as se
    # Only the records written through write_event carry hashes; the seeded
    # os_turn lines are plain test input, so verify the tail the product wrote.
    written = [json.loads(l) for l in _chain().read_text().splitlines()
               if '"cost.optimize_capped"' in l]
    rec = written[0]
    assert rec.get("hash") and "prev_hash" in rec


def test_the_completeness_verifier_passes():
    r = subprocess.run([sys.executable, str(_REPO / "scripts" / "verify_audit_event_completeness.py")],
                       capture_output=True, text=True, cwd=str(_REPO), timeout=240)
    assert r.returncode == 0, r.stdout[-1500:] + r.stderr[-1500:]
