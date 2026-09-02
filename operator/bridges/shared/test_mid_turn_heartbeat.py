"""Tests for mid-turn heartbeat (ADR-0551 C1 variant B).

Measures the SUM: does a heartbeat envelope actually land in the outbox for a
long-running task, and is it correctly bounded / cleared / silent when off.
Run: .venv/bin/python -m pytest operator/bridges/shared/test_mid_turn_heartbeat.py
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mid_turn_heartbeat as mth  # noqa: E402


def _outbox_envelopes(outbox: Path) -> list:
    return [json.loads(p.read_text()) for p in outbox.glob("*.json")]


def test_marker_parse_and_strip():
    reply = "Ich starte das im Hintergrund. ⟦bgtask:Anchor bauen⟧ Melde mich."
    assert mth.parse_markers(reply) == ["Anchor bauen"]
    stripped = mth.strip_markers(reply)
    assert "⟦bgtask" not in stripped and "Anchor bauen" not in stripped
    assert "Ich starte das im Hintergrund." in stripped


def test_heartbeat_lands_in_outbox_after_threshold(tmp_path):
    state, outbox = tmp_path / "state", tmp_path / "outbox"
    mth.mark_active(state, "discord:chan1", channel="discord",
                    chat_id="chan1", sender=None, label="langer Task")
    # Too soon → nothing.
    assert mth.deliver_due(state, outbox, first_after_s=60, now=time.time()) == 0
    assert _outbox_envelopes(outbox) == []
    # Past the threshold → one heartbeat envelope, correctly routed.
    n = mth.deliver_due(state, outbox, first_after_s=60, now=time.time() + 120)
    assert n == 1
    envs = _outbox_envelopes(outbox)
    assert len(envs) == 1
    e = envs[0]
    assert e["channel"] == "discord" and e["chat_id"] == "chan1"
    assert e.get("_task_progress") is True
    assert "langer Task" in e["text"]


def test_interval_gate_between_pings(tmp_path):
    state, outbox = tmp_path / "state", tmp_path / "outbox"
    t0 = time.time()
    mth.mark_active(state, "s", channel="discord", chat_id="c", sender=None, label="L")
    assert mth.deliver_due(state, outbox, first_after_s=60, interval_s=60, now=t0 + 61) == 1
    # 30s later → still within interval → no second ping.
    assert mth.deliver_due(state, outbox, first_after_s=60, interval_s=60, now=t0 + 91) == 0
    # 60s after the first ping → due again.
    assert mth.deliver_due(state, outbox, first_after_s=60, interval_s=60, now=t0 + 122) == 1


def test_clear_session_stops_heartbeat(tmp_path):
    state, outbox = tmp_path / "state", tmp_path / "outbox"
    mth.mark_active(state, "sX", channel="discord", chat_id="c", sender=None, label="L")
    assert mth.active_count(state) == 1
    assert mth.clear_session(state, "sX") == 1
    assert mth.active_count(state) == 0
    # Nothing to deliver after clear.
    assert mth.deliver_due(state, outbox, first_after_s=0, now=time.time() + 999) == 0


def test_bounded_final_note_and_removal(tmp_path):
    state, outbox = tmp_path / "state", tmp_path / "outbox"
    t0 = time.time()
    mth.mark_active(state, "s", channel="discord", chat_id="c", sender=None, label="L")
    # Past max_age → a final note is written AND the marker is removed (stops).
    n = mth.deliver_due(state, outbox, first_after_s=0, max_age_s=1800, now=t0 + 2000)
    assert n == 1
    assert mth.active_count(state) == 0
    assert "läuft weiter im Hintergrund" in _outbox_envelopes(outbox)[0]["text"]


def test_adapter_wires_heartbeat_on_live_paths():
    """E2E-wiring-proof (reachability): the live adapter must actually CALL the
    heartbeat on BOTH real surfaces — the outbound reply path (mark/clear/strip,
    flag-gated) and the main poll loop (deliver_due) — not merely import it."""
    src = (Path(__file__).resolve().parent / "adapter.py").read_text()
    assert '_bg_flag("bridge_mid_turn_task_notify")' in src, (
        "reply hook must gate on the ship-dark flag")
    assert ".mark_active(" in src and ".clear_session(" in src and ".strip_markers(" in src, (
        "reply hook must clear stale markers, register new ones, and strip the marker")
    assert ".deliver_due(ROOT, OUTBOX)" in src, (
        "main loop must deliver due heartbeats through the outbox")


def test_two_concurrent_tasks_per_session(tmp_path):
    state, outbox = tmp_path / "state", tmp_path / "outbox"
    mth.mark_active(state, "s", channel="discord", chat_id="c", sender=None, label="A")
    mth.mark_active(state, "s", channel="discord", chat_id="c", sender=None, label="B")
    assert mth.active_count(state) == 2
    assert mth.deliver_due(state, outbox, first_after_s=0, now=time.time() + 61) == 2
