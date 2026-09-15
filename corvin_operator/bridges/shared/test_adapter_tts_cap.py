#!/usr/bin/env python3
"""test_adapter_tts_cap.py — Hard-cap TTS input to OpenAI's 4096 limit.

Pro CLAUDE.md (`feedback_per_subtask_e2e`): the cap is reproduced from
the 2026-05-08 01:17 discord bridge incident where a 4067-char answer
went through build_voice_summary → summarize.py with a 400-char hint
that adaptive_target raised to 3457, the LLM produced ~4100 chars, and
synthesize_voice_note pushed all of it to OpenAI which returned HTTP
400 (`string_too_long, max 4096`). The user got the text reply but no
voice-note.

Three sub-tests:
  1. Short text passes through unchanged (no spurious truncation).
  2. Long text is capped to <= 4000 chars at a sentence boundary
     (preferred), not mid-word, not mid-sentence when avoidable.
  3. Pathological text with no sentence terminator falls back to a
     word boundary; still <= 4000.

These are unit-style assertions on the pure helper `_cap_for_openai_tts`
— sauberer als ein Mock-Roundtrip durch den OpenAI-Client. Das echte
Integrations-Risiko (synthesize_voice_note vergisst die Cap aufzurufen)
ist durch das call-site Audit oben in adapter.py gehandhabt.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_short_text_unchanged() -> None:
    _section("short text passes through unchanged")
    txt = "Hallo, das ist eine kurze Sprachnachricht. Drei Sätze. Ende."
    out = adapter._cap_for_openai_tts(txt)
    assert out == txt, f"expected unchanged, got {out!r}"
    assert out is txt, "should return same object for short input — saves an alloc"
    print(f"  OK — {len(out)} chars unchanged")


def test_long_text_capped_at_sentence() -> None:
    _section("long text capped at sentence boundary")
    # Build 5000 chars of repeating sentences; the cap should land at
    # one of the periods, not mid-word.
    sentence = "Das ist ein deutscher Satz mit Inhalt. "  # 39 chars
    txt = sentence * 130  # 5070 chars
    out = adapter._cap_for_openai_tts(txt)
    assert len(out) <= 4000, f"len {len(out)} exceeds cap"
    assert out.endswith("."), (
        f"expected period at end of capped slice, got tail {out[-15:]!r}"
    )
    print(f"  OK — {len(txt)} → {len(out)} at sentence boundary")


def test_long_text_no_terminator_falls_back_to_word() -> None:
    _section("no-terminator text falls back to word boundary")
    # 6000 chars of words separated by spaces, no period anywhere.
    txt = ("alpha beta gamma delta epsilon zeta eta theta iota kappa "
           "lambda mu nu xi omicron pi rho sigma tau upsilon phi chi "
           "psi omega ") * 60  # > 4000 chars, no '.'
    out = adapter._cap_for_openai_tts(txt)
    assert len(out) <= 4000, f"len {len(out)} exceeds cap"
    # Must end on a complete word (no trailing partial token).
    assert not out.endswith(" "), f"trailing space at boundary cut: {out[-15:]!r}"
    last_word = out.rsplit(" ", 1)[-1]
    assert last_word.isalpha(), (
        f"last token {last_word!r} not a clean word — cut hit mid-word"
    )
    print(f"  OK — {len(txt)} → {len(out)} at word boundary, last={last_word!r}")


def test_real_incident_size_4067() -> None:
    _section("the actual 2026-05-08 incident: 4067 chars")
    # Reproduce the input shape: 4067 chars including `## ` headings
    # and `**bold**` markdown plus a few list items.
    body = (
        "## Warum der Hype passiert ist\n\n"
        "369k Sterne, 76k Forks, 500+ Contributors, Sponsoren von OpenAI bis "
        "NVIDIA — das ist nicht „beliebt\", das ist viral geworden. Drei Faktoren "
        "haben sich addiert. "
    )
    body += body * 30  # >4000 chars
    txt = body[:4067]
    assert len(txt) == 4067
    out = adapter._cap_for_openai_tts(txt)
    assert len(out) <= 4000, f"incident-size input not capped: {len(out)}"
    print(f"  OK — {len(txt)} → {len(out)}")


if __name__ == "__main__":
    test_short_text_unchanged()
    test_long_text_capped_at_sentence()
    test_long_text_no_terminator_falls_back_to_word()
    test_real_incident_size_4067()
    print("\nALL OK")
