"""Drift-E2E — memory content injection in the CEL brief (ADR-0396/0399).

The most likely context-drift cause: an air-gapped / tool-less turn answered
wrong because the brief carried only memory TITLES, not the answerable FACT in
the memory body. ``render_brief_to_text(..., include_content=True)`` is the fix;
``include_content=False`` (default, ship-dark) keeps the title-only framing.

This test proves the difference is REAL and hard: the same brief, rendered with
content on, must contain the body fact and drop the title-only framing; rendered
with content off, must contain the title and NOT the body fact.

Real boundary: the brief's memory match points at a REAL ``.md`` file on disk,
and ``_memory_body`` reads + frontmatter-strips that file from disk. The disk
read is the boundary being exercised — nothing is monkeypatched. A separate
structural check proves the two live spawn surfaces (console ``chat_runtime`` and
the bridge ``adapter``) actually gate this render call by the
``cel_brief_includes_content`` feature flag, so the parameter is reachable, not
dead.

Run: python3 -m pytest operator/context_engineering/tests/test_drift_memory_injection_e2e.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "operator"))

from context_engineering.pipeline import render_brief_to_text  # noqa: E402
from context_engineering.rich_task_brief import (  # noqa: E402
    MemoryMatch, MemoryContext, RichTaskBrief,
)

# A fact that lives ONLY in the memory body, never in the title — so its
# presence in the rendered brief unambiguously proves the body was injected.
_BODY_FACT = "the canary rollout percentage for phase-3 is seventeen"
_TITLE = "Phase-3 rollout note"


def _write_memory_file(tmp_path: Path) -> Path:
    """A realistic memory .md: HTML-comment provenance banner + YAML frontmatter
    + body. _memory_body must strip the first two and surface the body fact."""
    p = tmp_path / "phase3-rollout.md"
    p.write_text(
        "<!-- provenance: auto-captured 2026-08-30; do not edit banner -->\n"
        "---\n"
        "title: Phase-3 rollout note\n"
        "tags: [canary, rollout]\n"
        "---\n"
        f"{_BODY_FACT}. Operators must not widen it without a measurement week.\n",
        encoding="utf-8",
    )
    return p


def _brief_for(source_file: Path) -> RichTaskBrief:
    match = MemoryMatch(
        filename=source_file.name,
        title=_TITLE,
        relevance_score=0.9,
        source_file=str(source_file),
        timestamp=datetime.now(),
        content_preview="canary rollout",  # only ~frontmatter, never the fact
    )
    return RichTaskBrief(
        raw_input="what is the phase-3 canary percentage?",
        enriched_task=object(),
        memory_context=MemoryContext(matches=[match], confidence=0.9),
        timestamp=datetime.now(),
    )


def test_include_content_true_injects_body_fact(tmp_path):
    """With content ON, the body fact is in the brief and the framing is the
    authoritative one — an air-gapped turn can answer from CEL alone."""
    brief = _brief_for(_write_memory_file(tmp_path))
    text = render_brief_to_text(brief, include_content=True)
    assert _BODY_FACT in text, "body fact must be injected when include_content=True"
    assert "authoritative" in text.lower(), (
        "content mode must use the assertive framing, not 'Relevant past memory'")


def test_include_content_false_is_title_only(tmp_path):
    """With content OFF (default, ship-dark), the brief carries the TITLE and
    NOT the body fact — the pre-feature path, byte-compatible."""
    brief = _brief_for(_write_memory_file(tmp_path))
    text = render_brief_to_text(brief, include_content=False)
    assert _TITLE in text, "title must appear in title-only mode"
    assert _BODY_FACT not in text, (
        "body fact must NOT leak into the brief when include_content=False")
    assert "Relevant past memory" in text, "title-only mode keeps the original framing"


def test_default_is_title_only(tmp_path):
    """The default (no kwarg) equals include_content=False — ship-dark."""
    brief = _brief_for(_write_memory_file(tmp_path))
    assert render_brief_to_text(brief) == render_brief_to_text(
        brief, include_content=False)


def test_the_two_render_modes_actually_differ(tmp_path):
    """Hard contrast: the two renderings of the SAME brief are not equal, and
    the difference is exactly the body fact. If they ever converge, the drift
    (title-only served where content was intended, or vice versa) is back."""
    brief = _brief_for(_write_memory_file(tmp_path))
    on = render_brief_to_text(brief, include_content=True)
    off = render_brief_to_text(brief, include_content=False)
    assert on != off
    assert _BODY_FACT in on and _BODY_FACT not in off


def test_missing_source_file_degrades_to_title(tmp_path):
    """Content injection is best-effort: a match whose source_file is gone must
    fall back to the title, never raise mid-turn."""
    brief = _brief_for(tmp_path / "does-not-exist.md")
    text = render_brief_to_text(brief, include_content=True)
    assert _TITLE in text
    assert _BODY_FACT not in text


def test_live_surfaces_gate_render_by_the_flag():
    """Reachability: the include_content parameter is not dead code — both live
    spawn surfaces resolve the cel_brief_includes_content flag and pass it into
    render_brief_to_text. This is the wiring that turns the tested behaviour into
    a live effect (e2e-wiring-proof Phase 1)."""
    chat = (_REPO / "core" / "console" / "corvin_console" / "chat_runtime.py").read_text(
        encoding="utf-8")
    assert 'is_enabled("cel_brief_includes_content"' in chat
    assert "include_content=" in chat, "chat_runtime must pass the flag into the render"

    adapter = (_REPO / "operator" / "bridges" / "shared" / "adapter.py").read_text(
        encoding="utf-8")
    assert 'is_enabled("cel_brief_includes_content"' in adapter
    assert "include_content=" in adapter, "adapter must pass the flag into the render"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
