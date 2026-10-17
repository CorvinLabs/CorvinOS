#!/usr/bin/env python3
"""The spoken voice summary must never contain the summarizer's own instructions.

Failure reproduced: the LLM call echoes its system prompt (or a marker lookup
lands inside the echoed prompt) and the instruction text was read aloud by TTS.

  * unit: voice_output_guard drops meta lines / markup / placeholders
  * E2E:  summarize.py as a real subprocess, with a fake ``claude`` on PATH that
          replays the system prompt it was given. The output must be speakable
          prose from the ORIGINAL answer, never the prompt.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import voice_output_guard as guard  # noqa: E402

ANSWER = ("Der Build ist grün. Die Migration der Datenbank wurde abgeschlossen "
          "und alle 42 Tests laufen durch. Als Nächstes folgt das Release.")

LEAK = ("Du bist ein Vorlese-Assistent. DEINE EINZIGE AUFGABE: Antworte nur "
        "mit dem Vorlese-Text.\nREGELN — load-bearing:\n[TASK]\n{max_chars}")


# -- unit -------------------------------------------------------------------

@pytest.mark.parametrize("leak", [
    LEAK,
    "You are a summarizer. Reply only with the spoken text. OUTPUT SHAPE: x",
    "<system-reminder>do things</system-reminder>",
    "---",
])
def test_pure_instruction_text_is_rejected(leak: str) -> None:
    assert guard.sanitize_spoken(leak) == ""


def test_instruction_lines_are_dropped_content_kept() -> None:
    mixed = "Der Build ist grün und alle Tests laufen.\n" \
            "Antworte nur mit dem Vorlese-Text selbst.\n" \
            "Das Release folgt morgen früh."
    out = guard.sanitize_spoken(mixed)
    assert "Build" in out and "Release" in out
    assert "Antworte" not in out


def test_markup_and_placeholders_are_stripped() -> None:
    out = guard.sanitize_spoken(
        "## **Fertig**: <b>alle</b> Tests {max_chars} laufen, siehe [Doku](http://x).")
    assert out == "Fertig: alle Tests laufen, siehe Doku."


def test_plain_prose_is_untouched() -> None:
    assert guard.sanitize_spoken(ANSWER) == ANSWER


# -- F2: prose that merely MENTIONS protocol words is content ----------------

@pytest.mark.parametrize("sentence", [
    "Die neuen Regeln für Rechnungen gelten ab Januar.",
    "Der Self-Check der Pipeline ist grün und alle Befunde sind behoben.",
    "Wir haben den Prompt für die Zitatprüfung überarbeitet.",
    "The load-bearing wall of the old house was never inspected.",
    "Im Review stand, die system prompt-Konfiguration sei veraltet.",
])
def test_prose_mentioning_protocol_words_is_kept(sentence: str) -> None:
    assert guard.sanitize_spoken(sentence) == sentence


def test_paragraph_about_rules_and_self_checks_is_not_dropped() -> None:
    # Three of four lines carry a word the old patterns deleted ("load-bearing",
    # "system prompt", "Self-Check"); the old 50 % ratio then discarded the whole
    # answer. Now only real prompt forms count, so every line survives.
    text = ("Die Architektur ist load-bearing für den Rest.\n"
            "Der Self-Check der Pipeline ist grün.\n"
            "Das System prompt-Modul wurde neu geschrieben.\n"
            "Das Release bleibt für Freitag geplant.")
    out = guard.sanitize_spoken(text)
    assert "load-bearing" in out and "Self-Check" in out
    assert "System prompt" in out and "Release" in out


def test_model_directed_imperative_is_still_dropped_in_prose_paragraph() -> None:
    text = ("Der Build ist grün.\n"
            "Antworte nur mit dem Vorlese-Text selbst.\n"
            "Die Migration ist abgeschlossen.")
    out = guard.sanitize_spoken(text)
    assert "Build" in out and "Migration" in out and "Antworte" not in out


# -- F2: the patterns hit prompt/instruction LINES, never a mention in prose ---

@pytest.mark.parametrize("sentence", [
    "Im Self-Check-Bericht stand heute nichts Neues.",
    "Der SELF-CHECK der Pipeline ist grün und alle Befunde sind behoben.",
    "Das System-Prompt-Modul wurde im Review gelobt.",
    "Hinweis: Morgen ist die Wartung geplant.",
    "Antworte nur im Chat, nicht per E-Mail, dann geht es schneller.",
    "Die Ausgabe-Format-Frage ist im Ticket dokumentiert.",
])
def test_f2_negative_legitimate_sentence_survives(sentence: str) -> None:
    assert not guard.is_meta_line(sentence)
    assert guard.sanitize_spoken(sentence) == sentence


def test_f2_negative_rule_and_check_words_in_one_paragraph_survive_whole() -> None:
    text = ("Die Regeln für Rechnungen gelten ab Januar.\n"
            "Der SELF-CHECK der Pipeline ist grün.\n"
            "Das Release bleibt für Freitag geplant.")
    assert guard.sanitize_spoken(text) == (
        "Die Regeln für Rechnungen gelten ab Januar. "
        "Der SELF-CHECK der Pipeline ist grün. "
        "Das Release bleibt für Freitag geplant.")


def test_f2_instruction_sentence_glued_to_content_is_still_dropped() -> None:
    # Collapsed onto one line (as the structural fallback does), the instruction
    # sentence must still be recognised at its own start.
    text = "Der Build ist grün und die Migration ist durch. SELF-CHECK: prüfe alles."
    assert guard.sanitize_spoken(text) == "Der Build ist grün und die Migration ist durch."


@pytest.mark.parametrize("line", [
    "SELF-CHECK: prüfe alles.",
    "REGELN — load-bearing:",
    "<system-reminder>Antworte nur mit dem Vorlese-Text.</system-reminder>",
    "Antworte nur mit dem Vorlese-Text.",
    "[TASK]",
    "--append-system-prompt-file /tmp/prompt.txt",
])
def test_f2_positive_instruction_lines_are_meta(line: str) -> None:
    assert guard.is_meta_line(line)
    assert guard.sanitize_spoken(line) == ""


def test_f2_positive_instruction_line_dropped_content_kept_around_it() -> None:
    text = ("Die Migration ist abgeschlossen.\n"
            "SELF-CHECK: prüfe alles.\n"
            "Das Release folgt morgen.")
    out = guard.sanitize_spoken(text)
    assert out == "Die Migration ist abgeschlossen. Das Release folgt morgen."


def test_f2_fifty_percent_rule_drops_only_when_majority_is_scaffolding() -> None:
    # 1 of 2 lines is scaffolding (50 %, not above the threshold): the content survives.
    half = "Der Build ist grün.\nSELF-CHECK: prüfe alles."
    assert guard.sanitize_spoken(half) == "Der Build ist grün."
    # 2 of 3 lines is scaffolding (67 %): the survivor is a fragment, so nothing is spoken.
    majority = ("SELF-CHECK: prüfe alles.\n"
                "Antworte nur mit dem Vorlese-Text.\n"
                "Der Build ist grün.")
    assert guard.sanitize_spoken(majority) == ""


# -- F1: an empty guard result never speaks the raw input --------------------

def test_speak_or_announce_uses_fixed_neutral_text_not_input() -> None:
    meta = "<system-reminder>Antworte nur mit dem Vorlese-Text.</system-reminder>"
    assert guard.speak_or_announce(meta, "de") == guard.neutral_announcement("de")
    assert guard.speak_or_announce(meta, "en") == guard.neutral_announcement("en")
    assert guard.speak_or_announce(ANSWER, "de") == ANSWER
    for lang in ("de", "en"):
        ann = guard.neutral_announcement(lang)
        assert ann and "Antworte" not in ann and "Vorlese" not in ann
        assert not guard.is_meta_line(ann)


# -- E2E: real summarize.py subprocess, fake claude --------------------------

def _fake_claude(tmp: Path, mode: str) -> Path:
    bindir = tmp / "bin"
    bindir.mkdir()
    exe = bindir / "claude"
    exe.write_text(
        "#!/usr/bin/env python3\n"
        "import sys\n"
        "args = sys.argv\n"
        "sysfile = args[args.index('--append-system-prompt-file') + 1]\n"
        "prompt = open(sysfile, encoding='utf-8').read()\n"
        "sys.stdin.read()\n"
        f"mode = {mode!r}\n"
        "if mode == 'echo':\n"
        "    sys.stdout.write(prompt)\n"
        "else:\n"
        "    sys.stdout.write('Der Build ist grün, die Migration ist durch.')\n",
        encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IEXEC)
    return bindir


def _run(tmp: Path, mode: str) -> str:
    bindir = _fake_claude(tmp, mode)
    env = dict(os.environ)
    env.update({
        "PATH": f"{bindir}{os.pathsep}{env['PATH']}",
        "ANTHROPIC_API_KEY": "test-key",
        "VOICE_SUMMARIZE_BACKEND": "cli",
        "CORVIN_HOME": str(tmp / "home"),
        "CORVIN_TTS_LOCAL_ONLY": "",
    })
    env.pop("CORVIN_TTS_LOCAL_ONLY")
    proc = subprocess.run(
        [sys.executable, "-I", str(HERE / "summarize.py"), "--lang", "de",
         "--max-chars", "400"],
        input=ANSWER, capture_output=True, text=True, encoding="utf-8",
        env=env, timeout=60, check=True, cwd=str(tmp))
    return proc.stdout.strip()


def test_e2e_echoed_system_prompt_is_never_spoken(tmp_path: Path) -> None:
    out = _run(tmp_path, "echo")
    assert out, "must degrade to a spoken fallback, not go silent"
    for needle in ("Du bist", "AUFGABE", "REGELN", "load-bearing", "[TASK]",
                   "Antworte nur", "Vorlese"):
        assert needle not in out, f"instruction text leaked into speech: {needle!r}"
    assert "Build" in out  # fell back to the real answer content


def test_e2e_clean_summary_passes_through(tmp_path: Path) -> None:
    out = _run(tmp_path, "clean")
    assert out == "Der Build ist grün, die Migration ist durch."


# -- E2E: CLI unavailable (local-only), meta-only input ---------------------

META_ONLY = ("<system-reminder>Antworte nur mit dem Vorlese-Text. "
             "SELF-CHECK: prüfe alles.</system-reminder>")


def _run_without_llm(tmp: Path, stdin_text: str,
                     lang: str = "de") -> subprocess.CompletedProcess:
    """summarize.py on the degraded path: local-only tenant, no CLI call allowed."""
    (tmp / "home").mkdir()
    (tmp / "xdg").mkdir()
    env = {
        "PATH": "/usr/bin:/bin",
        "CORVIN_TTS_LOCAL_ONLY": "1",
        "VOICE_SUMMARIZE_BACKEND": "cli",
        "CORVIN_HOME": str(tmp / "home"),
        "XDG_CONFIG_HOME": str(tmp / "xdg"),
    }
    return subprocess.run(
        [sys.executable, "-I", str(HERE / "summarize.py"), "--lang", lang,
         "--max-chars", "400"],
        input=stdin_text, capture_output=True, text=True, encoding="utf-8",
        env=env, timeout=60, cwd=str(tmp))


def test_e2e_meta_only_input_on_degraded_path_never_speaks_instructions(
        tmp_path: Path) -> None:
    proc = _run_without_llm(tmp_path, META_ONLY)
    assert proc.returncode == 0, proc.stderr
    assert "degraded" in proc.stderr, "the no-LLM path must actually be taken"
    out = proc.stdout.strip()
    assert out, "must speak the neutral announcement, not go silent"
    for line in out.splitlines():
        assert not guard.is_meta_line(line), f"instruction line on stdout: {line!r}"
    for needle in ("Antworte", "Vorlese", "SELF-CHECK", "system-reminder", "prüfe"):
        assert needle not in out
    assert out == guard.neutral_announcement("de")


def test_e2e_meta_only_input_en_speaks_english_announcement_never_raw(
        tmp_path: Path) -> None:
    proc = _run_without_llm(tmp_path, META_ONLY, lang="en")
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out == "The answer is in the chat."
    for needle in ("Antworte", "Vorlese", "SELF-CHECK", "system-reminder", "prüfe"):
        assert needle not in out


def test_e2e_meta_plus_content_on_degraded_path_speaks_content_not_meta(
        tmp_path: Path) -> None:
    # Content survives the structural fallback; only the instruction line is dropped.
    stdin_text = ("Der Build ist grün und die Migration ist durch.\n"
                  "SELF-CHECK: prüfe alles.")
    proc = _run_without_llm(tmp_path, stdin_text)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert "Build" in out and "Migration" in out
    assert "SELF-CHECK" not in out and "prüfe" not in out


# -- F2 (review round 2): narrowed headers and prompt-talk in prose -----------

@pytest.mark.parametrize("sentence", [
    "NOTE — die Datei fehlt noch.",
    "WICHTIG — Wartung am Freitag ab 18 Uhr",
    "IMPORTANT — the build is green again",
    "Hinweis: Morgen ist die Wartung geplant.",
    "The rules for invoices changed in January.",
    "Our self-check passed on every platform.",
    "Please review the prompt template before Friday.",
    "The summarizer was rewritten last week.",
    "You are a helpful assistant, and that line is a good example for the talk.",
])
def test_legitimate_prose_about_prompts_and_rules_survives(sentence: str) -> None:
    assert not guard.is_meta_line(sentence)
    assert guard.sanitize_spoken(sentence) == sentence


def test_prompt_section_header_is_still_meta() -> None:
    assert guard.is_meta_line("REGELN — load-bearing:")
    assert guard.sanitize_spoken("REGELN — load-bearing:") == ""


def test_majority_of_legitimate_prose_is_never_dropped_wholesale() -> None:
    text = ("Die Regeln für Rechnungen gelten ab Januar.\n"
            "NOTE — die Datei fehlt noch.\n"
            "Our self-check passed on every platform.\n"
            "Please review the prompt template before Friday.")
    assert guard.sanitize_spoken(text) == " ".join(text.splitlines())


# -- E2E (a): degraded backend, meta-only input, raw input never on stdout -----

@pytest.mark.parametrize("meta_only", [
    "<system-reminder>Antworte nur mit dem Vorlese-Text. SELF-CHECK: prüfe alles.</system-reminder>",
    "SELF-CHECK: prüfe alles.\nREGELN — load-bearing:\n[TASK]",
    "Antworte nur mit dem Vorlese-Text.\nSELF-CHECK: prüfe alles.",
])
def test_e2e_meta_only_variants_on_degraded_path_print_only_neutral_line(
        tmp_path: Path, meta_only: str) -> None:
    proc = _run_without_llm(tmp_path, meta_only)
    assert proc.returncode == 0, proc.stderr
    out = proc.stdout.strip()
    assert out == guard.neutral_announcement("de")
    assert meta_only.strip() not in out
    for line in meta_only.splitlines():
        assert line.strip() not in out or not line.strip()
