"""Quota-aware voice synthesis: when OpenAI returns 429, the adapter
records the cause, short-circuits subsequent calls within the backoff
window, and surfaces a user-facing notice via voice_skip_reason() so the
adapter can append it to the text reply.

Tests the structural pieces in isolation — no real OpenAI calls. The
inline-notice integration in process_one is exercised by the existing
adapter tests (test_adapter_parallel etc.) which set
ADAPTER_DISABLE_VOICE=1 and don't go through synthesize at all.

Run: python3 operator/bridges/shared/test_voice_quota.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path
from unittest.mock import patch

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))

# Skip the entire suite when the `openai` package is not installed for the
# python interpreter running the tests. The suite uses unittest.mock.patch
# on `openai.OpenAI`, which forces an import of the module — and that
# import is the bottleneck. The production adapter handles a missing
# `openai` cleanly via lazy import; the test harness does not. Treat this
# as an environmental skip (exit 0) rather than a hard failure so the
# bridge test runner stays green on python interpreters without the
# optional dependency installed.
try:
    import openai  # noqa: F401
except ImportError:
    print("[quota-aware voice synthesis] SKIP — openai package not installed", flush=True)
    import sys as _sys; _sys.exit(0)

import adapter  # noqa: E402


PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


def _reset_state():
    """Wipe module-level voice-engine state between cases."""
    adapter._voice_engine_state["quota_until"] = 0.0
    adapter._voice_engine_state["first_quota_logged"] = False
    adapter._voice_engine_state["last_skip_reason"] = None


class _FakeQuotaExceeded(Exception):
    """Mimics openai.RateLimitError surface — adapter only matches on
    the stringified message, so any Exception with the right text works."""

    def __str__(self):
        return ("Error code: 429 - {'error': {'message': 'You exceeded "
                "your current quota, please check your plan and billing "
                "details.', 'type': 'insufficient_quota'}}")


class _FakeAudioClient:
    """Stand-in for openai.OpenAI that always raises quota_exceeded."""

    def __init__(self, *_, **__):
        self.audio = self  # so client.audio.speech.create works

    @property
    def speech(self):
        return self

    def create(self, **_):
        raise _FakeQuotaExceeded()


def _drive_synth(monkey_quota: bool = True) -> Path | None:
    """Call synthesize_voice_note with a faked OpenAI client.

    monkey_quota=True → client always raises 429. edge-tts/Piper are also
    forced to "unavailable" here — this test is about the OpenAI-quota
    short-circuit specifically, not about whether other engines happen to
    be reachable on the test machine (edge-tts works fine since the
    2026-07-06 asyncio-import + imageio-ffmpeg-fallback fix, and would
    otherwise mask the quota path we're trying to exercise).
    monkey_quota=False → client succeeds.
    """
    import tempfile
    os.environ["OPENAI_API_KEY"] = "sk-fake-test-key"
    fake_root = Path(tempfile.mkdtemp(prefix="quota-test-"))
    (fake_root / "outbox").mkdir(parents=True, exist_ok=True)

    if monkey_quota:
        with patch.object(adapter, "ROOT", new=fake_root), \
             patch("openai.OpenAI", _FakeAudioClient), \
             patch.object(adapter, "_try_edge_tts", return_value=None), \
             patch.object(adapter, "_try_piper_tts", return_value=None):
            return adapter.synthesize_voice_note("hallo welt", lang="de")
    else:
        # Successful path — fake OpenAI returns bytes-like via duck typing.
        class _OK:
            def __init__(self, *_, **__):
                self.audio = self

            @property
            def speech(self):
                return self

            def create(self, **_):
                class _R:
                    def read(self):
                        return b"FAKE-OGG"
                return _R()

        with patch.object(adapter, "ROOT", new=fake_root), \
             patch("openai.OpenAI", _OK):
            return adapter.synthesize_voice_note("hallo welt", lang="de")


def main() -> int:
    print("[quota-aware voice synthesis]")
    _reset_state()

    # 1) First call after a 429 → returns None, sets quota_until in future,
    #    records a user-facing reason.
    t0 = time.time()
    result = _drive_synth(monkey_quota=True)
    t("synth returns None on 429",
      result is None)
    qu = adapter._voice_engine_state["quota_until"]
    t("quota_until is set ~1h into the future",
      qu > t0 + 3500 and qu < t0 + 3700,
      detail=f"qu={qu:.1f}, t0={t0:.1f}, delta={qu - t0:.1f}")
    reason = adapter.voice_skip_reason()
    t("voice_skip_reason mentions OpenAI quota",
      isinstance(reason, str) and "rate limit" in reason,
      detail=f"got: {reason!r}")
    t("first_quota_logged flag is set (won't re-log spam)",
      adapter._voice_engine_state["first_quota_logged"] is True)

    # 2) Second call within the backoff window → short-circuits, no API call.
    #    We patch openai.OpenAI to detonate; the short-circuit path must
    #    never reach it.
    second_called: list[bool] = [False]

    class _Detonator:
        def __init__(self, *_, **__):
            second_called[0] = True
            raise AssertionError("synth must NOT hit the API while in quota backoff")

    with patch.object(adapter, "ROOT", new=Path("/tmp")), \
         patch("openai.OpenAI", _Detonator), \
         patch.object(adapter, "_try_edge_tts", return_value=None), \
         patch.object(adapter, "_try_piper_tts", return_value=None):
        result2 = adapter.synthesize_voice_note("hallo welt", lang="de")

    t("second synth (within backoff) returns None",
      result2 is None)
    t("second synth does NOT instantiate OpenAI client",
      second_called[0] is False)

    # 3) After the backoff window expires AND the API succeeds — synth runs,
    #    skip reason is cleared, first_quota_logged resets so the next 429
    #    is logged again.
    adapter._voice_engine_state["quota_until"] = 0.0  # simulate window over
    result3 = _drive_synth(monkey_quota=False)
    t("synth runs again after backoff expires (success path)",
      result3 is not None)
    t("voice_skip_reason cleared after a successful synth",
      adapter.voice_skip_reason() is None)
    t("first_quota_logged reset to False after success",
      adapter._voice_engine_state["first_quota_logged"] is False)

    # 4) Missing API key path also produces a user-facing reason
    #    (different message).
    _reset_state()
    saved_key = os.environ.pop("OPENAI_API_KEY", None)
    try:
        # Ensure no .env file is present in any of the candidate
        # locations — the function falls back to walking up to repo-root.
        # We just patch _load_env_value to always return None.
        with patch.object(adapter, "_load_env_value", return_value=None), \
             patch.object(adapter, "_try_edge_tts", return_value=None), \
             patch.object(adapter, "_try_piper_tts", return_value=None):
            result4 = adapter.synthesize_voice_note("hallo welt", lang="de")
        t("missing-key path returns None",
          result4 is None)
        reason4 = adapter.voice_skip_reason()
        t("missing-key reason mentions API-Key",
          isinstance(reason4, str) and "OPENAI_API_KEY" in reason4,
          detail=f"got: {reason4!r}")
    finally:
        if saved_key is not None:
            os.environ["OPENAI_API_KEY"] = saved_key

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
