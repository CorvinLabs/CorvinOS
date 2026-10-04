"""Marker-driven timing for diagram highlights (ADR-2212).

OpenAI TTS (tts-1) takes plain text and returns no word-level timestamps —
no SSML <mark> support, no forced-alignment output. So highlight timing is
an ESTIMATE, not a measurement: marker character-offset in the clean text,
scaled proportionally against the synthesized audio's total duration. This
assumes a roughly constant speech rate, which holds well enough at a fixed
`speed` parameter for cueing purposes, but is not word-accurate. A future
v2 (Whisper re-alignment of the synthesized audio) would remove that
assumption; this module documents the limitation rather than hiding it.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

MARKER_RE = re.compile(r"\[\[hl:([A-Za-z0-9_-]{1,64})\]\]")


@dataclass(frozen=True)
class MarkerOffset:
    char_offset: int  # position in the CLEAN text (marker already removed)
    element_id: str


def split_markers(raw_text: str) -> tuple[str, list[MarkerOffset]]:
    """Strip ``[[hl:element_id]]`` markers out of raw_text.

    Returns (clean_text, offsets) where clean_text is what should be sent to
    TTS (the listener never hears marker syntax) and offsets locate each
    marker's position in clean_text, in the order they appeared.
    """
    offsets: list[MarkerOffset] = []
    out: list[str] = []
    cursor = 0
    for m in MARKER_RE.finditer(raw_text):
        out.append(raw_text[cursor:m.start()])
        clean_so_far = "".join(out)
        offsets.append(MarkerOffset(char_offset=len(clean_so_far), element_id=m.group(1)))
        cursor = m.end()
    out.append(raw_text[cursor:])
    clean_text = "".join(out)
    return clean_text, offsets


def estimate_marker_timestamps(
    offsets: list[MarkerOffset], clean_text_len: int, audio_duration_s: float
) -> list[tuple[float, str]]:
    """Proportional timing estimate: timestamp = (char_offset / len) * duration.

    Returns [] if there's nothing to time (no offsets, empty text, or a
    non-positive duration) — callers fall back to the current one-step-per-
    scene behavior rather than crashing on a degenerate scene.
    """
    if not offsets or clean_text_len <= 0 or audio_duration_s <= 0:
        return []
    return [
        (round((o.char_offset / clean_text_len) * audio_duration_s, 3), o.element_id)
        for o in offsets
    ]
