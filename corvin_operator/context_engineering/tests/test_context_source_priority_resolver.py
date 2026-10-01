"""Context Source Priority Resolver — ADR-2098 E2E proof.

The symptom ADR-2098 exists to fix: the CEL memory stage runs fresh on every
turn's raw task text, so a memory fragment matching the current turn's words
gets re-injected and rendered as an "authoritative" new fact every time — even
when THIS session already surfaced it a few turns ago. Two mechanical fixes,
both CEL-reachable without reading the live dialogue transcript:

  1. Intra-turn duplicate collapse: two memory matches on the same topic are
     collapsed to the most-recently-modified one before rendering.
  2. Session-scoped repeat tagging: a topic already surfaced earlier THIS
     session is tagged (never dropped — ADR-2098 §2 fail-closed-toward-
     visibility) and reframed on render instead of repeating the same
     authoritative framing.

Tests here drive the REAL production entry point
(``pipeline.build_brief`` → ``render_brief_to_text``) — the same path the
bridge adapter and console chat_runtime call — not the stage class directly,
per the e2e-wiring-proof discipline (CLAUDE.md): a test that bypasses the
transport boundary is a unit test wearing an E2E label.

Run: .venv/bin/python -m pytest operator/context_engineering/tests/test_context_source_priority_resolver.py -v
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "corvin_operator", _REPO / "corvin_operator" / "forge", _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from context_engineering import pipeline as _pipeline  # noqa: E402
from context_engineering.pipeline import build_brief, render_brief_to_text  # noqa: E402
from context_engineering.rich_task_brief import (  # noqa: E402
    MemoryMatch, MemoryContext, RichTaskBrief,
)
from context_engineering import context_priority as _cp  # noqa: E402
import context_engineering.stages.memory as _memstage  # noqa: E402
import context_engineering.stages as _stages  # noqa: E402


def _match(filename: str, title: str, score: float, ts: datetime) -> MemoryMatch:
    return MemoryMatch(filename=filename, title=title, relevance_score=score,
                       source_file=f"/nonexistent/{filename}", timestamp=ts)


# Two matches on the SAME topic ("Phase 0 Kickoff"), different mtimes — the
# dedup acceptance fixture. The newer one is the designated survivor.
_PHASE0_OLD = _match("phase0_old.md", "Phase 0 Kickoff", 0.8, datetime(2026, 1, 1))
_PHASE0_NEW = _match("phase0_new.md", "Phase 0 Kickoff", 0.75, datetime(2026, 6, 1))
# An unrelated topic, present in every turn, to prove it is NEVER tagged/merged.
_UNRELATED = _match("unrelated.md", "Unrelated Fact", 0.9, datetime(2026, 3, 1))


def _crafted_brief(matches: list) -> RichTaskBrief:
    return RichTaskBrief(
        raw_input="mach phase 0", enriched_task=object(),
        memory_context=MemoryContext(matches=list(matches), confidence=0.85),
        timestamp=datetime.now(),
    )


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """Sandbox CORVIN_HOME (canonical test-override knob) and stub memory
    retrieval to a controlled match set — mirrors test_load_bearing_anchor.py's
    ``isolated`` fixture exactly, so the resolver is proven through the SAME
    real path that test already established."""
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

    class _FakeML:
        def __init__(self, *a, **k):
            pass

        def enrich_task(self, _task_obj):
            return _crafted_brief([_PHASE0_OLD, _PHASE0_NEW, _UNRELATED])

    monkeypatch.setattr(_memstage, "MemoryLookup", _FakeML)
    return tmp_path


class _Sess:
    sid = "sess-cspr-test"


class _OtherSess:
    sid = "sess-cspr-other"


# ── Reachability (e2e-wiring-proof Phase 1) ─────────────────────────────────

def test_stage_registered_and_wired_in_both_pipelines():
    """The stage self-registers at package import AND is listed in both
    DEFAULT_PIPELINE and ACTIVE_PIPELINE — registered-but-not-wired is exactly
    the failure class CLAUDE.md names repeatedly (a Skill/stage that boots but
    nothing ever runs it)."""
    from context_engineering.stages.config import DEFAULT_PIPELINE, ACTIVE_PIPELINE
    assert "context_priority" in _stages.known_ids()
    assert "context_priority" in DEFAULT_PIPELINE
    assert "context_priority" in [e["stage"] for e in ACTIVE_PIPELINE]
    # Must run AFTER memory (it operates on memory's output).
    idx = DEFAULT_PIPELINE.index
    assert idx("memory") < idx("context_priority")


# ── Intra-turn duplicate collapse ───────────────────────────────────────────

def test_e2e_duplicate_same_topic_collapsed_to_newest(isolated):
    """Drive the REAL build_brief path: two same-topic matches collapse to the
    single most-recently-modified one; the unrelated match is untouched."""
    brief, _trace = build_brief("mach phase 0", "_default", _Sess(), False)
    filenames = [m.filename for m in brief.memory_context.matches]
    assert filenames.count("phase0_old.md") == 0, "older duplicate must be dropped"
    assert "phase0_new.md" in filenames, "newer duplicate must survive"
    assert "unrelated.md" in filenames, "a non-duplicate match must be untouched"
    assert len(filenames) == 2, "exactly one match per topic after collapse"


def test_e2e_duplicate_merge_is_audited(isolated, tmp_path):
    """The collapse is hash-chained (ADR-2098 audit_events), not a silent drop."""
    build_brief("mach phase 0", "_default", _Sess(), False)
    chain = tmp_path / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert chain.is_file(), "the tenant audit chain must exist after a merge"
    text = chain.read_text(encoding="utf-8")
    assert "context.duplicate_memory_merged" in text
    assert '"kept_filename": "phase0_new.md"' in text
    assert '"topic_key": "phase 0 kickoff"' in text


# ── Session-scoped repeat tagging (never silent suppression) ──────────────

def test_e2e_first_turn_is_not_tagged_as_repeat(isolated):
    """First occurrence in a session: no repeat tag, plain rendering."""
    brief, _t = build_brief("mach phase 0", "_default", _Sess(), False)
    assert "phase 0 kickoff" not in (getattr(brief, "memory_repeat_topics", None) or set())
    text = render_brief_to_text(brief)
    assert "bereits in dieser Session erwähnt" not in text


def test_e2e_second_turn_same_session_is_tagged_not_dropped(isolated):
    """Second turn, SAME session, same topic: tagged AND still present in the
    rendered text (ADR-2098 §2 — fail-closed toward visibility, never silent
    substitution). This is the exact symptom fix: the model sees the fact was
    already noted, instead of the same authoritative-new-fact framing again."""
    build_brief("mach phase 0", "_default", _Sess(), False)
    brief2, _t = build_brief("mach weiter mit phase 0", "_default", _Sess(), False)
    assert "phase 0 kickoff" in (getattr(brief2, "memory_repeat_topics", None) or set())
    text2 = render_brief_to_text(brief2)
    assert "Phase 0 Kickoff" in text2, "the fact itself must still be visible, never dropped"
    assert "bereits in dieser Session erwähnt" in text2


def test_e2e_new_session_is_not_affected_session_independence(isolated):
    """ADR-2098 §4 — session independence: a DIFFERENT session on the same
    tenant must see the topic fresh, unaffected by another session's history."""
    build_brief("mach phase 0", "_default", _Sess(), False)
    build_brief("mach phase 0", "_default", _Sess(), False)  # 2 prior turns, same session
    brief_other, _t = build_brief("mach phase 0", "_default", _OtherSess(), False)
    assert "phase 0 kickoff" not in (getattr(brief_other, "memory_repeat_topics", None) or set())
    text_other = render_brief_to_text(brief_other)
    assert "bereits in dieser Session erwähnt" not in text_other


def test_e2e_tagging_is_topic_keyed_not_special_cased(isolated):
    """Tagging applies uniformly by topic key, not just to the "phase 0" match
    used elsewhere in this file: the unrelated match tags too once it repeats,
    proving the mechanism is generic rather than cherry-picked to one title."""
    build_brief("mach phase 0", "_default", _Sess(), False)  # turn 1: nothing tagged
    brief2, _t = build_brief("mach phase 0", "_default", _Sess(), False)  # turn 2: both repeat
    repeats = getattr(brief2, "memory_repeat_topics", None) or set()
    assert "phase 0 kickoff" in repeats
    assert "unrelated fact" in repeats


def test_session_independence_store_is_outside_global_memory(isolated, tmp_path):
    """The seen-topics store is per-(tenant, session) under the tenant home —
    never inside the global memory directory a NEW session's lookup reads."""
    build_brief("mach phase 0", "_default", _Sess(), False)
    store = tmp_path / "tenants" / "_default" / "cel_context_priority" / "sess-cspr-test.json"
    assert store.is_file()
    assert "phase 0 kickoff" in _cp.load_seen_topics("_default", "sess-cspr-test")


# ── Topic-key helper (unit-level, exact-match-only by design) ──────────────

def test_topic_key_exact_normalization_no_fuzzy_merge():
    """ADR-2098 deliberately uses an EXACT normalized key, not fuzzy similarity
    — ambiguous near-matches must stay visible (§2), not silently merged."""
    a = _match("a.md", "Phase 0: Kickoff!", 0.9, datetime(2026, 1, 1))
    b = _match("b.md", "phase 0 kickoff", 0.9, datetime(2026, 1, 2))
    c = _match("c.md", "Phase 1 Kickoff", 0.9, datetime(2026, 1, 3))
    assert _cp.topic_key(a) == _cp.topic_key(b)
    assert _cp.topic_key(a) != _cp.topic_key(c)


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
