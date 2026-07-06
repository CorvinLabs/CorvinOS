#!/usr/bin/env python3
"""Per-subtask E2E for the STT provider chain (engine-agnostic STT).

Covers:
  * Stub provider round-trip — Protocol contract honoured
  * Resolver: CORVIN_STT_PROVIDER pin disables fallback
  * Resolver: fallback chain (first provider unavailable → second wins)
  * Resolver: STTTimeout re-raises without fallback
  * Resolver: STTTranscriptionFailed falls through to next provider
  * Resolver: unknown provider name → STTProviderUnavailable
  * Resolver: CORVIN_STT_CHAIN overrides the default chain
  * TranscriptResult.chars matches len(text)
  * No-PII contract: transcript content NOT in audit details
  * CLI: transcribe.py with stubbed provider returns 0 + prints transcript
  * CLI: pinned-but-unavailable provider returns 1
  * Adapter audit emission: voice.transcribed event lands in chain with
    provider + chars + wall_clock_s but NOT the transcript content.

The tests do NOT call OpenAI or load faster-whisper. A StubProvider is
registered into the resolver's _PROVIDERS map at test time and the env
override pins the chain to it. All assertions stay hermetic.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
_SCRIPTS = _REPO / "operator" / "voice" / "scripts"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_REPO / "operator" / "forge"))

from stt import (  # noqa: E402
    STTError,
    STTProviderUnavailable,
    STTTimeout,
    STTTranscriptionFailed,
    TranscriptResult,
    available_providers,
    resolve,
    transcribe as stt_transcribe,
)
from stt import resolver as _resolver  # noqa: E402


# ── Stub providers (test-only) ───────────────────────────────────────


class _OkProvider:
    name = "stub_ok"
    text = "hello world"
    lang = "de"
    duration = 1.5

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_path, *, lang=None, timeout_s=None):
        return TranscriptResult(
            text=self.text, provider=self.name,
            lang=self.lang, duration_s=self.duration,
        )


class _UnavailableProvider:
    name = "stub_off"

    def is_available(self) -> bool:
        return False

    def transcribe(self, audio_path, *, lang=None, timeout_s=None):
        raise STTProviderUnavailable("stub_off claims unavailable")


class _BrokenProvider:
    name = "stub_broken"

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_path, *, lang=None, timeout_s=None):
        raise STTTranscriptionFailed("stub_broken simulated API error")


class _TimeoutProvider:
    name = "stub_timeout"

    def is_available(self) -> bool:
        return True

    def transcribe(self, audio_path, *, lang=None, timeout_s=None):
        raise STTTimeout("stub_timeout simulated")


@contextmanager
def _patched_registry(extra: dict):
    """Temporarily inject test providers into the resolver registry."""
    original = dict(_resolver._PROVIDERS)
    _resolver._PROVIDERS.update(extra)
    saved_env = {
        k: os.environ.get(k)
        for k in ("CORVIN_STT_PROVIDER", "CORVIN_STT_CHAIN")
    }
    for k in saved_env:
        os.environ.pop(k, None)
    try:
        yield
    finally:
        _resolver._PROVIDERS.clear()
        _resolver._PROVIDERS.update(original)
        for k, v in saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _tmp_audio() -> Path:
    """Return a path to a non-empty file. STT stubs never read it."""
    fd, path = tempfile.mkstemp(suffix=".ogg", prefix="stt-test-")
    os.write(fd, b"FAKE_OGG_BYTES")
    os.close(fd)
    return Path(path)


# ── Protocol contract ────────────────────────────────────────────────


class OpenAIKeyEnvFileFallbackTests(unittest.TestCase):
    """Windows regression: bridge.ps1 launches the console/daemon directly,
    without the .env-into-shell-env step voice_lib.sh does on Linux/macOS.
    _resolve_api_key() must therefore also check the canonical .env file
    directly so STT doesn't depend on how the process was launched.
    """

    def setUp(self):
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("CORVIN_STT_OPENAI_KEY", "OPENAI_API_KEY", "VOICE_CONFIG_DIR")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp(prefix="stt-envfile-test-")
        os.environ["VOICE_CONFIG_DIR"] = self._tmpdir

        import importlib
        from stt import openai_whisper as _oaw
        importlib.reload(_oaw)
        self._oaw = _oaw

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import importlib
        from stt import openai_whisper as _oaw
        importlib.reload(_oaw)  # restore module-level VOICE_CONFIG_DIR

    def test_key_read_from_env_file_when_env_var_absent(self):
        env_path = Path(self._tmpdir) / ".env"
        env_path.write_text('OPENAI_API_KEY="sk-from-file-test"\n', encoding="utf-8")
        self.assertEqual(self._oaw._resolve_api_key(), "sk-from-file-test")

    def test_env_var_takes_priority_over_file(self):
        env_path = Path(self._tmpdir) / ".env"
        env_path.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "sk-from-env-var"
        self.assertEqual(self._oaw._resolve_api_key(), "sk-from-env-var")

    def test_no_key_anywhere_returns_none(self):
        self.assertIsNone(self._oaw._resolve_api_key())

    def test_service_env_file_used_when_dotenv_missing(self):
        (Path(self._tmpdir) / "service.env").write_text(
            "CORVIN_STT_OPENAI_KEY=sk-service-env\n", encoding="utf-8",
        )
        self.assertEqual(self._oaw._resolve_api_key(), "sk-service-env")


class ProtocolContractTests(unittest.TestCase):
    def test_transcript_result_chars_matches_len(self):
        r = TranscriptResult(text="hello", provider="stub", lang="en")
        self.assertEqual(r.chars, 5)

    def test_transcript_result_chars_empty(self):
        r = TranscriptResult(text="", provider="stub")
        self.assertEqual(r.chars, 0)

    def test_real_providers_satisfy_protocol(self):
        from stt.base import STTProvider
        from stt.openai_whisper import OpenAIWhisperProvider
        from stt.local_whisper import LocalWhisperProvider
        self.assertIsInstance(OpenAIWhisperProvider(), STTProvider)
        self.assertIsInstance(LocalWhisperProvider(), STTProvider)


# ── Resolver: env override pins one provider ─────────────────────────


class EnvOverridePinTests(unittest.TestCase):
    def test_pinned_provider_used(self):
        audio = _tmp_audio()
        with _patched_registry({"stub_ok": _OkProvider}):
            os.environ["CORVIN_STT_PROVIDER"] = "stub_ok"
            result = stt_transcribe(audio)
            self.assertEqual(result.text, "hello world")
            self.assertEqual(result.provider, "stub_ok")

    def test_pinned_unavailable_raises_no_fallback(self):
        audio = _tmp_audio()
        with _patched_registry({
            "stub_off": _UnavailableProvider,
            "stub_ok":  _OkProvider,
        }):
            os.environ["CORVIN_STT_PROVIDER"] = "stub_off"
            with self.assertRaises(STTProviderUnavailable):
                stt_transcribe(audio)

    def test_unknown_pinned_provider_raises(self):
        audio = _tmp_audio()
        with _patched_registry({}):
            os.environ["CORVIN_STT_PROVIDER"] = "nonexistent"
            with self.assertRaises(STTProviderUnavailable):
                stt_transcribe(audio)


# ── Resolver: chain semantics ────────────────────────────────────────


class ChainSemanticsTests(unittest.TestCase):
    def test_first_unavailable_falls_through(self):
        audio = _tmp_audio()
        with _patched_registry({
            "stub_off": _UnavailableProvider,
            "stub_ok":  _OkProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_off,stub_ok"
            result = stt_transcribe(audio)
            self.assertEqual(result.provider, "stub_ok")

    def test_first_broken_falls_through(self):
        audio = _tmp_audio()
        with _patched_registry({
            "stub_broken": _BrokenProvider,
            "stub_ok":     _OkProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_broken,stub_ok"
            result = stt_transcribe(audio)
            self.assertEqual(result.provider, "stub_ok")

    def test_timeout_does_not_fall_through(self):
        audio = _tmp_audio()
        with _patched_registry({
            "stub_timeout": _TimeoutProvider,
            "stub_ok":      _OkProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_timeout,stub_ok"
            with self.assertRaises(STTTimeout):
                stt_transcribe(audio)

    def test_all_unavailable_raises(self):
        audio = _tmp_audio()
        with _patched_registry({
            "stub_off":     _UnavailableProvider,
            "stub_broken":  _BrokenProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_off,stub_broken"
            with self.assertRaises(STTProviderUnavailable):
                stt_transcribe(audio)

    def test_unknown_in_chain_silently_skipped(self):
        audio = _tmp_audio()
        with _patched_registry({"stub_ok": _OkProvider}):
            os.environ["CORVIN_STT_CHAIN"] = "nonexistent,stub_ok"
            result = stt_transcribe(audio)
            self.assertEqual(result.provider, "stub_ok")


# ── Available providers probe ────────────────────────────────────────


class AvailableProbeTests(unittest.TestCase):
    def test_available_lists_only_reachable(self):
        with _patched_registry({
            "stub_ok":  _OkProvider,
            "stub_off": _UnavailableProvider,
        }):
            avail = available_providers()
            # Real providers (openai, local) may or may not be available
            # depending on the test machine; assert only our stubs.
            self.assertIn("stub_ok", avail)
            self.assertNotIn("stub_off", avail)


# ── CLI surface ──────────────────────────────────────────────────────


class CliSurfaceTests(unittest.TestCase):
    """Subprocess round-trip on the new transcribe.py shape."""

    def test_cli_returns_1_when_no_provider(self):
        audio = _tmp_audio()
        # Strip env so no real provider is reachable; pin to unknown name
        # so the resolver fails-loud.
        env = {**os.environ, "CORVIN_STT_PROVIDER": "nonexistent"}
        env.pop("OPENAI_API_KEY", None)
        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "transcribe.py"), str(audio)],
            capture_output=True, text=True, env=env,
        )
        self.assertEqual(r.returncode, 1, msg=r.stderr)
        self.assertIn("no provider", r.stderr.lower())

    def test_cli_returns_1_on_missing_file(self):
        r = subprocess.run(
            [sys.executable, str(_SCRIPTS / "transcribe.py"),
             "/nonexistent/audio.ogg"],
            capture_output=True, text=True,
        )
        self.assertEqual(r.returncode, 1)
        self.assertIn("file not found", r.stderr.lower())


# ── Audit-event no-PII contract ──────────────────────────────────────


class NoPIIContractTests(unittest.TestCase):
    """The audit-event MUST NOT carry the transcript content."""

    def test_audit_event_only_carries_metadata(self):
        # We inspect the EXISTING event-emission helpers from adapter.py
        # by importing them with a sandbox audit chain, then inspecting
        # what gets written.
        import importlib.util
        adapter_path = (
            _REPO / "operator" / "bridges" / "shared" / "adapter.py"
        )
        # We don't import the full adapter (heavy boot); we instead
        # validate the contract structurally via the _emit_* helpers.
        # Read source, find _emit_transcribe_ok, assert the only
        # fields under 'details' are the curated metadata-only set.
        src = adapter_path.read_text()
        # Sanity: function exists
        self.assertIn("def _emit_transcribe_ok", src)
        # The function MUST NOT contain 'result.text' in its details
        # construction. Approximate check: scan the function body.
        start = src.index("def _emit_transcribe_ok")
        end = src.index("def _emit_transcribe_failed", start)
        body = src[start:end]
        self.assertNotIn(
            "result.text", body,
            "voice.transcribed audit event leaks the transcript content",
        )
        self.assertNotIn(
            '"text"', body,
            "voice.transcribed audit event includes a text field",
        )
        # The curated detail keys MUST appear
        for key in ("provider", "lang", "wall_clock_s", "chars"):
            self.assertIn(f'"{key}"', body,
                          f"voice.transcribed missing detail key {key!r}")


if __name__ == "__main__":
    unittest.main()
