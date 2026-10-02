"""Side turns (/plugin-builder answers, /task) reach the session ledger only
through the L44 gate (ADR-2102, review R3).

A /plugin-builder answer is handed to the builder, never to the engine, so no
gate saw its text — and the ledger would then re-supply it into every later
system prompt. ``_ledger_record_side_turn`` now runs the house-rules gate and
records a denied text as refused (withheld from every re-supply).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adapter  # type: ignore  # noqa: E402
import session_ledger as sl  # type: ignore  # noqa: E402


def _ledger_of(chat):
    return sl.read_ledger(adapter._session_dir("telegram", chat))


def test_denied_side_turn_is_recorded_refused_and_withheld(monkeypatch):
    monkeypatch.setattr(adapter, "_check_house_rules_or_fail",
                        lambda **kw: "Request refused (house rules).")
    adapter._ledger_record_side_turn("telegram", "side-1", "/plugin-builder UNGATED-PAYLOAD-4242",
                                     "Plugin Builder is off")
    rec = _ledger_of("side-1")[-1]
    assert rec["refused"] == "house_rules" and rec["spawned"] is False
    block = sl.render_context(adapter._session_dir("telegram", "side-1"))
    assert "UNGATED-PAYLOAD-4242" not in block


def test_allowed_side_turn_is_recorded_plain(monkeypatch):
    monkeypatch.setattr(adapter, "_check_house_rules_or_fail", lambda **kw: None)
    adapter._ledger_record_side_turn("telegram", "side-2", "/plugin-builder a notes plugin",
                                     "Question 1: …")
    rec = _ledger_of("side-2")[-1]
    assert rec["refused"] == "" and rec["spawned"] is False


def test_gate_error_fails_closed(monkeypatch):
    def boom(**kw):
        raise RuntimeError("classifier down")
    monkeypatch.setattr(adapter, "_check_house_rules_or_fail", boom)
    adapter._ledger_record_side_turn("telegram", "side-3", "/plugin-builder X", "y")
    assert _ledger_of("side-3")[-1]["refused"] == "house_rules"
