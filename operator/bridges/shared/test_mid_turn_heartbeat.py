"""Tests for mid-turn heartbeat + status (ADR-0551 C1 variant B).

Measures the SUM: does a status/liveness envelope actually land in the outbox,
with the right content, cadence, and bounds.
Run: .venv/bin/python -m pytest operator/bridges/shared/test_mid_turn_heartbeat.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mid_turn_heartbeat as mth  # noqa: E402


def _envs(outbox: Path) -> list:
    return [json.loads(p.read_text()) for p in sorted(outbox.glob("*.json"))]


def _texts(outbox: Path) -> list:
    return [e["text"] for e in _envs(outbox)]


# ── marker parsing / stripping ──────────────────────────────────────────────

def test_parse_and_strip_all_marker_kinds():
    reply = ("Starte. ⟦bgtask:Bridge-Fix⟧ dann ⟦bgstep:Bridge-Fix|Phase 2/4: E2E⟧ "
             "und ⟦bgdone:Bridge-Fix⟧ fertig.")
    assert mth.parse_markers(reply) == ["Bridge-Fix"]
    assert mth.parse_steps(reply) == [("Bridge-Fix", "Phase 2/4: E2E")]
    assert mth.parse_done(reply) == ["Bridge-Fix"]
    s = mth.strip_markers(reply)
    assert "⟦bg" not in s and "Phase 2/4" not in s
    assert s.startswith("Starte.")


# ── status line: fires on change, immediate, not duplicated ─────────────────

def test_status_line_fires_immediately_on_change(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    mth.update_status(state, "sess", channel="discord", chat_id="c",
                      label="X", status="Phase 1/3: Recherche")
    # Status is immediate — even before the liveness threshold.
    n = mth.deliver_due(state, outbox, first_after_s=90, now=time.time())
    assert n == 1
    assert "🔧 X: Phase 1/3: Recherche" in _texts(outbox)[0]
    # Same status again → NOT re-sent.
    assert mth.deliver_due(state, outbox, first_after_s=90, now=time.time()) == 0
    # New status → a fresh status line.
    mth.update_status(state, "sess", label="X", status="Phase 2/3: Bauen")
    assert mth.deliver_due(state, outbox, first_after_s=90, now=time.time()) == 1
    assert any("Phase 2/3: Bauen" in t for t in _texts(outbox))


def test_step_before_start_creates_marker(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    # An update arriving before mark_active must not be silently lost.
    mth.update_status(state, "sess", channel="discord", chat_id="c",
                      label="Y", status="Iteration 1/5")
    assert mth.active_count(state) == 1


# ── liveness line: slow cadence, separate from status ───────────────────────

def test_liveness_line_after_threshold_and_interval(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    t0 = time.time()
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="Z")
    # Too soon → nothing.
    assert mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 30) == 0
    # Past threshold → one liveness line.
    assert mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 100) == 1
    assert "läuft seit" in _texts(outbox)[-1]
    # Within interval → no second.
    assert mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 200) == 0
    # After interval → due again.
    assert mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 300) == 1


def test_status_and_liveness_are_two_separate_messages(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    t0 = time.time()
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="Q")
    mth.update_status(state, "sess", label="Q", status="committe…")
    # One tick past the liveness threshold with a fresh status → BOTH messages.
    n = mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 100)
    assert n == 2
    texts = _texts(outbox)
    assert any("🔧" in t and "committe" in t for t in texts)
    assert any("⏱️" in t and "läuft seit" in t for t in texts)


# ── bounds / stop ───────────────────────────────────────────────────────────

def test_bgdone_and_clear_stop(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="D")
    assert mth.clear_task(state, "sess", "D") is True
    assert mth.active_count(state) == 0
    assert mth.deliver_due(state, outbox, first_after_s=0, now=time.time() + 999) == 0


def test_max_age_expires_and_removes(tmp_path):
    state, outbox = tmp_path / "s", tmp_path / "o"
    t0 = time.time()
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="E")
    n = mth.deliver_due(state, outbox, first_after_s=0, max_age_s=3600, now=t0 + 4000)
    assert n == 1
    assert "läuft weiter im Hintergrund" in _texts(outbox)[-1]
    assert mth.active_count(state) == 0


def test_liveness_is_sticky_progress_status_is_new_message(tmp_path):
    """Liveness must be an in-place sticky (_progress → daemon edits ONE message,
    no time flood); a status change must be a distinct new message (_task_progress)."""
    state, outbox = tmp_path / "s", tmp_path / "o"
    t0 = time.time()
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="F")
    mth.update_status(state, "sess", label="F", status="Phase 1")
    mth.deliver_due(state, outbox, first_after_s=90, interval_s=180, now=t0 + 100)
    envs = _envs(outbox)
    live = [e for e in envs if "läuft seit" in e["text"]]
    status = [e for e in envs if "🔧" in e["text"]]
    assert live and live[0].get("_progress") is True and "_task_progress" not in live[0]
    assert status and status[0].get("_task_progress") is True and "_progress" not in status[0]


# ── live-scan of the streaming token buffer ─────────────────────────────────

def test_scan_new_steps_surfaces_only_new_complete_markers():
    buf = "arbeite ⟦bgstep:T|Phase 1/3⟧ weiter"
    steps, idx = mth.scan_new_steps(buf, 0)
    assert steps == [("T", "Phase 1/3")]
    # No new complete marker past idx → nothing more.
    assert mth.scan_new_steps(buf, idx) == ([], idx)


def test_scan_new_steps_handles_marker_split_across_chunks():
    # A marker arriving in pieces must not fire until it is complete.
    a = "text ⟦bgstep:T|Pha"
    steps, idx = mth.scan_new_steps(a, 0)
    assert steps == [] and idx == 0          # partial → not yet
    b = a + "se 2/3⟧ more"
    steps, idx = mth.scan_new_steps(b, idx)
    assert steps == [("T", "Phase 2/3")]     # completed → fires once


# ── E2E wiring reachability: real adapter surfaces call the hooks ────────────

def test_adapter_wires_heartbeat_on_live_paths():
    src = (Path(__file__).resolve().parent / "adapter.py").read_text()
    assert '_bg_flag("bridge_mid_turn_task_notify")' in src, "reply hook must gate on the flag"
    assert ".parse_markers(" in src and ".parse_steps(" in src and ".parse_done(" in src, (
        "reply hook must parse start/step/done markers")
    assert ".mark_active(" in src and ".update_status(" in src, (
        "reply hook must register tasks and update their status")
    assert ".strip_markers(" in src, "reply hook must strip the markers"
    assert ".deliver_due(ROOT, OUTBOX)" in src, "main loop must deliver due heartbeats"


def test_streaming_path_live_scans_bgstep():
    """The synchronous streaming path must live-scan the token stream for steps
    and fold the current step into the alive heartbeat."""
    src = (Path(__file__).resolve().parent / "adapter.py").read_text()
    assert ".scan_new_steps(" in src, "streaming path must live-scan the token buffer"
    assert "_current_bgstep" in src, "alive heartbeat must fold in the current step"
