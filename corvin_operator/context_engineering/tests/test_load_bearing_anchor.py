"""Session Load-Bearing-Fact Anchor — CEL truncation-safe re-injection (ADR-0407).

The within-session context-drift gap: ``render_brief_to_text`` caps memory
matches at ``[:5]`` and ``scan_blockers`` caps blockers at ``[:5]``, so a
load-bearing fact at rank 6+ falls out of the brief SILENTLY every turn. The
``cel_load_bearing_anchor`` flag (ship-dark, default OFF) persists the turn's
load-bearing facts per session and re-injects them uncapped at the TOP of the
brief.

Tests here measure the SUM ("is the fact present?"), never an internal trace:

  * ``test_red_acceptance_constraint_survives_truncation`` — the RED-first
    acceptance: a brief with 6 blocker-signal matches whose 6th (the designated
    constraint) falls out of every [:5] cut; with the flag ON its text must be
    PRESENT in the rendered brief. (Written first, run RED against a neutered
    implementation, then GREEN — see the task report.)
  * ``test_flag_off_is_byte_identical_shipdark`` — flag OFF ⇒ no anchor header,
    the constraint absent, and the store + Move-2 counter are untouched (0).
  * ``test_e2e_build_brief_autopopulates_and_injects`` — drives the REAL
    ``build_brief → render_brief_to_text`` path (not ``render`` with a hand-built
    ``anchor_facts``) and proves auto-populate + injection + the on-disk store.
  * ``test_live_surfaces_carry_the_anchor_path`` — reachability: both live spawn
    surfaces call ``build_brief`` then ``render_brief_to_text``, and ``build_brief``
    itself calls the anchor auto-populate — so the feature rides the live path.
  * ``test_move2_signal_fires`` / ``test_move2_mutation_is_caught`` — the
    watchdog-readable Move-2 injection counter goes positive on injection, and
    muting it (the mutation) is caught.

Run: .venv/bin/python -m pytest operator/context_engineering/tests/test_load_bearing_anchor.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "operator", _REPO / "operator" / "forge", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from context_engineering import anchor as _anchor  # noqa: E402
from context_engineering import pipeline as _pipeline  # noqa: E402
from context_engineering.pipeline import build_brief, render_brief_to_text  # noqa: E402
from context_engineering.rich_task_brief import (  # noqa: E402
    MemoryMatch, MemoryContext, RichTaskBrief,
)
import context_engineering.stages.memory as _memstage  # noqa: E402
import corvin_core.feature_flags as _ff  # noqa: E402

# A token that lives ONLY in the 6th match's title, so its presence in the
# rendered brief unambiguously proves the rank-6 fact survived truncation.
_CANARY = "CANARY_SEVENTEEN"

# Six matches, ALL carrying a blocker signal in the title; #6 (the designated
# constraint) is last + lowest relevance, so it falls out of BOTH the memory
# [:5] section AND the blockers [:5] section.
_MATCH_TITLES = [
    "constraint: audit chain must not break",
    "blocker: never delete audit.jsonl",
    "do not force-push main branch",
    "deprecated legacy env-var fallback",
    "locked bot-disclosure card contract",
    f"irreversible fail-closed path gate {_CANARY}",  # rank 6 — the designated one
]


def _crafted_brief() -> RichTaskBrief:
    matches = [
        MemoryMatch(
            filename=f"m{i}.md",
            title=title,
            relevance_score=round(0.9 - i * 0.1, 3),  # descending; #6 lowest
            source_file=f"/nonexistent/m{i}.md",
            timestamp=datetime.now(),
            content_preview="",
        )
        for i, title in enumerate(_MATCH_TITLES)
    ]
    return RichTaskBrief(
        raw_input="keep working on the compliance audit chain",
        enriched_task=object(),
        memory_context=MemoryContext(matches=matches, confidence=0.9),
        timestamp=datetime.now(),
    )


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Isolate the anchor store under a tmp CORVIN_HOME and stub memory retrieval
    to the crafted 6-match brief. CORVIN_HOME is the canonical test override knob
    (NOT the forbidden CORVIN_TENANT_ID tenant/session fallback)."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    class _FakeML:
        def __init__(self, *a, **k):
            pass

        def enrich_task(self, _task_obj):
            return _crafted_brief()

    monkeypatch.setattr(_memstage, "MemoryLookup", _FakeML)
    return tmp_path


def _set_flag(monkeypatch, value: bool) -> None:
    monkeypatch.setattr(_ff, "is_enabled", lambda flag_id, tenant="_default": value)


class _Sess:
    sid = "sess-anchor-test"


# ── RED-first acceptance ────────────────────────────────────────────────────

def test_red_acceptance_constraint_survives_truncation(isolated, monkeypatch):
    """RED-first: with the anchor flag ON, the rank-6 constraint text (which every
    [:5] cut drops) must be PRESENT in the rendered brief. Boolean 'fact present?',
    no internal trace."""
    _set_flag(monkeypatch, True)
    brief, _trace = build_brief("audit chain task", "_default", _Sess(), False)
    text = render_brief_to_text(brief)
    assert _CANARY in text, (
        "rank-6 load-bearing constraint must survive the [:5] truncation when the "
        "anchor flag is ON")
    assert "Load-bearing facts (persist across this whole session" in text, (
        "the anchor must render its assertive, protected-slot header at the top")
    # It renders at the TOP — before the memory section.
    assert text.index(_CANARY) < text.index("Relevant past memory"), (
        "anchor slot must be rendered ABOVE the (truncated) memory section")


# ── Both-state ship-dark ────────────────────────────────────────────────────

def test_flag_off_is_byte_identical_shipdark(isolated, monkeypatch):
    """Flag OFF (default) ⇒ no anchor header, the rank-6 constraint absent, and
    NOTHING reached: the store has no facts and the Move-2 counter did not move."""
    _set_flag(monkeypatch, False)
    before = _anchor.injected_total()
    brief, trace = build_brief("audit chain task", "_default", _Sess(), False)
    text = render_brief_to_text(brief)

    assert "Load-bearing facts (persist across" not in text
    assert _CANARY not in text, "rank-6 fact must stay dropped when the flag is off"
    assert (getattr(brief, "anchor_facts", None) or []) == [], "no facts attached"
    assert trace.get("anchor_facts", 0) == 0
    # Nothing reached the store, and the counter is flat.
    assert _anchor.load_facts("_default", _Sess.sid) == []
    assert _anchor.injected_total() == before

    # Byte-identical: the empty anchor field contributes nothing to the render.
    brief.anchor_facts = []
    assert render_brief_to_text(brief) == text


# ── E2E wiring proof (real transport) ───────────────────────────────────────

def test_e2e_build_brief_autopopulates_and_injects(isolated, monkeypatch):
    """Drive the REAL build_brief → render path. Proves: (1) build_brief
    auto-populates the on-disk anchor store, (2) the persisted facts are attached
    to brief.anchor_facts, (3) the render injects them. No hand-built anchor_facts."""
    _set_flag(monkeypatch, True)
    brief, trace = build_brief("audit chain task", "_default", _Sess(), False)

    # (1) auto-populate persisted to the tenant/session-scoped store on disk.
    persisted = _anchor.load_facts("_default", _Sess.sid)
    assert any(_CANARY in f["text"] for f in persisted), (
        "build_brief must persist the rank-6 constraint to the anchor store")
    assert any(f["kind"] == "goal" for f in persisted), "the session goal is anchored"
    # (2) attached to the brief.
    assert brief.anchor_facts and len(brief.anchor_facts) == len(persisted)
    assert trace.get("anchor_facts", 0) >= 6
    # (3) injected into the rendered brief.
    text = render_brief_to_text(brief)
    assert _CANARY in text


def test_goal_is_stable_across_turns(isolated, monkeypatch):
    """The ORIGINAL session goal is anchored ONCE and persists — a fresh per-turn
    task does not evict the real constraints by re-adding a new goal each turn."""
    _set_flag(monkeypatch, True)
    build_brief("first task", "_default", _Sess(), False)
    build_brief("a completely different second task", "_default", _Sess(), False)
    goals = [f for f in _anchor.load_facts("_default", _Sess.sid) if f["kind"] == "goal"]
    assert len(goals) == 1, "only the original goal is kept, not one per turn"


def test_live_surfaces_carry_the_anchor_path():
    """Reachability (e2e-wiring-proof Phase 1): the anchor rides the SAME live path
    the CEL brief already uses. Both live spawn surfaces call build_brief then
    render_brief_to_text, and build_brief itself invokes the anchor auto-populate —
    so the feature is reachable without a second wiring."""
    adapter = (_REPO / "operator" / "bridges" / "shared" / "adapter.py").read_text(
        encoding="utf-8")
    assert "_cel_build_brief(" in adapter and "_cel_render(" in adapter, (
        "the bridge adapter drives build_brief → render (carries anchor_facts)")

    chat = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text(
        encoding="utf-8")
    assert "_cel_build_brief(" in chat and "_cel_render(" in chat, (
        "console chat_runtime drives build_brief → render (carries anchor_facts)")

    pipe = (_REPO / "operator" / "context_engineering" / "pipeline.py").read_text(
        encoding="utf-8")
    assert "_maybe_apply_anchor(" in pipe, "build_brief must call the anchor auto-populate"


# ── Move-2: watchdog-readable injection signal + mutation guard ─────────────

def test_move2_signal_fires(isolated, monkeypatch):
    """The Move-2 counter (a watchdog-readable module-level signal, NOT a return
    value) goes positive by exactly the number of facts injected."""
    _set_flag(monkeypatch, True)
    brief, _t = build_brief("audit chain task", "_default", _Sess(), False)
    n = len(brief.anchor_facts)
    assert n >= 6
    before = _anchor.injected_total()
    render_brief_to_text(brief)
    assert _anchor.injected_total() == before + n, (
        "record_injection must bump the watchdog counter by the injected count")


def test_move2_mutation_is_caught(isolated, monkeypatch):
    """Mutation: silence the Move-2 signal (make record_injection a no-op). The
    facts still reach the rendered text, but the counter stays FLAT — which is
    exactly what test_move2_signal_fires asserts against, so the mutation is
    caught (that test would go RED)."""
    _set_flag(monkeypatch, True)
    brief, _t = build_brief("audit chain task", "_default", _Sess(), False)
    # Mute the signal at its module attribute (render calls anchor.record_injection).
    monkeypatch.setattr(_anchor, "record_injection", lambda *a, **k: None)
    before = _anchor.injected_total()
    text = render_brief_to_text(brief)
    assert _CANARY in text, "text injection still happens under the mutation"
    assert _anchor.injected_total() == before, (
        "with the signal muted the counter does NOT move — proving the counter is "
        "a real, load-bearing signal the fires-test guards")


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
