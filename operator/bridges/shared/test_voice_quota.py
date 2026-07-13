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
import threading
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


def _run_concurrent_skip_reason_race() -> dict:
    """Reproduce the cross-thread skip-reason clobber in adapter.py.

    _voice_engine_state (adapter.py ~7419) is a single process-wide dict.
    Each individual field write/read is guarded by _voice_engine_lock, but
    the CALLER's two-statement sequence — ``voice_path =
    synthesize_voice_note(...)`` (adapter.py ~9553) followed by a later,
    separate ``reason = voice_skip_reason()`` (adapter.py ~9562) — is NOT
    one atomic operation. Anything that runs between those two statements
    (including another chat's own synth call, dispatched by the real
    ThreadPoolExecutor in adapter.main()) can overwrite last_skip_reason
    first.

    Uses threading.Event barriers rather than sleep-based timing so the
    interleaving is deterministic and not flaky:

      Thread A: reset quota_until to the past -> synth() (all 3 engines
                mocked unavailable) -> produces the "no TTS engine
                available" reason -> signal a_done -> WAIT for b_done
                (mirrors the real gap between the two caller statements)
                -> THEN read voice_skip_reason().
      Thread B: WAIT for a_done -> set quota_until into the future
                (simulating an unrelated 429 that just landed on B's own
                chat) -> synth() -> produces the "OpenAI hit rate limit"
                reason -> signal b_done -> read voice_skip_reason()
                immediately (no artificial gap on B's side, isolating the
                effect under test to A's gap).

    The two reasons are deliberately different strings so a clobber is
    unambiguously detectable (a coincidental match couldn't happen).
    """
    a_done = threading.Event()
    b_done = threading.Event()
    results: dict = {}

    def thread_a():
        adapter._voice_engine_state["quota_until"] = 0.0
        with patch.object(adapter, "_try_openai_tts", return_value=None), \
             patch.object(adapter, "_try_edge_tts", return_value=None), \
             patch.object(adapter, "_try_piper_tts", return_value=None):
            adapter.synthesize_voice_note("hallo welt", lang="de")
        # Captured single-threaded (B is still blocked on a_done) — this is
        # unambiguously A's own outcome, not yet touched by B.
        results["reason_a_expected"] = adapter._voice_engine_state["last_skip_reason"]
        a_done.set()
        # The real production gap: other work happens between the synth
        # call and the later voice_skip_reason() read in adapter.py. Here we
        # force a concurrent chat's own synth call into exactly that
        # window.
        b_done.wait(timeout=5)
        results["reason_a_observed"] = adapter.voice_skip_reason()

    def thread_b():
        a_done.wait(timeout=5)
        adapter._voice_engine_state["quota_until"] = time.time() + 3600
        with patch.object(adapter, "_try_openai_tts", return_value=None), \
             patch.object(adapter, "_try_edge_tts", return_value=None), \
             patch.object(adapter, "_try_piper_tts", return_value=None):
            adapter.synthesize_voice_note("hello world", lang="en")
        results["reason_b_expected"] = adapter._voice_engine_state["last_skip_reason"]
        b_done.set()
        results["reason_b_observed"] = adapter.voice_skip_reason()

    ta = threading.Thread(target=thread_a, name="chatA-synth")
    tb = threading.Thread(target=thread_b, name="chatB-synth")
    ta.start()
    tb.start()
    ta.join(timeout=10)
    tb.join(timeout=10)
    return results


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
        # We patch _load_env_value (legacy path) AND provider_keys.resolve_key
        # (the canonical resolver _try_openai_tts actually calls since the
        # ADR-0193/WA-22 2026-07-12 refactor — see adapter.py ~7663) to always
        # return None. Without the second patch this case silently exercised
        # the success path instead of "missing key" on any machine whose real
        # ~/.config/corvin-voice/service.env happens to have a working
        # OPENAI_API_KEY configured (e.g. any dev/maintainer machine) —
        # discovered as a live failure while adding the concurrency-race test
        # below.
        with patch.object(adapter, "_load_env_value", return_value=None), \
             patch.object(adapter._provider_keys, "resolve_key", return_value=None), \
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

    # 5) Concurrent TTS requests: _voice_engine_state is a single
    #    process-wide dict, and the caller's `voice_path =
    #    synthesize_voice_note(...)` followed by a later, separate `reason =
    #    voice_skip_reason()` (adapter.py ~9553/~9562) is NOT one atomic
    #    operation. A concurrent chat's own synth call can land in that gap
    #    and overwrite last_skip_reason with ITS OWN outcome first — a
    #    cross-request information leak where one user sees another user's
    #    unrelated failure reason.
    _reset_state()
    race = _run_concurrent_skip_reason_race()
    t("[race] thread B observes its own reason (no gap on B's side in this repro)",
      race.get("reason_b_observed") == race.get("reason_b_expected"),
      detail=f"observed={race.get('reason_b_observed')!r} expected={race.get('reason_b_expected')!r}")
    t("[race] thread A's skip reason is NOT clobbered by a concurrent thread's "
      "later call — KNOWN BUG if this fails: _voice_engine_state['last_skip_reason'] "
      "is shared process-wide state, and synthesize_voice_note()+voice_skip_reason() "
      "are two separate calls at the caller site, not one atomic read-after-write. "
      "See adapter.py ~9553/~9562 and _voice_engine_state comment (~7409-7426).",
      race.get("reason_a_observed") == race.get("reason_a_expected"),
      detail=f"observed={race.get('reason_a_observed')!r} expected={race.get('reason_a_expected')!r}")

    # 6) Residual thread-local leak when synthesis is never attempted:
    #    the thread-local fix (case 5 above) only helps when the CURRENT
    #    turn actually calls synthesize_voice_note(). _synthesize_voice_for_turn()
    #    (adapter.py, extracted from process_one) can decide voice WAS
    #    expected (mode "always" / over threshold) but build_voice_summary()
    #    returns "" — in that case synthesize_voice_note() is never called
    #    this turn, so without an explicit reset the thread-local mirror
    #    would still hold whatever a DIFFERENT chat's turn left there the
    #    last time THIS SAME pooled thread ran a real synth call (adapter.py
    #    dispatches turns onto a ThreadPoolExecutor — thread reuse across
    #    chats is real). No actual threading needed to reproduce this one:
    #    the leak is purely about state carried across two SEQUENTIAL calls
    #    on the same OS thread, which is exactly what thread-pool reuse
    #    looks like from the thread's own point of view.
    _reset_state()
    adapter._voice_local.last_skip_reason = adapter._UNSET  # clean slate

    # "Chat B" — an earlier turn on this exact thread hit a real TTS
    # failure and left a reason behind.
    with patch.object(adapter, "_try_openai_tts", return_value=None), \
         patch.object(adapter, "_try_edge_tts", return_value=None), \
         patch.object(adapter, "_try_piper_tts", return_value=None):
        adapter.synthesize_voice_note("hallo welt chat B", lang="de")
    stale_reason = adapter.voice_skip_reason()
    t("[leak-setup] chat B really did leave a stale reason behind",
      isinstance(stale_reason, str) and bool(stale_reason),
      detail=f"got: {stale_reason!r}")

    # "Chat A" — a LATER, unrelated turn reuses this same pooled thread.
    # Its build_voice_summary() produces nothing, so synthesize_voice_note()
    # is never invoked this turn at all.
    settings_always = {"voice_summary_mode": "always"}
    with patch.object(adapter, "build_voice_summary", return_value=""):
        voice_path_a, voice_was_expected_a = adapter._synthesize_voice_for_turn(
            "chat A's answer", settings_always, None, "chat A task", None,
        )
    t("[leak-fix] chat A: voice was expected (mode=always)",
      voice_was_expected_a is True)
    t("[leak-fix] chat A: voice_path is None (empty summary -> no synth attempted)",
      voice_path_a is None)
    t("[leak-fix] chat A's voice_skip_reason() does NOT leak chat B's stale "
      "reason — KNOWN BUG if this fails: synthesize_voice_note() is the ONLY "
      "thing that writes _voice_local, so when it's never called this turn "
      "(empty build_voice_summary), voice_skip_reason() falls through to "
      "whatever this pooled thread's LAST real synth call left behind, which "
      "can belong to a completely different chat. See "
      "_synthesize_voice_for_turn() in adapter.py.",
      adapter.voice_skip_reason() is None,
      detail=f"observed={adapter.voice_skip_reason()!r} stale_from_chat_B={stale_reason!r}")

    # 7) Negative control for (6): when voice is NOT expected at all this
    #    turn (mode "never" / under threshold), _synthesize_voice_for_turn()
    #    must NOT reset the thread-local — that path never reads
    #    voice_skip_reason() downstream in process_one, so a stale reason
    #    must stay available to any OTHER consumer that intentionally reads
    #    it across turns (e.g. a status command). This proves fix #6 didn't
    #    over-reset.
    _reset_state()
    adapter._voice_local.last_skip_reason = adapter._UNSET
    with patch.object(adapter, "_try_openai_tts", return_value=None), \
         patch.object(adapter, "_try_edge_tts", return_value=None), \
         patch.object(adapter, "_try_piper_tts", return_value=None):
        adapter.synthesize_voice_note("hallo welt chat B again", lang="de")
    stale_reason2 = adapter.voice_skip_reason()

    settings_never = {"voice_summary_mode": "never"}
    voice_path_never, voice_was_expected_never = adapter._synthesize_voice_for_turn(
        "irrelevant answer", settings_never, None, "", None,
    )
    t("[no-over-reset] mode=never: voice not expected",
      voice_was_expected_never is False)
    t("[no-over-reset] mode=never: voice_path is None",
      voice_path_never is None)
    t("[no-over-reset] mode=never: thread-local reason is left UNTOUCHED "
      "(cross-turn persistence for other consumers is intentional here)",
      adapter.voice_skip_reason() == stale_reason2,
      detail=f"observed={adapter.voice_skip_reason()!r} expected(unchanged)={stale_reason2!r}")

    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
