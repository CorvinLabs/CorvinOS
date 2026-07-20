"""Profile display_language must be AUTHORITATIVE for text output (2026-07-20).

Reported: Settings → Profile shows "Display Language: Deutsch", but replies came
back in English — and, via a stray CJK character in a reply, sometimes Chinese.
Root cause on the TEXT side: the system-prompt line read
"- Language: de (default; still match the user's actual writing language)",
i.e. it explicitly instructed the model to OVERRIDE the operator's setting.
(The voice side had its own cause in the console frontend: per-reply text
detection outranked the profile, and one CJK char flipped it to "zh".)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import profile as p  # noqa: E402


def test_language_line_is_authoritative(monkeypatch):
    monkeypatch.setattr(p, "load", lambda *a, **k: {"display_language": "de"})
    blk = p.for_system_prompt() or ""
    line = next(l for l in blk.splitlines() if "Language" in l)
    assert "ALWAYS answer in de" in line
    assert "still match the user's actual writing language" not in line
    assert "overrides" in line


def test_no_language_line_when_unset(monkeypatch):
    monkeypatch.setattr(p, "load", lambda *a, **k: {"name": "x"})
    assert "Language:" not in (p.for_system_prompt() or "")


# ── the console side: pinned language must be the LAST word in the prompt ────

def test_console_swaps_the_autodetect_rule_and_closes_with_the_pin(monkeypatch):
    """Three layers were needed before a pinned language actually stuck:

    1. the profile line told the model to override the setting,
    2. the base prompt carried a contradicting auto-detect rule,
    3. and even with both fixed the single early directive was diluted in a
       ~10 KB English prompt — reproduced end-to-end (the written prompt file
       provably contained "ALWAYS reply in de" with no competing rule, yet the
       reply came back English; the same directive alone via `claude -p` gave
       German). The closing block is what made it stick.
    """
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core" / "console"))
    from corvin_console import chat_runtime as cr

    class _P2:
        @staticmethod
        def load(*a, **k): return {"display_language": "de"}
    monkeypatch.setattr(cr, "_voice_profile", _P2)

    rule = cr._language_rule()
    assert "ALWAYS reply in de" in rule
    assert cr._LANGUAGE_RULE_AUTODETECT != rule          # auto-detect replaced

    closing = cr._language_closing_block()
    assert "OUTPUT LANGUAGE" in closing and "overrides everything above" in closing
    assert "you still answer in de" in closing


def test_console_keeps_autodetect_when_no_language_pinned(monkeypatch):
    import sys as _s
    from pathlib import Path as _P
    _s.path.insert(0, str(_P(__file__).resolve().parents[2] / "core" / "console"))
    from corvin_console import chat_runtime as cr

    class _P3:
        @staticmethod
        def load(*a, **k): return {}
    monkeypatch.setattr(cr, "_voice_profile", _P3)
    assert cr._language_rule() == cr._LANGUAGE_RULE_AUTODETECT   # unchanged default
    assert cr._language_closing_block() == ""                    # no closing block
