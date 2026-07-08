"""Regression tests for corvinOS/installer/steps/piper.py (ADR-0185 M2/M3).

Before this fix, Piper's installer step had two real gaps that made it a
permanently-skipped fallback tier for most users, even though piper-tts is
now an unconditional base dependency (pyproject.toml) with genuine
win32/macOS/Linux wheels:

  1. `_install_piper()` unconditionally returned early on Windows (stale
     "no Windows wheel for Python 3.10+" assumption) and unconditionally
     returned early in non-interactive installs, before ever attempting a
     pip install / PATH check — even though piper-tts is no longer optional.
  2. `_setup_model()`'s non-interactive branch hardcoded the ENGLISH voice
     model regardless of the user's detected system language, even though
     `_detect_language()` right above it already correctly resolves e.g.
     "de" from LANG=de_DE.UTF-8 — a German-locale user running
     `corvin-install` non-interactively got a silent English voice.

No test file existed for this module before this change (confirmed via
repo-wide search) — this is a new regression suite, not a rewrite of an
existing one.
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import piper as piper_mod


# ── _setup_model: non-interactive language selection ────────────────────────

def test_setup_model_non_interactive_uses_detected_language(tmp_path: Path) -> None:
    """A German-locale, non-interactive install must download the German
    model — not silently fall back to English."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()
    calls: list[tuple[str, str]] = []

    def fake_download(lang: str, rel_path: str, model_dir: Path, config_file: Path) -> None:
        calls.append((lang, rel_path))

    with mock.patch.object(piper_mod, "_detect_language", return_value="de"), \
         mock.patch.object(piper_mod, "_download_model", side_effect=fake_download):
        piper_mod._setup_model(voice_config_dir, interactive=False)

    assert calls == [("de", piper_mod._MODELS["de"][1])], (
        f"expected the German model to be downloaded for a de-locale "
        f"non-interactive install, got {calls!r}"
    )


def test_setup_model_non_interactive_uses_english_when_locale_unrecognized(tmp_path: Path) -> None:
    """Sanity/negative control: an unrecognized or English locale still
    resolves to the English model (no crash, no KeyError)."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()
    calls: list[tuple[str, str]] = []

    def fake_download(lang: str, rel_path: str, model_dir: Path, config_file: Path) -> None:
        calls.append((lang, rel_path))

    with mock.patch.object(piper_mod, "_detect_language", return_value="en"), \
         mock.patch.object(piper_mod, "_download_model", side_effect=fake_download):
        piper_mod._setup_model(voice_config_dir, interactive=False)

    assert calls == [("en", piper_mod._MODELS["en"][1])]


def test_setup_model_skips_download_when_a_model_already_exists(tmp_path: Path) -> None:
    """If a model is already configured and present on disk, no fresh
    download should be attempted regardless of language/interactivity."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()
    model_file = tmp_path / "existing-model.onnx"
    model_file.write_bytes(b"fake-onnx-bytes")
    config_file = voice_config_dir / "config.json"
    config_file.write_text(f'{{"piper_model_de": "{model_file}"}}')

    with mock.patch.object(piper_mod, "_download_model") as m_download:
        piper_mod._setup_model(voice_config_dir, interactive=False)

    m_download.assert_not_called()


# ── _install_piper: no longer opt-in / no longer Windows-gated ──────────────

def test_install_piper_attempts_pip_install_on_windows_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before this fix, `_install_piper` returned immediately on
    sys.platform == "win32" without ever trying pip — a stale assumption
    now that piper-tts ships genuine win32 wheels (cp39-abi3). It must now
    behave the same on Windows as everywhere else: try pip if missing."""
    monkeypatch.setattr(piper_mod.sys, "platform", "win32")
    monkeypatch.setattr(piper_mod, "shutil", mock.Mock(which=mock.Mock(return_value=None)))
    monkeypatch.setattr(piper_mod.os, "environ", {"APPDATA": str(Path("/tmp/appdata"))})
    with mock.patch.object(piper_mod, "_pip_install", return_value=True) as m_pip:
        piper_mod._install_piper(interactive=False)

    m_pip.assert_called_once_with("piper-tts")


def test_install_piper_attempts_pip_install_when_non_interactive(monkeypatch: pytest.MonkeyPatch) -> None:
    """Before this fix, `_install_piper` returned immediately whenever
    `interactive=False` (e.g. scripted/CI/non-terminal installs), skipping
    the pip install and the y/n prompt entirely — the correct fix removes
    the y/n gate (piper-tts is a base dependency, not opt-in) rather than
    special-casing non-interactive mode."""
    monkeypatch.setattr(piper_mod, "shutil", mock.Mock(which=mock.Mock(return_value=None)))
    with mock.patch.object(piper_mod, "_pip_install", return_value=True) as m_pip:
        piper_mod._install_piper(interactive=False)

    m_pip.assert_called_once_with("piper-tts")


def test_install_piper_is_a_noop_when_binary_already_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """When `piper` is already resolvable (the common case now that it's a
    base dependency), no pip install should be attempted at all."""
    monkeypatch.setattr(piper_mod, "shutil", mock.Mock(which=mock.Mock(return_value="/usr/bin/piper")))
    with mock.patch.object(piper_mod, "_pip_install") as m_pip:
        piper_mod._install_piper(interactive=False)

    m_pip.assert_not_called()


# ── ensure_piper: end-to-end orchestration ──────────────────────────────────

def test_ensure_piper_downloads_model_unconditionally_when_binary_present(tmp_path: Path) -> None:
    """The overall entry point: once the binary is available, the model
    download and service.env wiring must happen regardless of
    `interactive` — this is what makes Piper a genuine zero-config
    fallback instead of a tier nobody ever provisions."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    with mock.patch.object(piper_mod, "_install_piper") as m_install, \
         mock.patch.object(piper_mod, "_piper_binary", return_value="/usr/bin/piper"), \
         mock.patch.object(piper_mod, "_setup_model") as m_setup, \
         mock.patch.object(piper_mod, "_write_bin_env") as m_write_env:
        piper_mod.ensure_piper(voice_config_dir, interactive=False)

    m_install.assert_called_once_with(False)
    m_setup.assert_called_once_with(voice_config_dir, False)
    m_write_env.assert_called_once_with(voice_config_dir, "/usr/bin/piper")


def test_ensure_piper_downloads_model_when_only_python_package_present(tmp_path: Path) -> None:
    """INST-3/VOICE-4: a `uv tool install` never exposes the piper console
    script on PATH, but the runtime TTS path uses the piper PYTHON API. When
    the package imports (even with NO binary), the model must still download —
    only PIPER_BIN/service.env wiring is skipped."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    with mock.patch.object(piper_mod, "_install_piper"), \
         mock.patch.object(piper_mod, "_piper_binary", return_value=None), \
         mock.patch.object(piper_mod, "_piper_python_available", return_value=True), \
         mock.patch.object(piper_mod, "_setup_model") as m_setup, \
         mock.patch.object(piper_mod, "_write_bin_env") as m_write_env:
        piper_mod.ensure_piper(voice_config_dir, interactive=False)

    m_setup.assert_called_once_with(voice_config_dir, False)
    m_write_env.assert_not_called()


def test_ensure_piper_skips_model_when_neither_binary_nor_package(tmp_path: Path) -> None:
    """Negative control: with neither the binary nor the Python package
    available, there is nothing to run against — skip the model download."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    with mock.patch.object(piper_mod, "_install_piper"), \
         mock.patch.object(piper_mod, "_piper_binary", return_value=None), \
         mock.patch.object(piper_mod, "_piper_python_available", return_value=False), \
         mock.patch.object(piper_mod, "_setup_model") as m_setup, \
         mock.patch.object(piper_mod, "_write_bin_env") as m_write_env:
        piper_mod.ensure_piper(voice_config_dir, interactive=False)

    m_setup.assert_not_called()
    m_write_env.assert_not_called()


# ── Cross-file consistency: installer output must resolve via say.py ───────
#
# ADR-0185 review finding (CRITICAL, confirmed): the installer's `_MODELS`
# stem table and say.py's OWN separate `_PIPER_MODELS` table used DIFFERENT
# filenames for 8 of 12 languages (including de/en) — corvin-install would
# download a real model and report success, but say.py (the code the
# Console's `/voice/tts` endpoint and Discord/WhatsApp welcome messages
# actually spawn) could never find it at runtime, silently falling through
# to text-only. Fixed by making say.py read config.json (the installer's own
# SSOT) first. This test exercises the REAL `_setup_model`/`_download_model`/
# `_save_model_config` pipeline (only the network fetch itself is faked) and
# then feeds its real output into say.py's real, unmocked lookup — this is
# the one test that would have caught the original bug.

def _fake_fetch_writes_placeholder(url: str, dest: Path, *, silent: bool = False) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(b"fake-model-bytes")
    return True


@pytest.mark.parametrize("lang", sorted(piper_mod._MODELS.keys()))
def test_installer_output_resolves_via_say_py_for_every_language(
    tmp_path: Path, lang: str,
) -> None:
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    with mock.patch.object(piper_mod, "_fetch", side_effect=_fake_fetch_writes_placeholder):
        piper_mod._setup_model(voice_config_dir, interactive=False)
        # _setup_model's non-interactive branch downloads the DETECTED
        # system language, not necessarily the parametrized one — call
        # _download_model directly for the language under test too, so
        # every language in the table gets its own real installer-shaped
        # config.json entry to verify against.
        _, rel_path = piper_mod._MODELS[lang]
        piper_mod._download_model(lang, rel_path, voice_config_dir / "piper-models", voice_config_dir / "config.json")

    saved_voice_config_dir = os.environ.get("VOICE_CONFIG_DIR")
    os.environ["VOICE_CONFIG_DIR"] = str(voice_config_dir)
    scripts_dir = Path(__file__).resolve().parent.parent / "operator" / "voice" / "scripts"
    sys.path.insert(0, str(scripts_dir))
    try:
        import say as say_mod  # noqa: PLC0415
        importlib.reload(say_mod)  # VOICE_CONFIG_DIR is cached at import time

        resolved = say_mod._piper_model_for(lang)
        assert resolved is not None, (
            f"say.py could not find a Piper model for {lang!r} after "
            f"corvin-install downloaded one — this is exactly the "
            f"filename-mismatch regression this test guards against"
        )
        assert resolved.exists() and resolved.stat().st_size > 0
    finally:
        # Restore the env var BEFORE reloading `say` again (round-3 review
        # finding): `importlib.reload()` mutates the single shared
        # `sys.modules["say"]` object, which otherwise keeps pointing at this
        # test's now-deleted tmp_path dir for the rest of the pytest process
        # — mirrors the tearDown convention in the sibling
        # test_say_provider_status.py, which hits the exact same hazard.
        if saved_voice_config_dir is None:
            os.environ.pop("VOICE_CONFIG_DIR", None)
        else:
            os.environ["VOICE_CONFIG_DIR"] = saved_voice_config_dir
        if "say" in sys.modules:
            importlib.reload(sys.modules["say"])
        sys.path.remove(str(scripts_dir))
