"""Layer-12 appendix-mode E2E — closes the LERN-ZUGABE bypass.

Covers:
  L12.app/1  summarize.py --appendix-mode echoes input verbatim + suffixes
             a marker-prefixed annex (de + en).
  L12.app/2  Appendix fail-safe — when claude CLI is absent AND no
             ANTHROPIC_API_KEY, summarize.py prints stdin verbatim
             (no crash, no silence).
  L12.app/3  _audience_demands_appendix detects LERN-ZUGABE / LEARNING
             ANNEX in a rendered audience block; ignores other blocks.
  L12.app/4  _extract_appendix accepts marker-prefixed strings, rejects
             marker-less output, tolerates leading prose.
  L12.app/5  build_voice_summary override-path: with audience demanding
             appendix, the override comes back as override + annex
             (not just verbatim).
  L12.app/6  build_voice_summary short-text direct path: same fix
             applies; the LERN-ZUGABE suffix lands behind a short reply.
  L12.app/7  Faithfulness regression: the input text appears in the
             output BYTE-IDENTICAL (modulo trailing space). The appendix
             only adds a suffix; nothing in the input is rewritten.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT.parent.parent / "voice" / "scripts"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _section(label: str) -> None:
    print(f"\n[{label}]")


# Fake claude binary that prints a canned appendix to stdout.
_FAKE_CLAUDE_DE = """\
#!/usr/bin/env python3
import sys
print("Und zur Einordnung, dieses Konzept heisst Strangler-Fig. "
      "Es bedeutet, alten Pfad parallel zum neuen leben zu lassen, "
      "bis der neue trägt. Merksatz: parallel, nicht ersetzen.")
"""

_FAKE_CLAUDE_EN = """\
#!/usr/bin/env python3
import sys
print("For context, this concept is called dependency injection. "
      "Wiring lives outside the consumer, so swaps stay cheap. "
      "Memory aid: wire it in, do not bake it in.")
"""

_FAKE_CLAUDE_NO_MARKER = """\
#!/usr/bin/env python3
import sys
print("This is just a regular reply without any teaching annex marker.")
"""

_FAKE_CLAUDE_SILENT = """\
#!/usr/bin/env python3
import sys
print("")
"""


def _install_fake_claude(body: str, dest: Path) -> Path:
    """Drop a fake claude binary into *dest* and make it executable."""
    fake = dest / "claude"
    fake.write_text(body)
    fake.chmod(0o755)
    return fake


def _run_summarize(stdin: str, *args: str, fake_path: Path | None = None,
                   timeout: float = 30.0) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    # Make sure we don't accidentally call a real claude — the test
    # owns the PATH. Also strip API key so the SDK fallback can't fire.
    if fake_path is not None:
        env["PATH"] = f"{fake_path.parent}:{env.get('PATH', '')}"
    else:
        env["PATH"] = "/usr/bin:/bin"   # minimal — no claude available
    env.pop("ANTHROPIC_API_KEY", None)
    env["VOICE_HOOK_RECURSION"] = "1"
    return subprocess.run(
        ["python3", str(SCRIPTS / "summarize.py"), *args],
        input=stdin, capture_output=True, text=True,
        env=env, timeout=timeout, check=False,
    )


# ─────────────────────────────────────────────────────────────────────────
def case_appendix_mode_de_with_fake_claude() -> None:
    _section("L12.app/1: --appendix-mode echoes input + adds annex (DE)")
    tmp = Path(tempfile.mkdtemp(prefix="app-1-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_DE, tmp)
        original = "Der Voice-Pfad funktioniert jetzt."
        out = _run_summarize(original, "--lang", "de", "--appendix-mode",
                             fake_path=fake)
        assert out.returncode == 0, out.stderr
        body = out.stdout.strip()
        # Input must remain byte-identical (modulo trailing space)
        assert body.startswith(original), f"input lost: {body!r}"
        # Annex marker present
        assert "Und zur Einordnung," in body, body
        # Concept hint actually appended
        assert "Strangler-Fig" in body, body
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_appendix_mode_en_with_fake_claude() -> None:
    _section("L12.app/1b: --appendix-mode (EN) uses 'For context,' marker")
    tmp = Path(tempfile.mkdtemp(prefix="app-1b-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_EN, tmp)
        original = "Voice path is fixed."
        out = _run_summarize(original, "--lang", "en", "--appendix-mode",
                             fake_path=fake)
        assert out.returncode == 0, out.stderr
        body = out.stdout.strip()
        assert body.startswith(original), body
        assert "For context," in body, body
        assert "dependency injection" in body, body
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_appendix_fail_safe_no_llm() -> None:
    _section("L12.app/2: no claude + no API key → verbatim, no crash")
    original = "Eine Test-Nachricht ohne LLM-Zugang."
    out = _run_summarize(original, "--lang", "de", "--appendix-mode",
                         fake_path=None)
    assert out.returncode == 0, out.stderr
    body = out.stdout.strip()
    # No annex possible, but the input is preserved
    assert body == original, body
    # No "Und zur Einordnung" because no LLM ran
    assert "Und zur Einordnung," not in body
    print("  passed")


def case_appendix_marker_rejection() -> None:
    _section("L12.app/2b: LLM output without marker → fall back to verbatim")
    tmp = Path(tempfile.mkdtemp(prefix="app-2b-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_NO_MARKER, tmp)
        original = "Original-Text bleibt unangetastet."
        out = _run_summarize(original, "--lang", "de", "--appendix-mode",
                             fake_path=fake)
        body = out.stdout.strip()
        # No marker → _extract_appendix returns "" → caller returns verbatim
        assert body == original, body
        assert "regular reply" not in body
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_appendix_empty_llm_output() -> None:
    _section("L12.app/2c: LLM returns empty → verbatim")
    tmp = Path(tempfile.mkdtemp(prefix="app-2c-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_SILENT, tmp)
        original = "Hallo."
        out = _run_summarize(original, "--lang", "de", "--appendix-mode",
                             fake_path=fake)
        body = out.stdout.strip()
        assert body == original, body
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_audience_demands_appendix() -> None:
    _section("L12.app/3: _audience_demands_appendix detection")
    import adapter as A  # type: ignore
    # Profile-rendered block carries LERN-ZUGABE
    de_block = "HÖRER-PROFIL — Profil: Lern-Modus 3/3. LERN-ZUGABE (PFLICHT, ..."
    assert A._audience_demands_appendix(de_block) is True
    # Empty block → False
    assert A._audience_demands_appendix("") is False
    # Block without marker → False
    plain_block = "AUDIENCE — Profil: Lern-Modus 0/3. Keine Lehr-Zugabe."
    assert A._audience_demands_appendix(plain_block) is False
    # English variant
    en_block = "AUDIENCE — LEARNING ANNEX (MANDATORY) — append a teaching..."
    assert A._audience_demands_appendix(en_block) is True
    print("  passed")


def case_extract_appendix() -> None:
    _section("L12.app/4: _extract_appendix marker discipline")
    sys.modules.pop("summarize", None)
    sys.path.insert(0, str(SCRIPTS))
    import summarize as S  # type: ignore
    # Bare marker — passes through
    assert S._extract_appendix("Und zur Einordnung, foo.").startswith(
        "Und zur Einordnung,"
    )
    # Leading prose stripped — marker found mid-string
    raw = "Hier ist die Antwort: Und zur Einordnung, foo."
    out = S._extract_appendix(raw)
    assert out.startswith("Und zur Einordnung,"), out
    # No marker → rejected
    assert S._extract_appendix("Just prose, no marker") == ""
    # Empty input → empty
    assert S._extract_appendix("") == ""
    # English markers also work
    assert S._extract_appendix("For context, foo.").startswith("For context,")
    assert S._extract_appendix("Worth knowing, foo.").startswith("Worth knowing,")
    print("  passed")


def case_build_voice_summary_override_with_appendix() -> None:
    _section("L12.app/5: build_voice_summary override path gets appendix")
    tmp = Path(tempfile.mkdtemp(prefix="app-5-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_DE, tmp)
        # Patch the audience-resolver to return a LERN-ZUGABE block so
        # _audience_demands_appendix fires. We don't touch the real
        # profile.json — the test owns its own state.
        os.environ["PATH"] = f"{tmp}:{os.environ['PATH']}"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        sys.modules.pop("adapter", None)
        import adapter as A  # type: ignore
        original_resolver = A._resolve_audience_block
        try:
            A._resolve_audience_block = lambda: (
                "HÖRER-PROFIL — Lern-Modus 3/3. LERN-ZUGABE.", "de"
            )
            override_text = "Override-Text, der nicht verändert werden darf."
            result = A.build_voice_summary("ignored chat text",
                                            override=override_text)
            assert override_text.replace(",", "") in result.replace(",", ""), result
            # markdown stripper turns punctuation into normalised form;
            # core marker must appear
            assert "Und zur Einordnung," in result, result
            assert "Strangler-Fig" in result, result
        finally:
            A._resolve_audience_block = original_resolver
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_build_voice_summary_short_text_with_appendix() -> None:
    _section("L12.app/6: build_voice_summary short-text path gets appendix")
    tmp = Path(tempfile.mkdtemp(prefix="app-6-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_DE, tmp)
        os.environ["PATH"] = f"{tmp}:{os.environ['PATH']}"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        sys.modules.pop("adapter", None)
        import adapter as A  # type: ignore
        original_resolver = A._resolve_audience_block
        try:
            A._resolve_audience_block = lambda: (
                "HÖRER-PROFIL — Lern-Modus 3/3. LERN-ZUGABE.", "de"
            )
            short_text = "Kurze Reply, unter max_chars."
            result = A.build_voice_summary(short_text, max_chars=400)
            assert short_text in result, result
            assert "Und zur Einordnung," in result, result
        finally:
            A._resolve_audience_block = original_resolver
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# A fake claude that branches on the system prompt: the main summarize call
# gets a marker-LESS summary; the appendix and metapher backfill calls each
# return their own marker. This lets us prove the long-text path backfills BOTH.
_FAKE_CLAUDE_SMART = """\
#!/usr/bin/env python3
import sys
argv = " ".join(sys.argv)
if "Metapher-Generator" in argv:
    print("Als Bild gesprochen, es ist wie ein Notizzettel am Kuehlschrank.")
elif "AUSSCHLIESSLICH eine kurze Lehr" in argv:
    print("Und zur Einordnung, ein Cache ist ein Zwischenspeicher fuer teure Ergebnisse.")
else:
    print("Kurz gesagt: der Dienst speichert wiederholte Ergebnisse zwischen "
          "und antwortet dadurch spuerbar schneller.")
"""


def case_build_voice_summary_main_path_gets_both() -> None:
    # Regression: the LONG-TEXT (main summarize) path used to backfill ONLY the
    # metapher when the LLM summarizer skipped the --audience instructions, so
    # the LERN-ZUGABE silently vanished ("Metaphern da, Learning fehlt"). With
    # the symmetric backfill, the main path must restore BOTH markers.
    _section("L12.app/8: build_voice_summary MAIN path backfills LERN-ZUGABE + Metapher")
    tmp = Path(tempfile.mkdtemp(prefix="app-8-"))
    try:
        _install_fake_claude(_FAKE_CLAUDE_SMART, tmp)
        os.environ["PATH"] = f"{tmp}:{os.environ['PATH']}"
        os.environ.pop("ANTHROPIC_API_KEY", None)
        sys.modules.pop("adapter", None)
        import adapter as A  # type: ignore
        original_resolver = A._resolve_audience_block
        try:
            # Audience demands BOTH learning and metaphor.
            A._resolve_audience_block = lambda: (
                "HÖRER-PROFIL — Lern-Modus 3/3; Analogien erwünscht. "
                "LERN-ZUGABE (PFLICHT). METAPHER-ZUGABE (PFLICHT).", "de"
            )
            long_text = (
                "Der neue Antwort-Cache im Voice-Dienst speichert die Ergebnisse "
                "wiederkehrender Anfragen, sodass identische Fragen nicht erneut "
                "an das Modell gehen. Das senkt die Latenz deutlich und spart "
                "Tokens, weil teure Berechnungen nur einmal anfallen. Der Cache "
                "wird nach fünf Minuten oder bei geänderten Eingaben invalidiert, "
                "damit niemals veraltete Antworten ausgeliefert werden."
            )
            assert len(long_text) > 200, "test text must exceed max_chars to hit main path"
            result = A.build_voice_summary(long_text, max_chars=200)
            assert "Und zur Einordnung," in result, f"LERN-ZUGABE missing: {result!r}"
            assert "Als Bild gesprochen," in result, f"Metapher missing: {result!r}"
        finally:
            A._resolve_audience_block = original_resolver
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def case_has_lern_zugabe_suffix() -> None:
    _section("L12.app/9: _has_lern_zugabe_suffix marker detection")
    sys.modules.pop("adapter", None)
    import adapter as A  # type: ignore
    assert A._has_lern_zugabe_suffix("foo. Und zur Einordnung, bar.") is True
    assert A._has_lern_zugabe_suffix("foo. Worth knowing, bar.") is True
    assert A._has_lern_zugabe_suffix("just a plain summary, no annex.") is False
    print("  passed")


def case_byte_identical_input_preserved() -> None:
    _section("L12.app/7: faithfulness — input bytes appear unchanged")
    tmp = Path(tempfile.mkdtemp(prefix="app-7-"))
    try:
        fake = _install_fake_claude(_FAKE_CLAUDE_DE, tmp)
        original = (
            "Diese Original-Botschaft enthält wichtige Details: A, B und C. "
            "Sie darf in keiner Weise umformuliert werden."
        )
        out = _run_summarize(original, "--lang", "de", "--appendix-mode",
                             fake_path=fake)
        body = out.stdout.strip()
        # Locate input as substring — exact bytes must be present
        assert original in body, (
            f"original lost or paraphrased.\n"
            f"  expected: {original!r}\n"
            f"  got:      {body!r}"
        )
        # And the annex follows after a single space
        annex_start = body.find(original) + len(original)
        suffix = body[annex_start:].lstrip()
        assert suffix.startswith("Und zur Einordnung,"), body
        print("  passed")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    case_appendix_mode_de_with_fake_claude()
    case_appendix_mode_en_with_fake_claude()
    case_appendix_fail_safe_no_llm()
    case_appendix_marker_rejection()
    case_appendix_empty_llm_output()
    case_audience_demands_appendix()
    case_extract_appendix()
    case_build_voice_summary_override_with_appendix()
    case_build_voice_summary_short_text_with_appendix()
    case_build_voice_summary_main_path_gets_both()
    case_has_lern_zugabe_suffix()
    case_byte_identical_input_preserved()
    print("\nAll L12.app cases passed.")
