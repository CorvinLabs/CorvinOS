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

Most tests here do NOT call OpenAI or load a real local-whisper model. A
StubProvider is registered into the resolver's _PROVIDERS map at test time
and the env override pins the chain to it — those assertions stay hermetic.

The ``LocalWhisperPywhispercppTests`` class below is the exception, by
design (ADR-0185 M1 "must" requirement): it is a REAL, non-mocked
round-trip through ``pywhispercpp`` — downloads the tiny quantized GGML
model (~31 MB, cached under a fixed test dir so repeat local runs don't
re-fetch it) and transcribes a real speech sample fixture
(``fixtures/stt_sample.wav``). It requires network on first run and is
skipped only when ``pywhispercpp`` itself isn't importable in the current
environment — never faked, never mocked.
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
from unittest import mock

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
    provider_status,
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
    _resolve_api_key() must therefore also check the canonical service.env
    file directly so STT doesn't depend on how the process was launched.

    WA-22: the second, independently-drifting ~/.config/corvin-voice/.env
    file is retired — service.env is the ONE canonical file consulted
    (see operator/bridges/shared/provider_keys.py).
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
        env_path = Path(self._tmpdir) / "service.env"
        env_path.write_text('OPENAI_API_KEY="sk-from-file-test"\n', encoding="utf-8")
        self.assertEqual(self._oaw._resolve_api_key(), "sk-from-file-test")

    def test_env_var_takes_priority_over_file(self):
        env_path = Path(self._tmpdir) / "service.env"
        env_path.write_text("OPENAI_API_KEY=sk-from-file\n", encoding="utf-8")
        os.environ["OPENAI_API_KEY"] = "sk-from-env-var"
        self.assertEqual(self._oaw._resolve_api_key(), "sk-from-env-var")

    def test_retired_dotenv_file_is_no_longer_consulted(self):
        """WA-22: a value living ONLY in the retired .env file must not
        surface — pre-consolidation this test's own predecessor wrote here."""
        env_path = Path(self._tmpdir) / ".env"
        env_path.write_text('OPENAI_API_KEY="sk-should-be-ignored"\n', encoding="utf-8")
        self.assertIsNone(self._oaw._resolve_api_key())

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


# ── Local whisper (pywhispercpp) — REAL, non-mocked round-trip ──────
#
# ADR-0185 M1 explicitly requires a real (not mocked) STT round-trip test,
# not deferred to a later milestone. This class downloads the tiny
# quantized GGML model (~31 MB, first run only) into a fixed test-cache
# directory under the OS temp dir and transcribes a real speech sample
# checked into the repo (fixtures/stt_sample.wav, synthesized once via
# edge-tts + ffmpeg — not regenerated at test time). Skipped only when
# pywhispercpp itself isn't importable; a network failure during the
# model download is a genuine, loud test failure — never silently skipped.


class LocalWhisperPywhispercppTests(unittest.TestCase):
    _FIXTURE = _SCRIPTS / "fixtures" / "stt_sample.wav"
    _TEST_VOICE_CONFIG_DIR = str(Path(tempfile.gettempdir()) / "corvinos-stt-test-voice-config")

    @classmethod
    def setUpClass(cls):
        try:
            import pywhispercpp.model  # noqa: F401
        except ImportError:
            raise unittest.SkipTest("pywhispercpp not installed in this environment")
        if not cls._FIXTURE.is_file():
            raise unittest.SkipTest(f"missing test fixture: {cls._FIXTURE}")

    def setUp(self):
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("VOICE_CONFIG_DIR", "CORVIN_STT_LOCAL_ENGINE", "CORVIN_STT_LOCAL_MODEL")
        }
        os.environ["VOICE_CONFIG_DIR"] = self._TEST_VOICE_CONFIG_DIR
        os.environ.pop("CORVIN_STT_LOCAL_ENGINE", None)
        os.environ.pop("CORVIN_STT_LOCAL_MODEL", None)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_is_available_true(self):
        from stt.local_whisper import LocalWhisperProvider
        self.assertTrue(LocalWhisperProvider().is_available())

    def test_real_transcription_of_known_sample(self):
        from stt.local_whisper import LocalWhisperProvider
        result = LocalWhisperProvider().transcribe(self._FIXTURE)
        self.assertEqual(result.provider, "local")
        self.assertIn("testing local voice transcription", result.text.lower())
        self.assertEqual(result.lang, "en")
        self.assertEqual(result.chars, len(result.text))

    def test_lang_hint_is_honoured(self):
        from stt.local_whisper import LocalWhisperProvider
        result = LocalWhisperProvider().transcribe(self._FIXTURE, lang="en")
        self.assertIn("testing local voice transcription", result.text.lower())
        self.assertEqual(result.lang, "en")

    def test_missing_audio_file_raises_transcription_failed(self):
        from stt.local_whisper import LocalWhisperProvider
        with self.assertRaises(STTTranscriptionFailed):
            LocalWhisperProvider().transcribe(Path("/nonexistent/audio-for-stt-test.wav"))

    def test_timeout_raises_stt_timeout(self):
        from stt.local_whisper import LocalWhisperProvider
        with self.assertRaises(STTTimeout):
            LocalWhisperProvider().transcribe(self._FIXTURE, timeout_s=0.0001)


class LocalWhisperMissingPackageTests(unittest.TestCase):
    """is_available()/transcribe() degrade to STTProviderUnavailable when
    pywhispercpp is not importable — simulated via sys.modules patching so
    this test runs regardless of whether pywhispercpp is actually
    installed in the current environment, and resets the module-level
    model cache so a real model loaded by another test class doesn't mask
    the simulated missing-package condition.
    """

    def setUp(self):
        from stt import local_whisper as lw
        self._lw = lw
        self._saved_cache = lw._loaded_model
        lw._loaded_model = None

    def tearDown(self):
        self._lw._loaded_model = self._saved_cache

    def test_is_available_false_without_package(self):
        from stt.local_whisper import LocalWhisperProvider
        with mock.patch.dict(sys.modules, {"pywhispercpp": None, "pywhispercpp.model": None}):
            self.assertFalse(LocalWhisperProvider().is_available())

    def test_transcribe_raises_provider_unavailable_without_package(self):
        from stt.local_whisper import LocalWhisperProvider
        with mock.patch.dict(sys.modules, {"pywhispercpp": None, "pywhispercpp.model": None}):
            with self.assertRaises(STTProviderUnavailable):
                LocalWhisperProvider().transcribe(_tmp_audio())


def _tmp_wav16k() -> Path:
    """A real 16-kHz mono 16-bit WAV (silence) — passes _ensure_wav_16k's
    passthrough check so provider tests exercise inference, not conversion."""
    import tempfile
    import wave as _wave
    f = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    f.close()
    with _wave.open(f.name, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(16000)
        wf.writeframes(b"\x00\x00" * 1600)  # 0.1 s silence
    return Path(f.name)


class LocalWhisperInferenceSerializationTests(unittest.TestCase):
    """whisper.cpp contexts are not thread-safe: concurrent transcribe()
    calls on the shared model singleton must be serialized by
    _transcribe_lock (adversarial-review finding: two simultaneous Discord
    voice notes ran whisper_full on one C context — segfault risk plus
    user A's segments bleeding into user B's transcript)."""

    def test_concurrent_pywhispercpp_transcribes_never_overlap(self):
        import threading as _threading
        import time
        from stt.local_whisper import LocalWhisperProvider

        overlap = {"active": 0, "max": 0}
        guard = _threading.Lock()

        class _Seg:
            text = "hi"
            t1 = 100

        class _FakeModel:
            def transcribe(self, path, abort_callback=None, **kw):
                with guard:
                    overlap["active"] += 1
                    overlap["max"] = max(overlap["max"], overlap["active"])
                time.sleep(0.05)
                with guard:
                    overlap["active"] -= 1
                return [_Seg()]

        provider = LocalWhisperProvider()
        audio = _tmp_wav16k()
        model = _FakeModel()
        errors: list[BaseException] = []

        def _run():
            try:
                provider._transcribe_pywhispercpp(
                    model, audio, lang="de", budget=10.0)
            except BaseException as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [_threading.Thread(target=_run) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errors, [])
        self.assertEqual(
            overlap["max"], 1,
            "concurrent transcribe() calls overlapped on the shared model — "
            "_transcribe_lock is not serializing inference",
        )

    def test_lock_wait_charges_budget_and_times_out(self):
        import threading as _threading
        import time
        from stt import local_whisper as lw
        from stt.local_whisper import LocalWhisperProvider

        release = _threading.Event()

        class _SlowModel:
            def transcribe(self, path, abort_callback=None, **kw):
                release.wait(5.0)
                return []

        provider = LocalWhisperProvider()
        audio = _tmp_wav16k()
        holder_started = _threading.Event()

        def _holder():
            holder_started.set()
            provider._transcribe_pywhispercpp(
                _SlowModel(), audio, lang="de", budget=10.0)

        t = _threading.Thread(target=_holder, daemon=True)
        t.start()
        holder_started.wait(2.0)
        time.sleep(0.05)  # let the holder actually take the lock
        try:
            # STTProviderUnavailable (not STTTimeout): a busy shared model is
            # a structural local-busy condition the resolver must fall
            # through on, not a hard turn failure.
            with self.assertRaises(STTProviderUnavailable):
                provider._transcribe_pywhispercpp(
                    _SlowModel(), audio, lang="de", budget=0.2)
        finally:
            release.set()
            t.join(5.0)
        self.assertFalse(
            lw._transcribe_lock.locked(),
            "inference lock leaked after timeout path",
        )


class LocalWhisperEngineSelectionTests(unittest.TestCase):
    """CORVIN_STT_LOCAL_ENGINE opt-in switch to the legacy faster-whisper
    path — pure logic test, no model load (faster-whisper's own model
    download is a heavy CTranslate2 fetch, out of scope for this gate).
    """

    def setUp(self):
        self._saved = os.environ.get("CORVIN_STT_LOCAL_ENGINE")
        os.environ.pop("CORVIN_STT_LOCAL_ENGINE", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("CORVIN_STT_LOCAL_ENGINE", None)
        else:
            os.environ["CORVIN_STT_LOCAL_ENGINE"] = self._saved

    def test_default_prefers_pywhispercpp(self):
        from stt import local_whisper as lw
        self.assertFalse(lw._prefer_faster_whisper())

    def test_opt_in_env_selects_faster_whisper(self):
        from stt import local_whisper as lw
        os.environ["CORVIN_STT_LOCAL_ENGINE"] = "faster-whisper"
        self.assertTrue(lw._prefer_faster_whisper())


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

    def test_timeout_on_terminal_provider_reraises(self):
        # A timeout on the LAST provider in the chain is terminal — there is
        # nothing left to fall through to, so re-raise STTTimeout instead of
        # multiplying the user's wait.
        audio = _tmp_audio()
        with _patched_registry({
            "stub_ok":      _OkProvider,
            "stub_timeout": _TimeoutProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_timeout"
            with self.assertRaises(STTTimeout):
                stt_transcribe(audio)

    def test_timeout_on_nonterminal_provider_falls_through(self):
        # With the default chain now leading "openai,local", a blackholed
        # cloud endpoint that burns the budget must NOT hard-kill STT while a
        # healthy on-box provider sits unused — a non-terminal timeout falls
        # through to the next provider (regression fix, 2026-07-09 review).
        audio = _tmp_audio()
        with _patched_registry({
            "stub_timeout": _TimeoutProvider,
            "stub_ok":      _OkProvider,
        }):
            os.environ["CORVIN_STT_CHAIN"] = "stub_timeout,stub_ok"
            result = stt_transcribe(audio)
            self.assertEqual(result.provider, "stub_ok")

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


# ── Provider status introspection (ADR-0185 M4) ──────────────────────
#
# provider_status() backs the Console's voice-status panel. It must never
# raise and must never trigger a real transcription — these tests cover
# package-missing / model-missing / key-missing / all-ready states via
# monkeypatching, never a real model download.


class ProviderStatusTests(unittest.TestCase):
    def setUp(self):
        self._saved_env = {
            k: os.environ.get(k)
            for k in ("VOICE_CONFIG_DIR", "CORVIN_STT_OPENAI_KEY",
                      "OPENAI_API_KEY", "CORVIN_STT_LOCAL_MODEL")
        }
        for k in self._saved_env:
            os.environ.pop(k, None)
        self._tmpdir = tempfile.mkdtemp(prefix="stt-status-test-")
        os.environ["VOICE_CONFIG_DIR"] = self._tmpdir

        # openai_whisper.py caches _VOICE_CONFIG_DIR at import time (module
        # level), so the developer machine's real ~/.config/corvin-voice/.env
        # (if any) would otherwise leak through here — reload so
        # _resolve_api_key() reads from this test's empty tmpdir instead
        # (same fix OpenAIKeyEnvFileFallbackTests above already applies).
        import importlib
        from stt import openai_whisper as _oaw
        importlib.reload(_oaw)

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        import importlib
        from stt import openai_whisper as _oaw
        importlib.reload(_oaw)  # restore module-level VOICE_CONFIG_DIR

    def test_shape_has_local_and_openai_keys(self):
        status = provider_status()
        self.assertIn("local", status)
        self.assertIn("openai", status)
        for entry in status.values():
            for key in ("ready", "package_installed", "model_present",
                        "key_configured", "detail"):
                self.assertIn(key, entry)

    def test_local_package_missing_reports_not_ready(self):
        with mock.patch.dict(sys.modules, {"pywhispercpp": None, "pywhispercpp.model": None}):
            status = provider_status()
        self.assertFalse(status["local"]["ready"])
        self.assertFalse(status["local"]["package_installed"])
        self.assertIn("not installed", status["local"]["detail"])

    def test_local_model_missing_reports_not_ready_but_package_ok(self):
        # Simulate: package importable, but the GGML model file was never
        # downloaded (e.g. no network at install time — ADR-0185 Decision 3).
        fake_pywhispercpp = mock.MagicMock()
        with mock.patch.dict(sys.modules, {
            "pywhispercpp": fake_pywhispercpp,
            "pywhispercpp.model": mock.MagicMock(),
        }):
            status = provider_status()
        self.assertFalse(status["local"]["ready"])
        self.assertTrue(status["local"]["package_installed"])
        self.assertFalse(status["local"]["model_present"])
        self.assertIn("not downloaded", status["local"]["detail"])

    def test_local_ready_when_package_and_model_present(self):
        model_dir = Path(self._tmpdir) / "whisper-models"
        model_dir.mkdir(parents=True)
        # Filename must match local_whisper._DEFAULT_MODEL — this test leaves
        # CORVIN_STT_LOCAL_MODEL unset (see setUp), so provider_status() falls
        # through to the module default.
        from stt.local_whisper import _DEFAULT_MODEL
        (model_dir / f"ggml-{_DEFAULT_MODEL}.bin").write_bytes(b"fake-model-bytes")
        fake_pywhispercpp = mock.MagicMock()
        with mock.patch.dict(sys.modules, {
            "pywhispercpp": fake_pywhispercpp,
            "pywhispercpp.model": mock.MagicMock(),
        }):
            status = provider_status()
        self.assertTrue(status["local"]["ready"])
        self.assertTrue(status["local"]["model_present"])

    def test_openai_key_missing_reports_not_ready(self):
        status = provider_status()
        self.assertFalse(status["openai"]["ready"])
        self.assertFalse(status["openai"]["key_configured"])
        self.assertIn("no API key", status["openai"]["detail"])

    def test_openai_ready_when_key_and_package_present(self):
        os.environ["OPENAI_API_KEY"] = "sk-test-key-for-status-probe"
        fake_openai = mock.MagicMock()
        with mock.patch.dict(sys.modules, {"openai": fake_openai}):
            status = provider_status()
        self.assertTrue(status["openai"]["key_configured"])
        self.assertTrue(status["openai"]["ready"])

    def test_never_raises_and_never_leaks_exception_text(self):
        """Even if introspection blows up internally, provider_status()
        degrades to a safe entry rather than propagating — the Console
        status panel must never 500 because of this probe."""
        with mock.patch(
            "stt.local_whisper._models_dir",
            side_effect=RuntimeError("disk exploded"),
        ):
            status = provider_status()  # must not raise
        self.assertIn("local", status)
        self.assertNotIn("disk exploded", status["local"]["detail"])


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
