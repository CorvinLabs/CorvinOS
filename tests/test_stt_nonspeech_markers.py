"""whisper.cpp non-speech marker stripping (2026-07-12).

whisper.cpp emits bracketed markers for silence/noise segments
(``[BLANK_AUDIO]``, ``[ Silence ]``, ``[MUSIC]`` …). Left in, an empty/near-empty
voice note reached the model — and the user — as literal "[BLANK_AUDIO]" (the
reported "[BLANK_AUDIO] HELLO"). The provider strips ONLY this closed artifact
set, never free-form bracketed text the user actually spoke.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "operator" / "voice" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt.local_whisper import _strip_nonspeech_markers as strip  # type: ignore  # noqa: E402


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("[BLANK_AUDIO] HELLO", "HELLO"),          # the reported bug
        ("[BLANK_AUDIO]", ""),                      # pure-silence note → empty
        ("Hallo [ Silence ] Welt", "Hallo Welt"),
        ("(music) test (noise)", "test"),
        ("[BLANK_AUDIO] [BLANK_AUDIO] hallo", "hallo"),
        ("HELLO [inaudible] world", "HELLO world"),
        ("normal sentence, nothing to strip", "normal sentence, nothing to strip"),
    ],
)
def test_strips_known_nonspeech_markers(raw, expected):
    assert strip(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "Ich sagte [wichtig] dazu",     # a real word in brackets — NOT a marker
        "die Liste [1] [2] [3]",        # numeric refs the user dictated
        "commit [abc123] pushed",       # arbitrary bracketed token
    ],
)
def test_keeps_legitimate_bracketed_content(raw):
    # Only the closed artifact set is stripped; genuine bracketed speech stays.
    assert strip(raw) == raw


@pytest.mark.parametrize(
    "raw",
    [
        "(BLANK_AUDIO] HELLO",   # mismatched brackets — never emitted by any backend
        "HELLO [music) world",
        "test (silence] test",
    ],
)
def test_keeps_mismatched_brackets(raw):
    # Regression: an independent open/close character class used to strip
    # mismatched pairs like "(BLANK_AUDIO]" just as readily as well-formed
    # markers — neither whisper.cpp nor faster-whisper ever emit a mismatched
    # pair, so this text is never a real artifact and must be left alone.
    assert strip(raw) == raw
