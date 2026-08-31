"""Adversarial review 2026-08-04, live report: "the voice summary in the
Console chat sometimes gets cut off / doesn't come out complete."

Root cause: ``_voice_tts_sync`` clamped the summarized text to
``_TTS_PROVIDER_CHAR_LIMIT`` with a bare ``text[:limit]`` slice — unlike its
own neighboring fallback branches (``system_generated`` and the
no-summary-at-all case), which both already cut at the last sentence
boundary. This mattered because ``summarize.py::adaptive_target()`` scales
the summarizer's target length to ~85% of the ORIGINAL answer's length with
NO hard cap ("completeness wins") — a long chat answer (roughly >4700 chars,
common for this assistant's own detailed replies) can legitimately produce
an LLM summary itself longer than the 4000-char provider limit, and the raw
slice then cut it mid-word.

These tests pin the fix: ``_cut_at_sentence_boundary`` is now used
uniformly across all three truncation sites in routes/voice.py, and never
lands mid-word when a sentence boundary exists within the cut window.

Run: python3 -m pytest core/console/tests/test_voice_summary_truncation_boundary.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_CONSOLE = Path(__file__).resolve().parents[1]
if str(_CONSOLE) not in sys.path:
    sys.path.insert(0, str(_CONSOLE))

from corvin_console.routes import voice as V


class _FakeRec:
    tenant_id = "_default"
    sid_fingerprint = "abcd1234"


# ── _cut_at_sentence_boundary unit tests ─────────────────────────────────────

def test_short_text_within_limit_is_returned_unchanged():
    text = "Kurzer Satz."
    assert V._cut_at_sentence_boundary(text, 400) == text


def test_long_text_cuts_at_the_last_sentence_boundary_within_the_window():
    # Two sentences; limit lands inside the second one. The first sentence
    # must clear the default min_cut=80 threshold (realistic summaries are
    # well past that) so the boundary is actually used.
    first = ("Erster Satz mit ausreichend Inhalt, um weit über die minimale "
             "Schnittgrenze von achtzig Zeichen hinauszukommen und trotzdem "
             "eine sinnvolle Zusammenfassung zu bleiben. ")
    assert len(first) > 80
    second = "Zweiter Satz, der über das Limit hinausragt und mitten drin abgeschnitten würde."
    text = first + second
    limit = len(first) + 20  # cuts partway into `second`
    out = V._cut_at_sentence_boundary(text, limit)
    assert out == first.strip()
    assert out.endswith(".")
    # Must not contain a fragment of the second sentence's words.
    assert "abgeschnitten" not in out


def test_no_sentence_boundary_within_window_falls_back_to_hard_cut():
    # A single long run with no ". "/"! "/"? " before min_cut=80 chars in —
    # must still bound the length even though it cuts mid-word.
    text = "x" * 500
    out = V._cut_at_sentence_boundary(text, 100)
    assert len(out) == 100


def test_boundary_found_but_too_early_is_ignored_min_cut():
    # A period at position 5 is BELOW the default min_cut=80 -- using it would
    # produce a near-empty, useless "summary". Must hard-cut at the limit
    # instead of collapsing to a few words.
    text = "Hi. " + ("y" * 300)
    out = V._cut_at_sentence_boundary(text, 100)
    assert len(out) == 100
    assert not out.endswith(".")


# ── Integration: a long LLM summary must not be raw-sliced mid-word ─────────

def test_voice_tts_sync_cuts_an_oversized_summary_at_a_sentence_boundary(monkeypatch):
    """The core regression: summarize.py returning a summary LONGER than
    _TTS_PROVIDER_CHAR_LIMIT (legitimate per adaptive_target's uncapped 85%
    scaling for long answers) must still end at a sentence boundary once it
    reaches the TTS provider, not mid-word."""
    first_sentence = "Dies ist der erste vollständige Satz der Zusammenfassung. "
    # Pad well past the provider limit with a second sentence that would be
    # sliced mid-word by a raw [:4000] cut.
    long_tail = "Und hier folgt ein sehr langer zweiter Satz voller Substantive. " * 100
    oversized_summary = first_sentence + long_tail
    assert len(oversized_summary) > V._TTS_PROVIDER_CHAR_LIMIT

    monkeypatch.setattr(V, "_summarize_for_speech", lambda text, lang: oversized_summary)
    monkeypatch.setattr(V, "_resolve_tts_provider", lambda: "openai")
    monkeypatch.setattr(V, "_resolve_tts_voice", lambda lang: None)
    monkeypatch.setattr(V.console_audit, "action_performed", lambda **k: None)

    captured = {}

    def _fake_try_openai_tts(text, lang, voice):
        captured["text"] = text
        return b"OggS" + b"\x00" * 16

    monkeypatch.setattr(V, "_try_openai_tts", _fake_try_openai_tts)

    resp = V._voice_tts_sync(
        V.TtsRequest(text="Eine sehr lange ursprüngliche Antwort. " * 200, lang="de"),
        rec=_FakeRec(),
    )

    assert resp.status_code == 200
    spoken = captured["text"]
    assert len(spoken) <= V._TTS_PROVIDER_CHAR_LIMIT
    assert spoken.endswith("."), (
        f"expected a sentence-boundary cut, got a raw mid-word tail: {spoken[-60:]!r}"
    )
    # Regression guard: the old bare-slice bug would land mid-word here.
    assert not spoken.rstrip(".").endswith("Substanti"), "cut landed mid-word"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
