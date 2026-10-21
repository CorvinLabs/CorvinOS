"""say.py fetches a missing Piper language in the background the first time it has to
speak that language with the wrong voice (round 2, 2026-10-21)."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import say  # noqa: E402


@pytest.fixture
def env(tmp_path, monkeypatch):
    models = tmp_path / "piper"
    models.mkdir()
    (models / "en_US-lessac-medium.onnx").write_bytes(b"x" * 10)
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "piper_model_en": str(models / "en_US-lessac-medium.onnx"), "lang_default": "en"}))
    monkeypatch.setattr(say, "_PIPER_MODEL_DIR", models)
    monkeypatch.setattr(say, "VOICE_CONFIG_DIR", cfg_dir)
    for var in list(say.os.environ):
        if var.startswith("CORVIN_PIPER_MODEL_"):
            monkeypatch.delenv(var)
    return models


def test_wrong_language_fallback_requests_the_right_model_once(env, capsys):
    popen = mock.MagicMock()
    with mock.patch("subprocess.Popen", popen):
        model = say._piper_model_for("de")
        assert model is not None and model.name == "en_US-lessac-medium.onnx"
        assert popen.call_count == 1
        argv = popen.call_args.args[0]
        assert argv[:3] == [sys.executable, "-m", "corvinOS.shared.voice_models"] and argv[3] == "de"
        assert popen.call_args.kwargs.get("start_new_session") is True or "creationflags" in popen.call_args.kwargs
        # the second wrong-voice synthesis within the window does not spawn again
        say._piper_model_for("de")
        assert popen.call_count == 1
    err = capsys.readouterr().err
    assert "wrong-language speech" in err and "fetching the Piper model for 'de'" in err
    assert (env / ".fetch-requested-de").exists()


def test_the_request_is_repeated_after_the_interval(env):
    popen = mock.MagicMock()
    with mock.patch("subprocess.Popen", popen):
        say._piper_model_for("de")
        marker = env / ".fetch-requested-de"
        old = time.time() - say._MISSING_MODEL_REQUEST_INTERVAL_S - 5
        say.os.utime(marker, (old, old))
        say._piper_model_for("de")
    assert popen.call_count == 2


def test_an_unknown_language_and_an_exact_model_never_spawn(env):
    popen = mock.MagicMock()
    with mock.patch("subprocess.Popen", popen):
        say._piper_model_for("xx")          # no Piper voice exists for it
        say._piper_model_for("en")          # the exact model is on disk
    assert popen.call_count == 0
