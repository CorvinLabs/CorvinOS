#!/usr/bin/env python3
"""strip_for_tts.py — code-fence stripping (review side-note, 2026-07-20).

An UNTERMINATED code fence (streaming cut-off, or a model that forgot the
closing ```) was not stripped at all: the old pattern required a closing
fence, so the whole code block was read aloud / fed to the summarizer.
detect_lang.py's twin (_CODE_FENCE_RE) already carries the ``\\Z`` arm —
strip_for_tts now mirrors it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import strip_for_tts as S  # noqa: E402

_FENCE = "`" * 3


def test_terminated_fence_is_stripped():
    text = f"before\n{_FENCE}python\nsecret_code()\n{_FENCE}\nafter"
    out = S.strip_code_only(text)
    assert "secret_code" not in out
    assert "before" in out and "after" in out


def test_unterminated_fence_is_stripped_to_eof():
    text = f"prose intro\n{_FENCE}python\nsecret_code()\nmore_code()"
    out = S.strip_code_only(text)
    assert "secret_code" not in out and "more_code" not in out
    assert "prose intro" in out


def test_terminated_then_unterminated_fence():
    text = f"a\n{_FENCE}\ncode1\n{_FENCE}\nb\n{_FENCE}\ncode2"
    out = S.strip_code_only(text)
    assert "code1" not in out and "code2" not in out
    assert "a" in out and "b" in out


def test_full_mode_also_strips_unterminated_fence():
    out = S.strip_full(f"Result:\n{_FENCE}\nraise Boom()")
    assert "Boom" not in out
    assert "Result" in out


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
