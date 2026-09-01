"""Decision-point capture — CEL anchor extension (ADR-0407 amendment).

The "forgot Option 2" gap: options the assistant offers live in prior assistant
turns; when a long session is summarised, the verbatim options are dropped, so a
later "Option 2" no longer resolves. This extension captures a decision/options
block from the FINAL reply on the outbound path and persists it as a
``kind='decision'`` anchor fact under the same (tenant, session_key) the inbound
auto-populate uses, so the next turn's brief re-injects it uncapped.

Ship-dark behind the SAME ``cel_load_bearing_anchor`` flag (default OFF).

Tests measure the SUM ("is the option text present?"), never an internal trace.

Run: .venv/bin/python -m pytest \
     operator/context_engineering/tests/test_decision_point_anchor.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "operator", _REPO / "operator" / "forge", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from datetime import datetime  # noqa: E402

from context_engineering import anchor as _anchor  # noqa: E402
from context_engineering import pipeline as _pipeline  # noqa: E402
from context_engineering.pipeline import (  # noqa: E402
    build_brief, render_brief_to_text, maybe_capture_decision_point,
)
from context_engineering.rich_task_brief import (  # noqa: E402
    MemoryContext, RichTaskBrief,
)
import context_engineering.stages.memory as _memstage  # noqa: E402
import corvin_core.feature_flags as _ff  # noqa: E402

# A token that lives ONLY in Option 2, so its presence in the rendered brief
# unambiguously proves the offered option survived into the next turn.
_CANARY = "CANARY_OPTION_TWO_9F3"

_OPTIONS_REPLY = (
    "Zwei Wege, dann lege ich los:\n\n"
    "- **Option 1:** History-Rewrite-+-Force-Push-Bereinigung.\n"
    f"- **Option 2:** read-only Inventur starten ({_CANARY}).\n\n"
    "Welche willst du?"
)

_PLAIN_LIST_REPLY = (
    "Erledigt — ich habe folgendes getan:\n"
    "- Datei A geschrieben\n- Datei B getestet\n- Datei C committed"
)


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Isolate the anchor store under a tmp CORVIN_HOME and stub memory retrieval
    so build_brief does not depend on real on-disk memories."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    class _FakeML:
        def __init__(self, *a, **k):
            pass

        def enrich_task(self, _task_obj):
            # Minimal valid brief (empty memory) so build_brief yields a non-None
            # brief and _maybe_apply_anchor runs — the decision fact is what we assert.
            return RichTaskBrief(
                raw_input="okay Option 2",
                enriched_task=object(),
                memory_context=MemoryContext(matches=[], confidence=0.0),
                timestamp=datetime.now(),
            )

    monkeypatch.setattr(_memstage, "MemoryLookup", _FakeML)
    return tmp_path


def _set_flag(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(_ff, "is_enabled", lambda flag_id, tenant="_default": value)


class _Sess:
    sid = "sess-decision-test"


def _session_key() -> str:
    return _pipeline._session_key_of(_Sess(), "")


# ── RED-first acceptance: option survives into the next turn ─────────────────

def test_offered_option_survives_into_next_turn(isolated, monkeypatch):
    """RED-first: capture a reply offering options on the outbound path, then the
    NEXT turn's build_brief -> render must re-inject Option 2 verbatim. Boolean
    'option present?', no internal trace."""
    _set_flag(monkeypatch, True)
    # Turn N (outbound): the assistant offered options.
    captured = maybe_capture_decision_point(_OPTIONS_REPLY, "_default", _Sess())
    assert captured is not None, "an options block must be captured when flag ON"
    # Turn N+1 (inbound): the brief must re-inject it.
    brief, _trace = build_brief("okay Option 2", "_default", _Sess(), False)
    text = render_brief_to_text(brief)
    assert _CANARY in text, (
        "the previously-offered Option 2 must survive into the next turn's brief")
    assert "Open decision points you were offered" in text, (
        "decision facts must render under their own verbatim header")


def test_plain_list_is_not_captured(isolated, monkeypatch):
    """Conservative: an ordinary bullet list (no options/choice) must NOT be
    captured — no over-capture of every list."""
    _set_flag(monkeypatch, True)
    assert maybe_capture_decision_point(_PLAIN_LIST_REPLY, "_default", _Sess()) is None
    assert _anchor.load_facts("_default", _session_key()) == []


# ── Both-state ship-dark ────────────────────────────────────────────────────

def test_flag_off_no_capture_shipdark(isolated, monkeypatch):
    """Flag OFF (default) ⇒ the outbound hook is a no-op: returns None and writes
    nothing to the store (spy: load_facts stays empty)."""
    _set_flag(monkeypatch, False)
    assert maybe_capture_decision_point(_OPTIONS_REPLY, "_default", _Sess()) is None
    assert _anchor.load_facts("_default", _session_key()) == []


# ── Rolling window: keep last 3 decisions, never evict a constraint ──────────

def test_rolling_window_keeps_last_3_and_preserves_constraint(isolated, monkeypatch):
    _set_flag(monkeypatch, True)
    key = _session_key()
    # A load-bearing constraint that must NEVER be evicted by decision churn.
    _anchor.add_fact("_default", key, "constraint", "force-push is forbidden")
    for i in range(4):
        reply = (
            f"Menu {i}:\n- **Option 1:** do A{i}\n- **Option 2:** do B{i}\nWhich?"
        )
        maybe_capture_decision_point(reply, "_default", _Sess())
    facts = _anchor.load_facts("_default", key)
    decisions = [f for f in facts if f.get("kind") == "decision"]
    constraints = [f for f in facts if f.get("kind") == "constraint"]
    assert len(decisions) == 3, "only the newest 3 decision menus are kept"
    assert any("Menu 3" in f["text"] for f in decisions), "newest menu must be kept"
    assert not any("Menu 0" in f["text"] for f in decisions), "oldest menu evicted"
    assert len(constraints) == 1, "the constraint must NOT be evicted by decisions"


# ── E2E wiring reachability: real outbound surfaces call the hook ────────────

def test_live_surfaces_call_decision_capture():
    """Reachability: both live outbound surfaces actually CALL the capture hook
    (not merely import it) — proven against the real source, per e2e-wiring-proof."""
    adapter = (_REPO / "operator" / "bridges" / "shared" / "adapter.py").read_text()
    chat = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text()
    assert "_cel_maybe_capture_decision(" in adapter, (
        "the bridge adapter must call the decision-capture hook on the outbound path")
    assert "_cel_capture_decision(" in chat, (
        "chat_runtime must call the decision-capture hook on the outbound path")


# ── Move-2: injection signal fires for a decision fact ──────────────────────

def test_decision_injection_signal_fires(isolated, monkeypatch):
    """The watchdog-readable Move-2 counter goes positive when a captured decision
    fact is actually re-injected into a rendered brief."""
    _set_flag(monkeypatch, True)
    before = _anchor.injected_total()
    maybe_capture_decision_point(_OPTIONS_REPLY, "_default", _Sess())
    brief, _trace = build_brief("okay Option 2", "_default", _Sess(), False)
    render_brief_to_text(brief)
    assert _anchor.injected_total() > before, (
        "record_injection must fire when a decision fact reaches the brief")
