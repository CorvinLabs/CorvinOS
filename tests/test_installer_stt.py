"""Regression tests for corvinOS/installer/steps/stt.py (ADR-0185 M1).

``pywhispercpp`` replaces ``faster-whisper`` as the canonical, cross-platform
local STT engine — no more ``sys.platform == "win32"`` skip, and the model
download step (mirroring installer/steps/piper.py's model-fetch pattern) is
new work: there was no STT model provisioning step before this change at
all (faster-whisper relied on huggingface_hub's implicit lazy cache).

These tests are hermetic — the real network-backed round trip (actually
downloading the GGML model and transcribing real audio through
``pywhispercpp``) lives in ``operator/voice/scripts/test_stt.py``'s
``LocalWhisperPywhispercppTests``, not here.
"""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from corvinOS.installer.steps import stt as stt_mod


# ── ensure_stt: package presence / install ──────────────────────────────────


def test_ensure_stt_skips_pip_install_when_pywhispercpp_already_importable(tmp_path: Path) -> None:
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    with mock.patch.object(stt_mod, "_pip_install") as m_pip, \
         mock.patch.object(stt_mod, "_download_whisper_model") as m_download:
        stt_mod.ensure_stt(voice_config_dir, interactive=False)

    m_pip.assert_not_called()
    m_download.assert_called_once_with(voice_config_dir, stt_mod._DEFAULT_MODEL)


def test_ensure_stt_attempts_pip_install_when_pywhispercpp_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Before this fix, Windows unconditionally skipped local STT entirely.
    Now every platform (including Windows) attempts the same pip install —
    there is no more platform branch in ensure_stt() at all."""
    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    import builtins
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pywhispercpp":
            raise ImportError("simulated: pywhispercpp not installed")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", side_effect=fake_import), \
         mock.patch.object(stt_mod, "_pip_install", return_value=False) as m_pip, \
         mock.patch.object(stt_mod, "_download_whisper_model") as m_download:
        stt_mod.ensure_stt(voice_config_dir, interactive=False)

    m_pip.assert_called_once_with("pywhispercpp")
    # Install failed (mocked False) and the module still isn't importable
    # (still patched) — the step must give up gracefully without ever
    # reaching the model download.
    m_download.assert_not_called()


# ── _download_whisper_model ──────────────────────────────────────────────────


def test_download_whisper_model_skips_when_file_already_present(tmp_path: Path) -> None:
    voice_config_dir = tmp_path / "voice_config"
    model_dir = voice_config_dir / "whisper-models"
    model_dir.mkdir(parents=True)
    dest = model_dir / "ggml-tiny-q5_1.bin"
    dest.write_bytes(b"fake-ggml-bytes")

    with mock.patch("pywhispercpp.utils.download_model") as m_download:
        ok = stt_mod._download_whisper_model(voice_config_dir, "tiny-q5_1")

    assert ok is True
    m_download.assert_not_called()


def test_download_whisper_model_returns_true_on_success(tmp_path: Path) -> None:
    voice_config_dir = tmp_path / "voice_config"
    model_dir = voice_config_dir / "whisper-models"

    def fake_download_model(model_name, download_dir=None, **kwargs):
        # Mirror pywhispercpp.utils.download_model's real side effect:
        # write the ggml-<name>.bin file into download_dir.
        Path(download_dir).mkdir(parents=True, exist_ok=True)
        (Path(download_dir) / f"ggml-{model_name}.bin").write_bytes(b"fake-ggml-bytes")
        return str(Path(download_dir) / f"ggml-{model_name}.bin")

    with mock.patch("pywhispercpp.utils.download_model", side_effect=fake_download_model) as m_download:
        ok = stt_mod._download_whisper_model(voice_config_dir, "tiny-q5_1")

    assert ok is True
    m_download.assert_called_once()
    assert (model_dir / "ggml-tiny-q5_1.bin").is_file()


def test_download_whisper_model_degrades_gracefully_on_network_failure(tmp_path: Path) -> None:
    """ADR-0185 Decision 3 / Must-NOT-do: no network at install time must
    defer with a clear message, never raise or abort the rest of
    corvin-install."""
    voice_config_dir = tmp_path / "voice_config"

    with mock.patch("pywhispercpp.utils.download_model", side_effect=ConnectionError("no network")):
        ok = stt_mod._download_whisper_model(voice_config_dir, "tiny-q5_1")

    assert ok is False  # must not raise


def test_download_whisper_model_returns_false_when_no_file_produced(tmp_path: Path) -> None:
    """download_model() can silently no-op (e.g. invalid model name) without
    raising — treat "no file on disk" as failure too, not a false success."""
    voice_config_dir = tmp_path / "voice_config"

    with mock.patch("pywhispercpp.utils.download_model", return_value=None):
        ok = stt_mod._download_whisper_model(voice_config_dir, "tiny-q5_1")

    assert ok is False
