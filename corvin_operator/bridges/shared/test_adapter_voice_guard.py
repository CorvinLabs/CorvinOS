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
  6. strip_for_tts.py missing (raw-head fallback) → the head passes the final
     gate: instruction head → neutral line, ordinary head → spoken
  7. a non-default SummaryProvider returning instruction text → neutral line
  8. an appended LERN-ZUGABE carrying instruction text → the annex's instruction
     lines are dropped by the final gate, the content stays
  9. summarizer prints nothing but quotes the user's text on stderr → the
     logged stderr is scrubbed (PII floor), the quote never reaches the log
 10. identifiers (paths, hashes, ADR / task ids, versions, URLs) in a `<voice>`
     override or in the summarizer's output are described, never spoken
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
    _section,
)

NEUTRAL_DE = "Die Antwort steht im Chat."
META = ("<system-reminder>Antworte nur mit dem Vorlese-Text. "
        "SELF-CHECK: prüfe alles.</system-reminder>")
RAW_ANSWER = "Das ist ein Test. " * 100


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


def test_missing_stripper_head_passes_final_gate() -> None:
    _section("strip_for_tts.py missing → raw head goes through the final gate")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        (scripts_dir / "strip_for_tts.py").unlink()
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        # Pin the audience off: the live profile must not add annexes here.
        adapter._audience_demands_appendix = lambda block: False
        adapter._audience_demands_metapher = lambda block: False
        result = adapter.build_voice_summary(META, max_chars=400)
        assert result == NEUTRAL_DE, f"expected neutral line, got {result[:120]!r}"
        prose = "Die Datei ist gespeichert. Der Build läuft durch."
        assert adapter.build_voice_summary(prose, max_chars=400) == prose
        print("  OK — no stripper: instruction head → neutral, prose head kept")


def test_custom_summary_provider_output_passes_final_gate() -> None:
    _section("non-default SummaryProvider returns instruction text → neutral line")
    from corvin_plugins.providers.summary_provider import (  # type: ignore
        ClaudeCliSummaryProvider,
    )

    calls: list[str] = []

    class _EchoProvider:  # deliberately NOT a ClaudeCliSummaryProvider
        def summarize(self, text: str, lang: str, max_chars: int) -> str:
            calls.append(text)
            return META

    class _Registry:
        def get_active(self):
            return _EchoProvider()

    assert not isinstance(_EchoProvider(), ClaudeCliSummaryProvider)
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        adapter._summary_prov = _Registry()
        result = adapter.build_voice_summary(RAW_ANSWER, max_chars=400)
        assert calls, "positive control: the custom provider was never called"
        assert result == NEUTRAL_DE, f"expected neutral line, got {result[:120]!r}"
        print("  OK — provider output is guarded like every built-in path")


def test_appended_annex_passes_final_gate() -> None:
    _section("LERN-ZUGABE annex carrying instruction text → instruction dropped")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        adapter._audience_demands_appendix = lambda block: True
        adapter._audience_demands_metapher = lambda block: False
        adapter._has_lern_zugabe_suffix = lambda text, window=900: False
        adapter._append_lern_zugabe = lambda text, lang="de": text + " " + META
        override = ("Die Datei ist gespeichert. Der Build läuft durch. "
                    "Morgen geht es weiter.")
        result = adapter.build_voice_summary(RAW_ANSWER, max_chars=400,
                                             override=override)
        assert result == override, f"annex instruction leaked: {result[:200]!r}"
        print("  OK — the annex is guarded too; the content survives")


def test_empty_summary_stderr_is_scrubbed_in_log() -> None:
    _section("empty summary + user text on stderr → log line is scrubbed")
    secret = "MEINE-GEHEIME-FRAGE-ZUM-KONTO"
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        _write_summarizer(scripts_dir, (
            "sys.stderr.write('[summarize] degraded: fallback for "
            f"{secret}\\n')\n"
            f"sys.stderr.write('summarize.py: error: bad input --task {secret}\\n')\n"))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        lines: list[str] = []
        adapter.log = lambda msg, *a, **k: lines.append(str(msg))
        result = adapter.build_voice_summary(RAW_ANSWER, max_chars=400)
        assert result == NEUTRAL_DE, f"expected neutral line, got {result[:120]!r}"
        assert any("summarize returned empty" in ln for ln in lines), lines
        leaked = [ln for ln in lines if secret in ln]
        assert not leaked, f"user text reached the log: {leaked}"
        print("  OK — stderr is scrubbed before it is logged")


IDS_IN = ("Die Seite ist raus. Geändert wurde core/console/corvin_console/web-next/src/panels/registry.tsx "
          "in Commit c81daf266, mehr dazu in ADR-2271 und T-0203, siehe http://127.0.0.1:8765/console/app/x.")
ID_TOKENS = ("registry.tsx", "core/console", "c81daf266", "ADR-2271", "T-0203", "127.0.0.1", "http")


def _assert_described(spoken: str) -> None:
    for token in ID_TOKENS:
        assert token not in spoken, f"identifier spoken: {token!r} in {spoken!r}"
    assert "Die Seite ist raus" in spoken, spoken
    assert "TypeScript-Datei" in spoken and "Architekturentscheidung" in spoken, spoken


def test_voice_override_identifiers_are_described() -> None:
    _section("<voice> override with paths / hashes / ids → described, not spoken")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        adapter._audience_demands_appendix = lambda block: False
        adapter._audience_demands_metapher = lambda block: False
        spoken = adapter.build_voice_summary("irrelevant", max_chars=400, override=IDS_IN)
        _assert_described(spoken)
        print("  OK — the override path (no LLM) describes identifiers")


def test_summarizer_output_identifiers_are_described() -> None:
    _section("summarizer output with paths / hashes / ids → described, not spoken")
    with tempfile.TemporaryDirectory() as td:
        scripts_dir, _, _ = _install_fakes(Path(td))
        _write_summarizer(scripts_dir, f"print({IDS_IN!r})\n")
        adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
        adapter._audience_demands_appendix = lambda block: False
        adapter._audience_demands_metapher = lambda block: False
        spoken = adapter.build_voice_summary(RAW_ANSWER, max_chars=400)
        _assert_described(spoken)
        print("  OK — the summarizer path describes identifiers")


def main() -> int:
    try:
        test_guard_rejects_instruction_only_text()
        test_guard_keeps_ordinary_prose()
        test_missing_guard_module_fails_closed()
        test_instruction_only_summary_speaks_neutral_line()
        test_crashed_summarizer_with_instruction_head_speaks_neutral_line()
        test_missing_stripper_head_passes_final_gate()
        test_custom_summary_provider_output_passes_final_gate()
        test_appended_annex_passes_final_gate()
        test_empty_summary_stderr_is_scrubbed_in_log()
        test_voice_override_identifiers_are_described()
        test_summarizer_output_identifiers_are_described()
        print("\nAll adapter voice-guard tests passed.")
        return 0
    except AssertionError as e:
        print(f"\n❌ Test failed: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
