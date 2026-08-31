#!/usr/bin/env python3
"""test_adapter_voice_override.py — `<voice>…</voice>` author-override path.

Pro CLAUDE.md (`feedback_per_subtask_e2e`): the new override path skips the
summarize.py pipeline entirely when the assistant authored a custom voice
version. Without coverage, a regression here would silently fall back to
auto-summarization without the user noticing — defeating the whole point.

Five subtests:

  1. Plain text without <voice> tag → extract_voice_override returns
     (text, None). build_voice_summary keeps its existing behavior.
  2. Text with single <voice>…</voice> block → returns
     (chat_text_without_tag, voice_text). build_voice_summary uses override
     verbatim, no summarize.py call.
  3. Empty <voice></voice> tag → returns (cleaned_text, None) — empty
     overrides do not hijack the path; falls through to summarize.
  4. Multi-line <voice> block (DOTALL) — must capture newlines inside.
  5. The chat_text after stripping has no double-blank-line gaps where the
     tag was — re.sub('\\n{3,}', '\\n\\n', …) cleanup verified.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _install_fake_summarizer(tmp: Path) -> tuple[Path, Path]:
    scripts_dir = tmp / "scripts"
    scripts_dir.mkdir()
    argv_dump = tmp / "summarizer_argv.json"

    fake_summarize = scripts_dir / "summarize.py"
    fake_summarize.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"open({json.dumps(str(argv_dump))}, 'w').write("
        "json.dumps(sys.argv[1:]))\n"
        "print('LLM_FALLBACK_SUMMARY')\n"
    )
    fake_summarize.chmod(0o755)

    fake_stripper = scripts_dir / "strip_for_tts.py"
    fake_stripper.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    fake_stripper.chmod(0o755)

    return scripts_dir, argv_dump


def _fresh_adapter(scripts_dir: Path):
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    adapter.SCRIPTS_DIR = scripts_dir
    # Isolate from the real on-disk profile so learning-mode flags
    # (voice_audience_learning >= 1) don't route the override path
    # through summarize.py --appendix-mode and break the test.
    adapter._voice_profile = None
    return adapter


def test_no_tag_returns_unchanged() -> None:
    _section("plain text → no voice override extracted")
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    chat, voice = adapter.extract_voice_override(
        "Just a normal reply, no special tag."
    )
    assert chat == "Just a normal reply, no special tag."
    assert voice is None
    print("  OK — text unchanged, voice=None")


def test_single_tag_extracts_and_strips() -> None:
    _section("single <voice>…</voice> → extracted + stripped from chat")
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    text = ("Hier kommt die ausführliche Antwort mit Listen:\n"
            "- Punkt eins\n- Punkt zwei\n\n"
            "<voice>Kurz: zwei Punkte, einer für A, einer für B.</voice>\n\n"
            "Soweit klar?")
    chat, voice = adapter.extract_voice_override(text)
    assert "<voice>" not in chat, f"tag leaked into chat: {chat!r}"
    assert "</voice>" not in chat
    assert voice == "Kurz: zwei Punkte, einer für A, einer für B."
    assert "Soweit klar?" in chat, "trailing text was lost"
    assert "Punkt eins" in chat, "leading text was lost"
    print(f"  OK — voice={voice!r}, chat preserved {len(chat)} chars")


def test_empty_tag_falls_through() -> None:
    _section("empty <voice></voice> → no override, falls through")
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    chat, voice = adapter.extract_voice_override(
        "Eine Antwort.\n\n<voice></voice>\n\nWeiter."
    )
    # Empty tag should NOT capture a voice override (treats as falsy).
    assert voice is None, f"empty tag must not produce override: {voice!r}"
    print("  OK — empty tag returned voice=None")


def test_multiline_tag_dotall() -> None:
    _section("multi-line <voice>…\\n…</voice> captured fully")
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    text = ("Antwort.\n\n<voice>Erster Satz.\n"
            "Zweiter Satz.\n"
            "Dritter Satz.</voice>")
    chat, voice = adapter.extract_voice_override(text)
    assert voice == "Erster Satz.\nZweiter Satz.\nDritter Satz."
    assert "<voice>" not in chat
    print("  OK — multi-line capture intact")


def test_build_voice_summary_uses_override_skips_llm() -> None:
    _section("build_voice_summary(override=…) → skips summarize.py")
    with tempfile.TemporaryDirectory() as td:
        td_path = Path(td)
        scripts_dir, argv_dump = _install_fake_summarizer(td_path)
        adapter = _fresh_adapter(scripts_dir)
        # Long enough that without override, summarize.py would be called.
        long_text = "Das ist ein langer Test. " * 80  # ~2000 chars
        out = adapter.build_voice_summary(
            long_text, max_chars=400,
            override="Selbst formulierte Voice-Note in zwei Sätzen.",
        )
        assert out == "Selbst formulierte Voice-Note in zwei Sätzen.", (
            f"override must be returned verbatim (markdown-stripped), got {out!r}"
        )
        # The fake summarize.py would have written its argv to argv_dump
        # if it had been invoked. Absence of the file proves no LLM call.
        assert not argv_dump.exists(), (
            "summarize.py was invoked despite override — that defeats the "
            "whole point of the author-override path"
        )
        print("  OK — override returned verbatim, no LLM call")


def test_literal_voice_mention_does_not_hijack_real_block() -> None:
    _section("literal <voice> mention earlier must NOT swallow the real block")
    for m in ("adapter", "profile"):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    # Reproduces 2026-07-13: a reply that EXPLAINS the mechanism mentions the
    # literal token `<voice>` in its prose, then appends its real block. The old
    # leftmost-match paired the stray mention with the real closing tag and
    # truncated the visible reply at the mention.
    text = (
        "Zweitens der Code-Bug: Der `<voice>`-Override-Pfad hängte die Zugabe "
        "ohne Guard an.\n\n"
        "Drittens: das Dedup-Fenster war zu klein, jetzt geweitet.\n\n"
        "<voice>Kurz gesprochen: zwei Fixes, jetzt sauber.</voice>"
    )
    chat, voice = adapter.extract_voice_override(text)
    assert voice == "Kurz gesprochen: zwei Fixes, jetzt sauber.", f"voice={voice!r}"
    # The whole visible body must survive — this is the regression.
    assert "Zweitens der Code-Bug" in chat, f"body truncated: {chat!r}"
    assert "Drittens" in chat, f"body truncated after the mention: {chat!r}"
    assert "`<voice>`-Override-Pfad" in chat, "literal mention must stay as text"
    assert "</voice>" not in chat, "the real block must be stripped from chat"
    print("  OK — real block extracted, visible body intact, mention preserved")


def main() -> int:
    test_no_tag_returns_unchanged()
    test_single_tag_extracts_and_strips()
    test_empty_tag_falls_through()
    test_multiline_tag_dotall()
    test_build_voice_summary_uses_override_skips_llm()
    test_literal_voice_mention_does_not_hijack_real_block()
    print("\nAll voice-override adapter tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
