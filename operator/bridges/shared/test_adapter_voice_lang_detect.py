#!/usr/bin/env python3
"""test_adapter_voice_lang_detect.py — per-turn de/en escape hatch for the
voice-summary output-language pin.

Bug (reported live, 2026-07-12): a Corvin instance configured with
`profile.display_language = "zh-Hans"` produced a CHINESE voice-summary
audio for a German-language text reply. Root cause: `build_voice_summary()`
read `profile.display_language` directly and, whenever it was a non-de/en
locale, unconditionally passed `--output-language <that locale>` to
`summarize.py` — which force-translates the summary via a system-prompt
directive explicitly engineered (per `i18n.language_directive()`'s own
docstring) to override even a "match the user's actual language" rule.
There was no per-turn signal anywhere in this pipeline to say "the text
being spoken right now is already de/en, don't force-translate it."

Fix: `_detect_confident_de_en()` (a thin wrapper around the existing
`operator/voice/scripts/detect_lang.py` function-word heuristic) plus
`_resolve_voice_output_language()`, which only lets a confident de/en
detection override the static profile pin — ambiguous/non-Latin-script
text still falls through to the profile default unchanged, so a genuine
zh-Hans/ja/ar user's preference is untouched.

Tests use the same fake-summarizer-argv-dump harness as
test_adapter_voice_audience.py (real subprocess pipeline, deterministic).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _install_fake_summarizer(tmp: Path) -> tuple[Path, Path]:
    scripts_dir = tmp / "scripts"
    scripts_dir.mkdir()
    argv_dump = tmp / "summarizer_argv.json"

    fake_summarize = scripts_dir / "summarize.py"
    fake_summarize.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys, os\n"
        "argv = sys.argv[1:]\n"
        "if '--appendix-mode' not in argv and '--metapher-mode' not in argv:\n"
        f"    open({json.dumps(str(argv_dump))}, 'w').write(json.dumps(argv))\n"
        "print('FAKE_SUMMARY_OUTPUT')\n"
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


def _fresh_adapter_with_scripts_dir(scripts_dir: Path):
    import adapter  # type: ignore
    adapter.SCRIPTS_DIR = scripts_dir
    return adapter


def _adapter_with_profile_lang(tmp_path: Path, display_language: str):
    """Isolated profile dir + freshly-imported adapter with a fake
    summarizer, profile.display_language pre-set. Returns (adapter, argv_dump)."""
    profile_dir = tmp_path / "voice-config"
    profile_dir.mkdir()
    os.environ["XDG_CONFIG_HOME"] = str(profile_dir)
    for m in ("profile", "adapter"):
        sys.modules.pop(m, None)

    scripts_dir, argv_dump = _install_fake_summarizer(tmp_path)
    adapter = _fresh_adapter_with_scripts_dir(scripts_dir)
    assert adapter._voice_profile is not None, (
        "profile module failed to import — fix the optional-import path"
    )
    adapter._voice_profile.set_value("display_language", display_language)
    return adapter, argv_dump


# ── _detect_confident_de_en: unit-level ─────────────────────────────────

def test_detect_confident_de_en_recognizes_german() -> None:
    for m in ("adapter",):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    text = "Das ist ein Text auf Deutsch, und ich habe mich gerade selbst durchgecheckt."
    assert adapter._detect_confident_de_en(text) == "de"


def test_detect_confident_de_en_recognizes_english() -> None:
    for m in ("adapter",):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    text = "This is a text in English and it should be detected as such."
    assert adapter._detect_confident_de_en(text) == "en"


def test_detect_confident_de_en_returns_none_for_chinese_script() -> None:
    """Non-Latin script text has zero de/en function-word hits — must NOT
    be misclassified as de or en; the profile default should still apply."""
    for m in ("adapter",):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    text = "你好，这是一段中文文本，用于测试语言检测。"
    assert adapter._detect_confident_de_en(text) is None


def test_detect_confident_de_en_returns_none_for_empty_text() -> None:
    for m in ("adapter",):
        sys.modules.pop(m, None)
    import adapter  # type: ignore
    assert adapter._detect_confident_de_en("") is None


# ── _resolve_voice_output_language: unit-level ──────────────────────────

def test_resolve_output_language_de_profile_stays_de(tmp_path: Path) -> None:
    """User preference de → always de."""
    adapter, _ = _adapter_with_profile_lang(tmp_path, "de")
    assert adapter._resolve_voice_output_language("Ein deutscher Text.") == "de"
    # German preference is authoritative regardless of text
    assert adapter._resolve_voice_output_language("The installation is complete.") == "de"
    assert adapter._resolve_voice_output_language("你好") == "de"


def test_resolve_output_language_zh_profile_stays_zh_regardless_of_text(
    tmp_path: Path,
) -> None:
    """User preference zh-Hans → always zh-Hans, even if text is German/English."""
    adapter, _ = _adapter_with_profile_lang(tmp_path, "zh-Hans")
    # German text → but user said Chinese
    resolved = adapter._resolve_voice_output_language(
        "Deine Installation ist fertig, und ich habe mich gerade selbst durchgecheckt."
    )
    assert resolved == "zh-Hans", f"User preference must be respected, got {resolved!r}"
    # English text → but user said Chinese
    resolved = adapter._resolve_voice_output_language(
        "The installation is complete and everything checked out fine."
    )
    assert resolved == "zh-Hans"
    # Chinese text → matches preference
    resolved = adapter._resolve_voice_output_language("你好，我是 Corvin。")
    assert resolved == "zh-Hans"


def test_resolve_output_language_en_profile_stays_en(
    tmp_path: Path,
) -> None:
    """User preference en → always en."""
    adapter, _ = _adapter_with_profile_lang(tmp_path, "en")
    assert adapter._resolve_voice_output_language("German text here.") == "en"
    assert adapter._resolve_voice_output_language("Das ist Deutsch.") == "en"


# ── build_voice_summary: end-to-end via the real subprocess pipeline ────

def test_build_voice_summary_de_profile_sets_de_flag(
    tmp_path: Path,
) -> None:
    """User preference de → summarize.py is invoked with --lang de (user's choice)."""
    adapter, argv_dump = _adapter_with_profile_lang(tmp_path, "de")

    long_text = (
        "Ein sehr langer deutscher Text für die Zusammenfassung. " * 20
    )
    result = adapter.build_voice_summary(long_text, max_chars=400)
    assert result, "build_voice_summary returned empty"

    argv = json.loads(argv_dump.read_text())
    assert "--lang" in argv
    idx = argv.index("--lang")
    assert argv[idx + 1] == "de", "User's de preference should be respected"


def test_build_voice_summary_zh_profile_sets_zh_flag_regardless_of_text(
    tmp_path: Path,
) -> None:
    """User preference zh-Hans → summarize.py is invoked with --output-language
    zh-Hans regardless of what the text language is. User knows best."""
    adapter, argv_dump = _adapter_with_profile_lang(tmp_path, "zh-Hans")

    long_german_text = (
        "Die Installation ist jetzt vollstaendig abgeschlossen und alle "
        "Systeme wurden erfolgreich ueberprueft. " * 20
    )
    result = adapter.build_voice_summary(long_german_text, max_chars=400)
    assert result, "build_voice_summary returned empty"

    argv = json.loads(argv_dump.read_text())
    assert "--output-language" in argv, (
        f"User preference zh-Hans must set --output-language flag: argv={argv}"
    )
    idx = argv.index("--output-language")
    assert argv[idx + 1] == "zh-Hans", "User's zh-Hans preference is authoritative"
