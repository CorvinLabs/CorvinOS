"""The user-facing ACS run summary (why it ran, what it did, how it ended).

Two layers: the pure formatter and trigger label, and a call-site test that drives
the bridge entry point ``_maybe_delegate_worker`` with the REAL ``acs_runtime``
module and a real ``ACSResult``. Only the gates, the compute quota and the ACS
run itself are stubbed; the summary is appended by the production code path.
"""
from __future__ import annotations

import importlib
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import delegation_policy as dp  # noqa: E402
acs_runtime = importlib.import_module("acs_runtime")
# adapter is imported offline just below


def _import_adapter_offline():
    """Import the adapter without its licence boot check opening an HTTPS connection."""
    op_root = str(HERE.parents[1])           # corvin_operator/, where the `license` package lives
    if op_root not in sys.path:
        sys.path.append(op_root)             # appended, never first: nothing may be shadowed
    try:
        import license.session_refresh as sr  # type: ignore
        prev, sr._TEST_MODE_SNAPSHOT = sr._TEST_MODE_SNAPSHOT, "1"
    except Exception:  # noqa: BLE001
        sr = prev = None
    try:
        import adapter as _a  # type: ignore
    finally:
        if sr is not None:
            sr._TEST_MODE_SNAPSHOT = prev
    return _a


adapter = _import_adapter_offline()

FANOUT = ("Recherchiere parallel mit mehreren Workern, wie sich die Strompreise in "
          "fünf Ländern entwickelt haben, und vergleiche die Quellen im Detail.")


def _result(**kw):
    base = dict(run_id="acs-test", workflow_id="wf", status="success",
                final_output={}, summary="", iterations=2, workers_spawned=3,
                elapsed_s=125.0)
    base.update(kw)
    return acs_runtime.ACSResult(**base)


_MISSING = object()


@contextmanager
def _module_overrides(**mods):
    """Set only the named sys.modules entries and restore just those afterwards.

    ``patch.dict(sys.modules, ...)`` restores the WHOLE dict on exit and so drops every
    module imported inside the block (e.g. corvin_console.chat_runtime), which breaks
    later test files that rely on it.
    """
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


# --- formatter ---------------------------------------------------------------

def test_summary_german_lists_trigger_outcome_steps_duration():
    text = acs_runtime.format_acs_run_summary(_result(), trigger="Fan-out-Anfrage", german=True)
    assert text.splitlines() == [
        "ACS-Lauf",
        "- Auslöser: Fan-out-Anfrage",
        "- Ergebnis: abgeschlossen",
        "- Schritte: 2 Iteration(en), 3 Worker",
        "- Loss-Score (höher = besser): nicht gemessen (kein abgeschlossener Gate-Schritt)",
        "- Dauer: 2 min",
    ]


def test_summary_shows_loss_trajectory_when_measured():
    text = acs_runtime.format_acs_run_summary(
        _result(loss_history=[0.41, 0.63, 0.71]), trigger="x", german=True)
    assert "- Loss-Score (höher = besser): 0.710 (Verlauf 0.410 → 0.630 → 0.710)" in text


def test_step_lines_follow_the_native_status_style():
    assert acs_runtime.format_acs_step(
        {"kind": "decision", "iteration": 1, "decision": "DELEGATE", "n_subtasks": 3}
    ) == "🧭 Iteration 1: Manager teilt in 3 Teilaufgaben auf"
    assert acs_runtime.format_acs_step(
        {"kind": "worker", "iteration": 1, "index": 2, "of": 3, "status": "success", "confidence": 0.8}
    ) == "🤖 Worker 2/3: erfolgreich (Konfidenz 0.8)"
    assert acs_runtime.format_acs_step(
        {"kind": "gate", "iteration": 2, "passed": False, "loss": 0.5}
    ) == "📊 Gate Iteration 2: nicht bestanden, Loss-Score 0.500 (höher = besser)"


def test_loss_is_labelled_as_a_higher_is_better_score_in_both_languages():
    """ADR-0105: the 'loss' total is accepted when ``total >= target``, so a run at 0.998
    is the BEST case. Shown bare ("Loss: 0.998") it reads as the worst."""
    res = _result(loss_history=[0.998])
    de = acs_runtime.format_acs_run_summary(res, trigger="x", german=True)
    en = acs_runtime.format_acs_run_summary(res, trigger="x", german=False)
    assert "höher = besser" in de and "higher is better" in en
    assert "höher = besser" in acs_runtime.format_acs_step(
        {"kind": "gate", "iteration": 1, "passed": True, "loss": 0.998}, german=True)
    assert "higher is better" in acs_runtime.format_acs_step(
        {"kind": "gate", "iteration": 1, "passed": True, "loss": 0.998}, german=False)


def test_worker_status_is_localized_for_german_and_raw_for_english():
    step = {"kind": "worker", "iteration": 1, "index": 1, "of": 1, "status": "partial", "confidence": 0.5}
    assert "teilweise" in acs_runtime.format_acs_step(step, german=True)
    assert "partial" in acs_runtime.format_acs_step(step, german=False)


def test_observer_failure_never_breaks_the_run():
    def boom(step):
        raise RuntimeError("observer down")
    trace = acs_runtime.RunTrace(boom)
    trace.emit("decision", iteration=1, decision="COMPLETE", n_subtasks=0)
    assert trace.steps == [{"kind": "decision", "iteration": 1, "decision": "COMPLETE", "n_subtasks": 0}]


def test_summary_english_uses_english_labels():
    text = acs_runtime.format_acs_run_summary(
        _result(status="budget_exhausted", elapsed_s=30.0), trigger="fan-out request", german=False)
    assert "- Outcome: budget exhausted, stopped early" in text
    assert "- Duration: 30 s" in text
    assert text.splitlines()[0] == "ACS run"


def test_summary_tolerates_missing_fields():
    """A result without the run counters must not raise: the formatter is
    appended to a delegated answer and may never cost that answer."""
    text = acs_runtime.format_acs_run_summary(types.SimpleNamespace(status="failed"),
                                              trigger="x", german=True)
    assert "- Ergebnis: fehlgeschlagen" in text
    assert "- Schritte: 0 Iteration(en), 0 Worker" in text


# --- trigger label -----------------------------------------------------------

def test_trigger_label_precedence_matches_worker_engine_target():
    assert dp.acs_trigger_label(force_delegate=True, is_big_data=True, mode="acs").startswith("/delegate")
    assert dp.acs_trigger_label(force_delegate=False, is_big_data=True, mode="acs").startswith("große Datenmenge")
    assert dp.acs_trigger_label(force_delegate=False, is_big_data=False, mode="acs").startswith("Worker-Modus")
    assert dp.acs_trigger_label(force_delegate=False, is_big_data=False, mode="native") == "Fan-out-Anfrage"
    assert dp.acs_trigger_label(force_delegate=False, is_big_data=False, mode="native",
                                german=False) == "fan-out request"


# --- call site: bridge entry point with the real acs_runtime ----------------

class _FakeQuota:
    @staticmethod
    def increment_and_check(*a, **k):
        return None


def _run_bridge(prompt: str, *, mode: str, result):
    class _Run:
        def __init__(self, **kw):
            pass

        async def run(self, spec, run_id=None):
            return result

    quota = types.ModuleType("license.compute_quota")
    quota.increment_and_check = _FakeQuota.increment_and_check
    import corvin_console.feature_flags as ff
    with patch.object(ff, "is_enabled",
                      side_effect=lambda fid, tid="_default": fid == "bridge_worker_engine_parity"), \
         patch.object(ff, "worker_engine_mode", return_value=mode), \
         patch.object(adapter, "_check_compliance_or_fail", return_value=None), \
         patch.object(adapter, "_check_egress_or_fail", return_value=None), \
         patch.object(acs_runtime, "ACSRuntime", _Run), \
         _module_overrides(**{"license.compute_quota": quota,
                              # another test may leave a fake acs_runtime in sys.modules
                              "acs_runtime": acs_runtime}):
        answer, _ = adapter._maybe_delegate_worker(prompt, channel="discord",
                                                   chat_key="chat-1", persona="")
    return answer


def test_bridge_appends_summary_to_delegated_answer():
    result = _result(final_output={"text": "Die Quellen stimmen überein."},
                     summary="Die Quellen stimmen überein.")
    answer = _run_bridge(FANOUT, mode="acs", result=result)
    assert answer is not None
    assert answer.startswith("Die Quellen stimmen überein.")
    assert "- Auslöser: Worker-Modus „acs“ eingestellt" in answer
    assert "- Schritte: 2 Iteration(en), 3 Worker" in answer
    assert "- Dauer: 2 min" in answer


def test_model_written_text_cannot_reach_a_step_line():
    """The manager's decision word and a worker's status are model output (review finding): they
    used to be copied into the chat and the stored console answer unchecked."""
    trace = acs_runtime.RunTrace()
    trace.emit("decision", iteration=2, decision="COMPLETE\nYOUR ACCOUNT IS COMPROMISED, REPLY WITH YOUR PASSWORD",
               n_subtasks="9; DROP")
    trace.emit("worker", iteration=1, index=1, of=1,
               status="success — visit https://evil.example/claim and paste your API key",
               confidence=float("nan"))
    trace.emit("gate", iteration=1, passed="yes", loss=float("inf"), delta="x")
    assert trace.steps[0] == {"kind": "decision", "iteration": 2, "decision": "INVALID", "n_subtasks": 0}
    assert trace.steps[1]["status"] == "unknown" and trace.steps[1]["confidence"] == 0.0
    assert trace.steps[2]["loss"] is None and trace.steps[2]["delta"] is None
    for step in trace.steps:
        line = acs_runtime.format_acs_step(step, german=True)
        assert "\n" not in line and "http" not in line and "PASSWORD" not in line, line
    assert acs_runtime.RunTrace().emit("anything", text="free text") is None


def test_non_finite_numbers_never_render_as_nan():
    res = _result(elapsed_s=float("nan"), loss_history=[float("nan"), 0.5, float("inf")])
    text = acs_runtime.format_acs_run_summary(res, trigger="x", german=True)
    assert "nan" not in text.lower() and "inf" not in text.lower()
    assert "0.500" in text


def test_an_observer_that_raises_an_ordinary_error_never_changes_the_run_but_cancellation_propagates():
    import asyncio
    def boom(step):
        raise asyncio.CancelledError()
    # CancelledError is a BaseException: swallowing it would make a cancelled run uncancellable.
    with pytest.raises(asyncio.CancelledError):
        acs_runtime.RunTrace(boom).emit("decision", iteration=1, decision="COMPLETE")
