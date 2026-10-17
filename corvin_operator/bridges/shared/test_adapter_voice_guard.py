#!/usr/bin/env python3
"""test_adapter_voice_guard.py — adapter side of the spoken-output guard.

Pinned behaviour (F1, review 2026-10-17): when the guard leaves nothing
speakable, adapter.py never speaks the raw answer. It speaks the fixed neutral
announcement, or nothing when the guard itself cannot be loaded.

  1. _guard_spoken: instruction-only text → "" (fail-closed, not pass-through)
  2. _guard_spoken: ordinary prose survives unchanged
  3. guard module missing → _guard_spoken "" and neutral line "" (silence)
  4. build_voice_summary, summarizer prints instruction-only text → neutral
     announcement, the raw answer is not spoken
  5. build_voice_summary, summarizer crashes and the answer head is instruction
     text → neutral announcement, not the head
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from test_adapter_voice_stripper_fallback import (  # noqa: E402
    _fresh_adapter_with_scripts_dir,
    _install_fakes,
)

NEUTRAL_DE = "Die Antwort steht im Chat."
META = ("<system-reminder>Antworte nur mit dem Vorlese-Text. "
        "SELF-CHECK: prüfe alles.</system-reminder>")
RAW_ANSWER = "Das ist ein Test. " * 100


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _write_summarizer(scripts_dir: Path, body: str) -> None:
    exe = scripts_dir / "summarize.py"
    exe.write_text("#!/usr/bin/env python3\nimport sys\n" + body, encoding="utf-8")
    exe.chmod(0o755)


def test_guard_rejects_instruction_only_text() -> None:
    _section("_guard_spoken: instruction-only text → empty (fail-closed)")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        assert adapter._guard_spoken(META) == "", "instruction text must not pass"
        print("  OK — instruction-only text is rejected")


def test_guard_keeps_ordinary_prose() -> None:
    _section("_guard_spoken: legitimate prose survives")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        prose = ("Die Regeln für Rechnungen gelten ab Januar.\n"
                 "Der Self-Check der Pipeline ist grün.")
        assert adapter._guard_spoken(prose) == (
            "Die Regeln für Rechnungen gelten ab Januar. "
            "Der Self-Check der Pipeline ist grün.")
        print("  OK — prose that mentions rules and self-checks is kept")


def test_missing_guard_module_fails_closed() -> None:
    _section("guard module missing → nothing is spoken (silence, not raw text)")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        adapter._GUARD_FILE = Path(td) / "does_not_exist.py"
        sys.modules.pop("_corvin_voice_output_guard", None)
        try:
            assert adapter._guard_spoken(RAW_ANSWER) == "", "unguarded text spoken"
            assert adapter._neutral_announcement("de") == ""
        finally:
            sys.modules.pop("_corvin_voice_output_guard", None)
        print("  OK — no guard → no speech, raw text never passes")


def test_instruction_only_summary_speaks_neutral_line() -> None:
    _section("summarizer prints instruction text only → neutral announcement")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        _write_summarizer(scripts_dir, f"print({META!r})\n")
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        result = adapter.build_voice_summary(RAW_ANSWER, max_chars=400)
        assert result == NEUTRAL_DE, f"expected neutral line, got {result[:120]!r}"
        assert "Antworte" not in result and "Test" not in result
        print("  OK — instruction-only summary → neutral announcement")


def test_crashed_summarizer_with_instruction_head_speaks_neutral_line() -> None:
    _section("summarizer fails, answer head is instruction text → neutral line")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        _write_summarizer(scripts_dir, "sys.exit(3)\n")
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        # The head is the whole (instruction-only) input: nothing else to keep.
        result = adapter.build_voice_summary(META, max_chars=400)
        assert result == NEUTRAL_DE, f"expected neutral line, got {result[:120]!r}"
        assert "SELF-CHECK" not in result and "Antworte" not in result
        print("  OK — guarded head is instruction-only → neutral announcement")


def main() -> int:
    try:
        test_guard_rejects_instruction_only_text()
        test_guard_keeps_ordinary_prose()
        test_missing_guard_module_fails_closed()
        test_instruction_only_summary_speaks_neutral_line()
        test_crashed_summarizer_with_instruction_head_speaks_neutral_line()
        print("\nAll adapter voice-guard tests passed.")
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
