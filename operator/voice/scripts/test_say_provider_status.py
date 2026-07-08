#!/usr/bin/env python3
"""Unit tests for ``say.py::provider_status()`` (ADR-0185 M4).

Backs the Console voice-status panel's TTS rows. Cheap introspection
only — never synthesizes audio, never raises. Covers package-missing /
model-missing / key-missing / all-ready states via monkeypatching, no
real network call or model download.
"""
from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(_SCRIPTS))

import say as _say  # noqa: E402


class ProviderStatusShapeTests(unittest.TestCase):
    def test_shape_has_openai_edge_piper_keys(self):
        status = _say.provider_status()
        for name in ("openai", "edge", "piper"):
            self.assertIn(name, status)
        for entry in status.values():
            for key in ("ready", "package_installed", "model_present",
                        "key_configured", "detail"):
                self.assertIn(key, entry)


class OpenAiStatusTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("VOICE_CONFIG_DIR", "OPENAI_API_KEY", "CORVIN_TTS_OPENAI_KEY")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp(prefix="say-status-test-")
        os.environ["VOICE_CONFIG_DIR"] = self._tmpdir
        # say.py caches VOICE_CONFIG_DIR at module-import time — reload so
        # _load_key_from_env_files()/_resolve_key() read this test's empty
        # tmpdir instead of the developer machine's real config dir.
        importlib.reload(_say)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        importlib.reload(_say)

    def test_no_key_reports_not_ready(self):
        status = _say.provider_status()
        self.assertFalse(status["openai"]["ready"])
        self.assertFalse(status["openai"]["key_configured"])
        self.assertIn("no API key", status["openai"]["detail"])

    def test_key_and_package_present_reports_ready(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-say-status-probe"
        with mock.patch.dict(sys.modules, {"openai": mock.MagicMock()}):
            status = _say.provider_status()
        self.assertTrue(status["openai"]["key_configured"])
        self.assertTrue(status["openai"]["ready"])

    def test_key_present_but_package_missing_reports_not_ready(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-say-status-probe"
        with mock.patch.dict(sys.modules, {"openai": None}):
            status = _say.provider_status()
        self.assertTrue(status["openai"]["key_configured"])
        self.assertFalse(status["openai"]["ready"])
        self.assertIn("not installed", status["openai"]["detail"])


class EdgeStatusTests(unittest.TestCase):
    def test_package_missing_reports_not_ready(self):
        with mock.patch.dict(sys.modules, {"edge_tts": None}):
            status = _say.provider_status()
        self.assertFalse(status["edge"]["ready"])
        self.assertFalse(status["edge"]["package_installed"])
        self.assertIn("not installed", status["edge"]["detail"])

    def test_package_present_reports_ready(self):
        with mock.patch.dict(sys.modules, {"edge_tts": mock.MagicMock()}):
            status = _say.provider_status()
        self.assertTrue(status["edge"]["ready"])
        self.assertTrue(status["edge"]["package_installed"])
        # No API key concept for edge-tts.
        self.assertIsNone(status["edge"]["key_configured"])


class PiperStatusTests(unittest.TestCase):
    def test_no_binary_no_package_reports_not_ready(self):
        with mock.patch.dict(sys.modules, {"piper": None}), \
             mock.patch("shutil.which", return_value=None):
            status = _say.provider_status()
        self.assertFalse(status["piper"]["ready"])
        self.assertFalse(status["piper"]["package_installed"])
        self.assertIn("not installed", status["piper"]["detail"])

    def test_binary_present_but_no_model_reports_not_ready(self):
        with mock.patch.dict(sys.modules, {"piper": None}), \
             mock.patch("shutil.which", return_value="/usr/bin/piper"), \
             mock.patch.object(_say, "_piper_model_for", return_value=None):
            status = _say.provider_status()
        self.assertTrue(status["piper"]["package_installed"])
        self.assertFalse(status["piper"]["model_present"])
        self.assertFalse(status["piper"]["ready"])
        self.assertIn("no Piper voice model", status["piper"]["detail"])

    def test_binary_and_model_present_reports_ready(self):
        with mock.patch.dict(sys.modules, {"piper": None}), \
             mock.patch("shutil.which", return_value="/usr/bin/piper"), \
             mock.patch.object(_say, "_piper_model_for", return_value=Path("/fake/model.onnx")):
            status = _say.provider_status()
        self.assertTrue(status["piper"]["ready"])
        self.assertTrue(status["piper"]["model_present"])


class NeverRaisesTests(unittest.TestCase):
    def test_provider_status_never_raises_on_bad_env(self):
        # Sanity: even with a nonsense VOICE_CONFIG_DIR, provider_status()
        # must return a dict, never propagate.
        with mock.patch.dict(os.environ, {"VOICE_CONFIG_DIR": "/nonexistent/path/xyz"}):
            importlib.reload(_say)
            try:
                status = _say.provider_status()
            finally:
                importlib.reload(_say)
        self.assertIsInstance(status, dict)


if __name__ == "__main__":
    unittest.main()
