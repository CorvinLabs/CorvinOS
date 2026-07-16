"""voice_tag — the `<voice>…</voice>` override extraction.

The offsets are the whole game here: the function returns two SLICES of the
input, so anything that shifts an index corrupts both the visible reply and the
spoken text at once. Two real bugs live in this file's history:

  * leftmost-match pairing let a literal `<voice>` mentioned in the prose pair
    with the real block's closing tag, truncating the visible reply
    (reported 2026-07-13);
  * offsets computed on `text.lower()` — which is NOT length-preserving —
    were applied to the original string.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from voice_tag import extract_voice_override, with_voice_override  # noqa: E402


def test_plain_block_is_extracted() -> None:
    assert extract_voice_override("Sichtbar.\n\n<voice>Gesprochen.</voice>") == (
        "Sichtbar.", "Gesprochen.")


def test_no_block_is_a_passthrough() -> None:
    assert extract_voice_override("Nur Text.") == ("Nur Text.", None)


def test_unclosed_tag_is_not_an_override() -> None:
    assert extract_voice_override("Text <voice>offen") == ("Text <voice>offen", None)


def test_tags_are_matched_case_insensitively() -> None:
    assert extract_voice_override("Sichtbar.\n\n<VOICE>Gesprochen.</VOICE>") == (
        "Sichtbar.", "Gesprochen.")


def test_a_literal_mention_in_the_prose_is_not_hijacked() -> None:
    """Regression: leftmost-match paired the mention with the real close tag."""
    chat, spoken = extract_voice_override(
        "Ich nutze den <voice> Pfad hier.\n\n<voice>Gesprochen.</voice>")
    assert spoken == "Gesprochen."
    assert chat == "Ich nutze den <voice> Pfad hier."


def test_dotted_capital_i_does_not_shift_the_offsets() -> None:
    """Regression: str.lower() maps U+0130 to TWO codepoints.

    Offsets were computed on the lowered copy and applied to the original, so a
    reply containing 'İ' before the block cut BOTH halves mid-tag: the chat text
    ended in '<vo' and TTS spoke 'prochener Text hier.</v'.
    """
    src = "İstanbul İzmir İçel.\n\n<voice>Gesprochener Text hier.</voice>"
    assert len(src.lower()) != len(src), "premise: lower() is not length-preserving"
    assert extract_voice_override(src) == ("İstanbul İzmir İçel.", "Gesprochener Text hier.")


def test_roundtrip_through_with_voice_override() -> None:
    reply = with_voice_override("Lauf `corvin --flag` aus.", "Fuehre den Befehl aus.")
    chat, spoken = extract_voice_override(reply)
    assert spoken == "Fuehre den Befehl aus."
    assert "<" not in chat and ">" not in chat  # visible half is neutralised
