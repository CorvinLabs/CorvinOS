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
    adapter, _ = _adapter_with_profile_lang(tmp_path, "de")
    assert adapter._resolve_voice_output_language("Ein deutscher Text.") == "de"


def test_resolve_output_language_zh_profile_with_german_text_falls_back_to_de(
    tmp_path: Path,
) -> None:
    """The exact reported bug: zh-Hans profile default + a confidently
    German turn must resolve to "de", not "zh-Hans"."""
    adapter, _ = _adapter_with_profile_lang(tmp_path, "zh-Hans")
    resolved = adapter._resolve_voice_output_language(
        "Deine Installation ist fertig, und ich habe mich gerade selbst durchgecheckt."
    )
    assert resolved == "de", f"expected the confident German detection to win, got {resolved!r}"


def test_resolve_output_language_zh_profile_with_chinese_text_stays_zh(
    tmp_path: Path,
) -> None:
    """Regression guard: a genuine zh-Hans user's preference must NOT be
    broken by the de/en escape hatch — ambiguous/non-Latin text falls
    through to the static profile default unchanged."""
    adapter, _ = _adapter_with_profile_lang(tmp_path, "zh-Hans")
    resolved = adapter._resolve_voice_output_language("你好，我是 Corvin。")
    assert resolved == "zh-Hans"


def test_resolve_output_language_zh_profile_with_english_text_resolves_en(
    tmp_path: Path,
) -> None:
    adapter, _ = _adapter_with_profile_lang(tmp_path, "zh-Hans")
    resolved = adapter._resolve_voice_output_language(
        "The installation is complete and everything checked out fine."
    )
    assert resolved == "en"


# ── build_voice_summary: end-to-end via the real subprocess pipeline ────

def test_build_voice_summary_zh_profile_german_text_omits_output_language_flag(
    tmp_path: Path,
) -> None:
    """End-to-end reproduction of the bug: with profile.display_language
    = zh-Hans and a long GERMAN reply, summarize.py must NOT be invoked
    with --output-language zh-Hans (which force-translates the summary)."""
    adapter, argv_dump = _adapter_with_profile_lang(tmp_path, "zh-Hans")

    long_german_text = (
        "Die Installation ist jetzt vollstaendig abgeschlossen und alle "
        "Systeme wurden erfolgreich ueberprueft. " * 20
    )  # well above the default 400-char summarizer threshold
    result = adapter.build_voice_summary(long_german_text, max_chars=400)
    assert result, "build_voice_summary returned empty"

    argv = json.loads(argv_dump.read_text())
    assert "--output-language" not in argv, (
        f"German text must not be force-translated to the zh-Hans profile "
        f"default: argv={argv}"
    )


def test_build_voice_summary_zh_profile_chinese_text_keeps_output_language_flag(
    tmp_path: Path,
) -> None:
    """Regression guard: genuinely non-Latin-script input still gets the
    profile's output-language pin — this is the actual feature the pin
    exists for and must keep working."""
    adapter, argv_dump = _adapter_with_profile_lang(tmp_path, "zh-Hans")

    long_chinese_text = "你好，这是一段很长的中文文本，用于测试语言检测和摘要功能是否正常工作。" * 10
    # CJK text is far denser per character than Latin text — 10x this
    # string is only ~350 chars, under the default 400-char threshold, so
    # a smaller max_chars is needed here to force the summarizer branch
    # (the short-text passthrough path never invokes summarize.py at all).
    result = adapter.build_voice_summary(long_chinese_text, max_chars=100)
    assert result, "build_voice_summary returned empty"

    argv = json.loads(argv_dump.read_text())
    assert "--output-language" in argv, f"expected the zh-Hans pin to survive: argv={argv}"
    idx = argv.index("--output-language")
    assert argv[idx + 1] == "zh-Hans"
