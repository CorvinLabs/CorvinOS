"""ACS through the bridge's real entry point: ``process_one`` → delegation → outbox.

``test_acs_run_trace_e2e`` calls ``_maybe_delegate_worker`` directly. That proves the
function, not that ``process_one`` hands it the live status emitter — a one-word typo at
that call site would leave every test green and the live step lines dead. Here an inbox
envelope goes in and the outbox files are read back: the status files must carry the
step lines and the final message must carry the answer plus the run summary.

Only the engine calls (manager, worker, critique) and the gates are replaced.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX sandbox paths")

WORKER_TEXT = (
    "Die Quellen stimmen in drei von vier Punkten überein. Der Strompreis stieg zwischen "
    "2021 und 2024 in allen fünf Ländern, im Schnitt um etwa 40 Prozent, mit deutlichen "
    "Unterschieden zwischen den Quellen bei der Ursachenangabe."
)
DELEGATE = {
    "decision": "DELEGATE", "reasoning": "split by country",
    "subtasks": [
        {"id": "de", "instructions": "Germany prices", "expected_output": {"type": "object"},
         "success_criteria": "numbers", "priority": "normal",
         "budget_allocation": {"max_tokens": 2000, "max_tool_calls": 2}},
        {"id": "fr", "instructions": "France prices", "expected_output": {"type": "object"},
         "success_criteria": "numbers", "priority": "normal",
         "budget_allocation": {"max_tokens": 2000, "max_tool_calls": 2}},
    ],
}
COMPLETE = {"decision": "COMPLETE", "reasoning": "covered",
            "complete_artifacts": {"summary": WORKER_TEXT, "output_paths": []}}

_MISSING = object()


@contextmanager
def _module_overrides(**mods):
    saved = {k: sys.modules.get(k, _MISSING) for k in mods}
    sys.modules.update(mods)
    try:
        yield
    finally:
        for k, v in saved.items():
            if v is _MISSING:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    """A regression that makes delegation return None falls through to process_one's DIRECT turn
    (ADAPTER_FAKE_CLAUDE=0 below), which would start the real claude CLI."""
    from acs_test_guards import forbid_real_cli_and_network
    forbid_real_cli_and_network(monkeypatch)


def _fresh_adapter(monkeypatch, env: dict):
    for k, v in env.items():
        monkeypatch.setenv(k, v)       # restored after the test, never leaked into the next
    # Importing the adapter runs the licence boot refresh, whose key-integrity check opens an HTTPS
    # connection unless the process snapshot says test mode: once per test, 17 per file otherwise.
    try:
        import license.session_refresh as _sr  # type: ignore
        monkeypatch.setattr(_sr, "_TEST_MODE_SNAPSHOT", "1")
    except Exception:  # noqa: BLE001 — module unavailable => nothing to switch off
        pass
    sys.modules.pop("adapter", None)
    import adapter  # type: ignore
    # Hermetic: no real acceptable-use call to the model (test_adapter_btw does the same)
    # and no voice synthesis. Before this the test spawned a real `claude` haiku call and
    # three summarize.py processes and took 30 s.
    adapter._house_rules_classifier = lambda task, rules, auth, **_kw: ("", 1.0, "test-benign")
    # The turn's own L44 gate (spawn_gates.check_l44 -> house_rules.classify) looks the
    # classifier up in house_rules, not on the adapter: pin it there as well.
    import house_rules  # type: ignore
    monkeypatch.setattr(house_rules, "_house_rules_classifier",
                        lambda task, rules, auth, **_kw: ("", 1.0, "test-benign"))
    return adapter


def _send(adapter, inbox: Path, text: str):
    env = {"id": "msg-acs-1", "channel": "sandbox-acs", "from": "u_acs", "chat_id": "chat_acs",
           "text": text, "ts": time.time()}
    f = inbox / "msg-acs-1.json"
    f.write_text(json.dumps(env))
    adapter.process_one(f, settings={"whitelist": ["u_acs"], "progress_updates": True})


@pytest.fixture()
def sandbox():
    base = Path(tempfile.mkdtemp(prefix="acs-process-one-"))
    dirs = {n: base / n for n in ("inbox", "outbox", "processed")}
    for d in dirs.values():
        d.mkdir()
    yield dirs
    shutil.rmtree(base, ignore_errors=True)


def _env(dirs):
    return {"ADAPTER_INBOX": str(dirs["inbox"]), "ADAPTER_OUTBOX": str(dirs["outbox"]),
            "ADAPTER_PROCESSED": str(dirs["processed"]), "ADAPTER_FAKE_CLAUDE": "0",
            "ADAPTER_ROUTING_MODE": "off", "ADAPTER_DISABLE_VOICE": "1"}


def test_process_one_streams_acs_steps_to_the_outbox_and_ends_with_the_summary(sandbox, monkeypatch):
    import acs_runtime as rt
    import acs_gate_chain as gc
    import corvin_console.feature_flags as ff

    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    answers = iter([json.dumps(DELEGATE), json.dumps(COMPLETE)])
    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    with patch.object(ff, "is_enabled",
                      side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
         patch.object(ff, "worker_engine_mode", return_value="native"), \
         patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
         patch.object(adapter, "_check_egress_or_fail", return_value=None), \
         patch.object(rt, "_call_manager_sync", side_effect=lambda *a, **k: (next(answers), 50)), \
         patch.object(rt, "_call_worker_sync", side_effect=lambda *a, **k: (WORKER_TEXT, 30, None)), \
         patch.object(gc, "_run_critique_sync", return_value=None), \
         patch.object(rt, "_assert_engine_licensed", return_value=None), \
         patch.object(rt, "_write_audit", side_effect=lambda *a, **k: None), \
         _module_overrides(**{"license.compute_quota": quota, "acs_runtime": rt}):
        # /delegate forces ACS even in native mode, so the routing is not under test here.
        _send(adapter, sandbox["inbox"], "/delegate Vergleiche die Strompreise in zwei Ländern")

    status_texts = [json.loads(f.read_text())["text"]
                    for f in sorted(sandbox["outbox"].glob("msg-acs-1_s*.json"))]
    assert any(t.startswith("🧭 Iteration 1: Manager teilt in 2 Teilaufgaben") for t in status_texts), status_texts
    assert sum(t.startswith("🤖 Worker") for t in status_texts) == 2, status_texts
    # The gate line leads with the verdict's marker from the shared vocabulary (ADR-2269), not
    # a literal: this gate passed, so it is the "done" marker, and never the "failed" one.
    from status_markers import icon  # noqa: PLC0415
    gate = [t for t in status_texts if "Gate Iteration" in t and "höher = besser" in t]
    assert gate, status_texts
    assert all(t.startswith(icon("done") + " Gate") for t in gate), gate
    assert not any(t.startswith(icon("failed")) for t in gate), gate

    finals = [json.loads(f.read_text()) for f in sorted(sandbox["outbox"].glob("msg-acs-1_0*.json"))]
    assert finals, f"no final message in {[p.name for p in sandbox['outbox'].iterdir()]}"
    body = " ".join(f.get("text", "") for f in finals)
    assert WORKER_TEXT[:40] in body
    assert "ACS-Lauf" in body and "- Auslöser: /delegate" in body
    assert "- Loss-Score (höher = besser): " in body


def test_a_failed_acs_run_logs_its_reason_and_falls_back_to_the_direct_turn(sandbox, monkeypatch):
    """The R31 defect rejected every bridge ACS run at validation; only the log could say so."""
    import acs_runtime as rt
    import corvin_console.feature_flags as ff

    adapter = _fresh_adapter(monkeypatch, _env(sandbox))

    class _Failing:
        def __init__(self, **kw):
            pass

        async def run(self, spec, run_id=None, **kw):
            return rt.ACSResult(run_id=run_id or "x", workflow_id="w", status="failed",
                                error="workflow validation failed: [ERROR] R31 (max_depth)")

    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    logged: list[str] = []
    with patch.object(ff, "is_enabled",
                      side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
         patch.object(ff, "worker_engine_mode", return_value="native"), \
         patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
         patch.object(adapter, "_check_egress_or_fail", return_value=None), \
         patch.object(adapter, "log", side_effect=logged.append), \
         patch.object(rt, "ACSRuntime", _Failing), \
         _module_overrides(**{"license.compute_quota": quota, "acs_runtime": rt}):
        answer, _ = adapter._maybe_delegate_worker(
            "/delegate Vergleiche die Strompreise", channel="sandbox-acs", chat_key="c",
            persona="")

    assert answer is None, "a failed run must degrade to the direct turn"
    assert any("ended failed" in line and "R31" in line for line in logged), logged


# --- the big-data entry points (review finding: a mutant that dropped on_status / is_big_data on
# these paths left every test green) ---------------------------------------------------------------

BIG = "Analysiere die 3 GB Logdatei und fasse die Fehler zusammen."


def _drive_big_data(monkeypatch, sandbox, *, parity: bool):
    import acs_runtime as rt
    import acs_gate_chain as gc
    import corvin_console.feature_flags as ff

    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    answers = iter([json.dumps(DELEGATE), json.dumps(COMPLETE)])
    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    # parity=False is the flag-off carve-out (_maybe_delegate_big_data); parity=True the full ladder.
    enabled = {"bridge_big_data_delegation", "bridge_worker_engine_parity"} if parity else {"bridge_big_data_delegation"}
    with patch.object(ff, "is_enabled", side_effect=lambda fid, tid="_default": fid in enabled), \
         patch.object(ff, "worker_engine_mode", return_value="native"), \
         patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
         patch.object(adapter, "_check_egress_or_fail", return_value=None), \
         patch.object(rt, "_call_manager_sync", side_effect=lambda *a, **k: (next(answers), 50)), \
         patch.object(rt, "_call_worker_sync", side_effect=lambda *a, **k: (WORKER_TEXT, 30, None)), \
         patch.object(gc, "_run_critique_sync", return_value=None), \
         patch.object(rt, "_assert_engine_licensed", return_value=None), \
         patch.object(rt, "_write_audit", side_effect=lambda *a, **k: None), \
         _module_overrides(**{"license.compute_quota": quota, "acs_runtime": rt}):
        _send(adapter, sandbox["inbox"], BIG)
    status = [json.loads(f.read_text())["text"] for f in sorted(sandbox["outbox"].glob("msg-acs-1_s*.json"))]
    body = " ".join(json.loads(f.read_text()).get("text", "") for f in sorted(sandbox["outbox"].glob("msg-acs-1_0*.json")))
    return status, body


@pytest.mark.parametrize("parity", [True, False], ids=["full-ladder", "flag-off-carve-out"])
def test_a_big_data_turn_streams_steps_and_names_its_trigger_on_both_entry_points(sandbox, monkeypatch, parity):
    status, body = _drive_big_data(monkeypatch, sandbox, parity=parity)
    assert any(t.startswith("🧭 Iteration 1: Manager teilt in 2 Teilaufgaben") for t in status), status
    assert sum(t.startswith("🤖 Worker") for t in status) == 2, status
    assert "- Auslöser: große Datenmenge erkannt" in body, body


# --- the bridge spec: one budget source for both surfaces, a name that always validates -----------

def test_the_bridge_spec_uses_the_consoles_budget_source(monkeypatch, sandbox):
    import acs_validator
    import corvin_console.chat_runtime as cr
    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    monkeypatch.setattr(cr, "_delegation_budget", lambda tid: {
        "max_loops": 7, "max_depth": 3, "max_total_workers": 11, "max_wall_time": 900,
        "timeout_seconds": 600, "max_worker_turns": 40})
    spec = adapter._build_bridge_acs_spec("Vergleiche zwei Dinge", "discord", "worker-delegation", "_default")
    budget = spec["orchestration"]["delegation_loop"]["budget"]
    assert budget["max_total_workers"] == 11 and budget["max_wall_time"] == 900 and budget["max_depth"] == 3
    assert acs_validator.validate_workflow_dict(spec).ok


def test_the_bridge_spec_falls_back_to_a_valid_spec_without_a_budget(monkeypatch, sandbox):
    import acs_validator
    import corvin_console.chat_runtime as cr
    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    monkeypatch.setattr(cr, "_delegation_budget", lambda tid: (_ for _ in ()).throw(RuntimeError("unreadable")))
    spec = adapter._build_bridge_acs_spec("x", "discord", "worker-delegation", "_default")
    assert "budget" not in spec["orchestration"]["delegation_loop"]
    assert acs_validator.validate_workflow_dict(spec).ok     # the R31 shape: a budget WITHOUT max_depth fails


@pytest.mark.parametrize("channel", ["discord", "web chat", "Teams-EU", "123abc", "_x", "a", "ÄÖ", "x" * 200, ""])
def test_any_channel_name_yields_a_workflow_name_that_passes_rule_r2(monkeypatch, sandbox, channel):
    import acs_validator
    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    spec = adapter._build_bridge_acs_spec("Vergleiche zwei Dinge", channel, "worker-delegation", "_default")
    res = acs_validator.validate_workflow_dict(spec)
    assert res.ok, (channel, spec["workflow"]["name"], [str(e) for e in res.errors])


# --- the spec that actually reaches ACSRuntime.run (a mutant that swapped the budget source back to
# none, or hard-coded the tenant, left every direct test of _build_bridge_acs_spec green) -----------

def _capture_runtime(rt, store):
    class _Cap:
        def __init__(self, **kw):
            store["init"] = kw

        async def run(self, spec, run_id=None, **kw):
            store["spec"] = spec
            return rt.ACSResult(run_id=run_id or "r", workflow_id="w", status="success",
                                summary=WORKER_TEXT, final_output={"text": WORKER_TEXT})
    return _Cap


def test_the_spec_that_reaches_the_runtime_carries_the_tenants_budget(sandbox, monkeypatch):
    import acs_runtime as rt
    import corvin_console.chat_runtime as cr
    import corvin_console.feature_flags as ff

    adapter = _fresh_adapter(monkeypatch, _env(sandbox))
    seen: dict = {}
    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = lambda *a, **k: None
    asked: list = []

    def budget(tid):
        asked.append(tid)
        return {"max_loops": 9, "max_depth": 2, "max_total_workers": 5 if tid == "tenant-x" else 6,
                "max_wall_time": 700, "timeout_seconds": 300, "max_worker_turns": 11}

    monkeypatch.setattr(cr, "_delegation_budget", budget)
    with patch.object(ff, "is_enabled", side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
         patch.object(ff, "worker_engine_mode", return_value="native"), \
         patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
         patch.object(adapter, "_check_egress_or_fail", return_value=None), \
         patch.object(rt, "ACSRuntime", _capture_runtime(rt, seen)), \
         patch.object(rt, "_write_audit", side_effect=lambda *a, **k: None), \
         _module_overrides(**{"license.compute_quota": quota, "acs_runtime": rt}):
        answer, _ = adapter._maybe_delegate_worker(
            "/delegate Vergleiche zwei Dinge", channel="sandbox-acs", chat_key="c", persona="",
            tenant_id="tenant-x")
    assert answer and answer.startswith(WORKER_TEXT[:30])
    assert asked == ["tenant-x"], "the budget must be loaded for the turn's own tenant"
    b = seen["spec"]["orchestration"]["delegation_loop"]["budget"]
    assert b["max_total_workers"] == 5 and b["max_wall_time"] == 700 and b["max_depth"] == 2
    assert seen["init"]["tenant_id"] == "tenant-x"
