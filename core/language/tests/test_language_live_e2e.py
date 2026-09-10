"""Real-LLM E2E for the ADR-0650 language path.

Rule 6 note — WHY this drives the resolver and not the translator:
``OutputTranslator`` is the LLM-involving component on paper, but its
``llm_translate_fn`` is ``None`` at every construction site in the tree
(``LanguageOutputBoundary.__init__`` hard-codes ``llm_translate_fn=None`` with
a "Will be wired by chat_runtime" comment), and no production code constructs
either class. There is no LLM call to drive there yet. The resolver path IS
wired (``chat_runtime.stream_turn`` → ``resolve_turn_language``) and it is the
half that consumes real model text, so that is what this exercises: a real
``claude -p`` turn produces real German prose, and the resolver must decide
"de" from it.
"""
from __future__ import annotations

import os
import subprocess

import pytest

from core.language import LanguageResolver, LanguageSource, detect_language

_LIVE = os.environ.get("CLAUDE_LIVE_E2E") == "1"


@pytest.mark.live
@pytest.mark.skipif(not _LIVE, reason="set CLAUDE_LIVE_E2E=1 to run the real claude -p turn")
def test_real_llm_german_response_resolves_to_german() -> None:
    """A real German model answer must resolve to 'de' via the RESPONSE tier."""
    proc = subprocess.run(
        [
            "claude", "-p",
            "Antworte ausschliesslich auf Deutsch, in zwei vollstaendigen Saetzen: "
            "Was ist eine Datei in einem Betriebssystem?",
            "--model", "haiku",
        ],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, f"claude -p failed: {proc.stderr[:400]}"
    answer = proc.stdout.strip()
    assert len(answer) > 40, f"unexpectedly short answer: {answer!r}"

    # Detection on the real model output.
    assert detect_language(answer) == "de", f"detector said {detect_language(answer)!r} for: {answer[:200]!r}"

    # And through the real resolver: no profile + ambiguous user text means the
    # decision has to come from the model's own (German) response.
    ctx = LanguageResolver(profile_lang=None).resolve(
        user_text="123 test", response_text=answer,
    )
    assert ctx.resolved_lang == "de"
    assert ctx.source is LanguageSource.RESPONSE
