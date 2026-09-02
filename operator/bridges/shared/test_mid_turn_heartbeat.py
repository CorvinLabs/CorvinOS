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


# ── review fixes #1-#6 (regressions) ────────────────────────────────────────

def test_strip_markers_removes_partial_empty_and_oversized(tmp_path=None):
    """#5 — strip must remove partial (unterminated), empty-label and oversized
    markers, not only well-formed ones, so nothing ever leaks to the channel."""
    # Partial: split across chunks, no closing ⟧ yet.
    assert "⟦bg" not in mth.strip_markers("Arbeite ⟦bgstep:T|Pha")
    # Empty label.
    assert "⟦bg" not in mth.strip_markers("davor ⟦bgtask:⟧ danach")
    # Oversized label (well past the 80/120 parse caps).
    big = "⟦bgtask:" + ("x" * 300) + "⟧ ende"
    out = mth.strip_markers(big)
    assert "⟦bg" not in out and "x" not in out
    # A normal marker still strips and surrounding text survives.
    assert mth.strip_markers("Hi ⟦bgdone:T⟧ da").strip() == "Hi  da".strip()


def test_parse_and_strip_never_raise_on_non_str():
    """#6 — type guards: non-str input must not raise."""
    assert mth.parse_markers(None) == []
    assert mth.parse_steps(123) == []
    assert mth.parse_done({"x": 1}) == []
    assert mth.strip_markers(None) is None
    assert mth.scan_new_steps(None, 0) == ([], 0)
    assert mth.scan_new_steps("x", -5) == ([], 0)  # bad from_index normalised


def test_scan_new_steps_advances_index_past_markerless_text():
    """#4 — the scan cursor must advance over long markerless text so repeated
    calls are not O(n) over the whole growing buffer each time."""
    buf = "⟦bgstep:T|P1⟧" + ("filler " * 500)  # >> _MAX_MARKER_LEN of trailing text
    steps, idx = mth.scan_new_steps(buf, 0)
    assert steps == [("T", "P1")]
    # Cursor advanced to within one max-marker-length of the end.
    assert idx >= len(buf) - mth._MAX_MARKER_LEN
    # But never past the buffer, and a still-forming tail marker is preserved.
    assert idx <= len(buf)


def test_envelope_msg_ids_are_unique():
    """#2 — msg_id must be collision-free even for the same (kind, seq)."""
    rec = {"session_key": "s", "label": "L", "channel": "discord", "chat_id": "c"}
    a = mth._envelope(rec, "t", "live", 0)
    b = mth._envelope(rec, "t", "live", 0)
    assert a["msg_id"] != b["msg_id"]
    assert a["msg_id"].startswith("mth_")


def test_deliver_due_gc_corrupt_record_and_isolates(tmp_path):
    """#3 — a corrupt marker file is unlinked (not re-scanned forever) and does
    not abort the tick for a healthy record sorted after it."""
    state, outbox = tmp_path / "s", tmp_path / "o"
    # Create a valid record first (establishes the dir).
    mth.update_status(state, "sess", channel="discord", chat_id="c",
                      label="good", status="Phase 1")
    d = state / "mid_turn_heartbeats"
    # A corrupt file that sorts BEFORE the good one (a_ prefix).
    bad = d / "a_corrupt.json"
    bad.write_text("{not valid json", encoding="utf-8")
    n = mth.deliver_due(state, outbox, first_after_s=90, now=time.time())
    # The good record still emitted its status line …
    assert n == 1
    assert any("Phase 1" in t for t in _texts(outbox))
    # … and the corrupt file was garbage-collected.
    assert not bad.exists()


def test_deliver_due_bad_started_at_does_not_ping_forever(tmp_path):
    """#3 — a record with a garbage started_at must not read as expired and ping
    on every tick; it is treated as age 0 (fresh)."""
    state, outbox = tmp_path / "s", tmp_path / "o"
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="B")
    d = state / "mid_turn_heartbeats"
    p = next(d.glob("*.json"))
    rec = mth._load(p)
    rec["started_at"] = "not-a-number"
    mth._atomic_write(p, rec)
    # Far in the "future" — a numeric started_at would be way past max_age.
    n = mth.deliver_due(state, outbox, first_after_s=90, interval_s=180,
                        now=time.time() + 99999)
    assert n == 0  # treated as age 0 → nothing due, no crash


def test_module_has_reentrant_lock():
    """#1 — the read-modify-write sections are guarded by a module-level RLock
    (reentrant: update_status re-enters it via mark_active)."""
    assert hasattr(mth._lock, "acquire") and hasattr(mth._lock, "release")
    # Reentrancy: a plain Lock would deadlock on the second acquire.
    assert mth._lock.acquire(blocking=False)
    try:
        assert mth._lock.acquire(blocking=False)  # re-enter, same thread
        mth._lock.release()
    finally:
        mth._lock.release()


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


def test_emission_side_wired_into_system_prompt():
    """#11 — the EMISSION side: system_prompt_for adds a marker-protocol block
    ONLY when the flag is on (ship-dark). Reachability assertion — a true
    transport E2E needs a live bridge + model, so this is a source-grep
    (infeasibility exception), same as the other adapter-wiring checks above.
    """
    src = (Path(__file__).resolve().parent / "adapter.py").read_text()
    assert "_bg_marker_block" in src, "system prompt must build a marker-protocol block"
    # It teaches the exact markers the reply hook parses/strips.
    assert "⟦bgtask:" in src and "⟦bgstep:" in src and "⟦bgdone:" in src, (
        "the emission block must document the bgtask/bgstep/bgdone protocol")
    # And it is gated on the same ship-dark flag (block stays empty when off).
    assert 'if _bg_flag("bridge_mid_turn_task_notify"):' in src


# ── review Round-2 LOW fixes ────────────────────────────────────────────────

def test_sweep_removes_expired_and_corrupt_but_not_fresh(tmp_path):
    """Round-2 #1 — flag-off cleanup: sweep GCs expired + corrupt markers but
    keeps a fresh one, and emits nothing (no outbox writes)."""
    state, outbox = tmp_path / "s", tmp_path / "o"
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="fresh")
    mth.mark_active(state, "sess", channel="discord", chat_id="c", sender=None, label="old")
    # age the "old" one past MAX_AGE
    import json as _j
    p_old = mth._marker_path(state, "sess", "old")
    d = _j.loads(p_old.read_text()); d["started_at"] = time.time() - 5000; p_old.write_text(_j.dumps(d))
    # a corrupt file
    corrupt = mth._dir(state) / "sess__deadbeef00.json"; corrupt.write_text("{not json")
    removed = mth.sweep(state)
    assert removed == 2                      # old + corrupt
    assert mth.active_count(state) == 1      # fresh survives
    assert not outbox.exists() or list(outbox.glob("*.json")) == []  # emitted nothing


def test_strip_does_not_eat_foreign_bracket_after_partial():
    """Round-2 #2 — a partial ⟦bgstep: must not swallow an independent ⟦…⟧ that
    follows it on the same line."""
    out = mth.strip_markers("text ⟦bgstep:foo and then ⟦other⟧ tail")
    assert "⟦other⟧" in out          # foreign structure preserved
    assert "⟦bgstep" not in out      # the partial marker is stripped


def test_strip_mirrors_parser_no_wandering_edge():
    """Round-3 — strip mirrors the parser: a VALID marker whose status contains a
    literal ⟦ is stripped WHOLE (no leak), while a PARTIAL opener still preserves
    an independent ⟦…⟧ that follows it. Both edges hold at once."""
    # Valid bgstep, status contains a literal ⟦…⟧ → stripped entirely.
    assert mth.strip_markers("pre ⟦bgstep:build|weird ⟦text⟧ post").strip() == "pre  post".strip()
    # parse_steps agrees it is a valid marker (status up to first ⟧).
    assert mth.parse_steps("⟦bgstep:build|weird ⟦text⟧") == [("build", "weird ⟦text")]
    # Partial opener (no |, no close) + independent foreign bracket → foreign kept.
    out = mth.strip_markers("text ⟦bgstep:foo and then ⟦other⟧ tail")
    assert "⟦other⟧" in out and "⟦bgstep" not in out
    # Normal complete markers still strip cleanly.
    assert "⟦bg" not in mth.strip_markers("a ⟦bgtask:T⟧ b ⟦bgdone:T⟧ c")


# ── LB-prov: heartbeat/status envelopes carry a provenance marking ──────────

def test_envelope_carries_provenance(tmp_path):
    """LB-prov / EU AI Act Art. 50 §4: a machine-generated heartbeat/status
    envelope carries a `provenance` marking, consistent with the progress /
    completion envelopes. Proven through the real deliver_due write path."""
    state, outbox = tmp_path / "s", tmp_path / "o"
    mth.update_status(state, "sess", channel="discord", chat_id="c",
                      label="X", status="Phase 1/3: Recherche")
    assert mth.deliver_due(state, outbox, first_after_s=90, now=time.time()) == 1
    envs = _envs(outbox)
    assert envs and "provenance" in envs[0], envs
