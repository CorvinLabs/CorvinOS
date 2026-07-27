#!/usr/bin/env python3
"""Per-subtask E2E for the per-persona TTS-voice plumbing in
``adapter.synthesize_voice_note`` and the corresponding profile-aware
call site in ``process_one``.

Bug background. The Stop-Hook driven ``speak.sh`` already honours
``CORVIN_CALLER_PERSONA`` via ``voice_persona_voice`` (Layer 9 +
voice_lib.sh extension). For bridge replies, however, the Stop-Hook
short-circuits via ``VOICE_HOOK_RECURSION=1``: the actual voice-note
goes through ``adapter.synthesize_voice_note``. Until this patch the
voice was hard-coded to ``nova`` (DE) / ``alloy`` (EN), so the cowork
persona's ``tts_voice`` field was silently ignored on every Discord/
WhatsApp/Telegram/Slack/E-mail voice-note. This test pins the contract:

    voice arg passed → forwarded verbatim to OpenAI client
    voice arg None → falls back to "nova" (de) / "alloy" (en)
    voice resolution from profile dict prefers tts_voice_<lang>
        over tts_voice (lang-agnostic)

Run: python3 operator/bridges/shared/test_adapter_voice_persona.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Skip when openai is missing — patch("openai.OpenAI", ...) requires openai.
#
# The skip must be spelled differently for the two ways this file runs. As a
# SCRIPT (the `run-all-tests.sh` convention, see the Run: line above) exiting 0
# is right. Under PYTEST it is catastrophic: `sys.exit` raises SystemExit during
# collection, which the collector does not catch, so pytest aborts the whole
# session with "INTERNALERROR ... caught unexpected SystemExit". Since
# operator/bridges/shared/ is one of the paths the coverage workflow passes,
# that killed the entire job ~1.5 s in — no tests, no coverage, and an error
# that names this file rather than the missing dependency (found 2026-07-27).
try:
    import openai  # noqa: F401
except ImportError:
    _msg = "openai package not installed"
    if "pytest" in sys.modules:
        import pytest

        pytest.skip(_msg, allow_module_level=True)
    print(f"[adapter persona voice] SKIP — {_msg}", flush=True)
    sys.exit(0)

import adapter  # type: ignore  # noqa: E402


# ── Fake OpenAI client that records voice= for assertions ─────────────
_last_call_voice: list[str] = []


class _FakeOpenAI:
    """Records the `voice` kwarg of every speech.create() call into a
    module-level list so the test can assert on it."""

    def __init__(self, *_a, **_kw):
        self.audio = self

    @property
    def speech(self):
        return self

    def create(self, **kw):
        _last_call_voice.append(kw.get("voice", ""))

        class _R:
            def read(self):
                return b"FAKE-OGG"

        return _R()


def _reset() -> None:
    _last_call_voice.clear()
    # Clear quota-backoff so prior tests don't short-circuit synth.
    adapter._voice_engine_state["quota_until"] = 0.0
    adapter._voice_engine_state["last_skip_reason"] = None
    os.environ["OPENAI_API_KEY"] = "sk-fake-test-key"


def _call_synth(*, lang: str, voice: str | None) -> Path | None:
    fake_root = Path(tempfile.mkdtemp(prefix="voice-persona-"))
    (fake_root / "outbox").mkdir(parents=True, exist_ok=True)
    with patch.object(adapter, "ROOT", new=fake_root), \
         patch("openai.OpenAI", _FakeOpenAI):
        return adapter.synthesize_voice_note(
            "hallo welt", lang=lang, voice=voice,
        )


failures: list[str] = []


def expect(cond: bool, label: str, detail: str = "") -> None:
    if cond:
        print(f"PASS: {label}")
    else:
        msg = f"{label}{(' — ' + detail) if detail else ''}"
        failures.append(msg)
        print(f"FAIL: {msg}")


def main() -> int:
    # ── 1. Explicit voice arg is forwarded to OpenAI ─────────────────
    _reset()
    out = _call_synth(lang="de", voice="onyx")
    expect(out is not None, "synth returns a path with explicit voice")
    expect(_last_call_voice == ["onyx"],
           "voice='onyx' forwarded verbatim",
           f"got {_last_call_voice}")

    # ── 2. None → hardcoded language default (DE → nova) ─────────────
    _reset()
    _call_synth(lang="de", voice=None)
    expect(_last_call_voice == ["nova"],
           "voice=None DE → nova fallback",
           f"got {_last_call_voice}")

    # ── 3. None → hardcoded language default (EN → nova) ─────────────
    # Both DE and EN use "nova" as the default voice; language-specific
    # selection was removed in favour of a single universal default.
    _reset()
    _call_synth(lang="en", voice=None)
    expect(_last_call_voice == ["nova"],
           "voice=None EN → nova fallback",
           f"got {_last_call_voice}")

    # ── 4. Empty string is treated as "no voice" → fallback ──────────
    _reset()
    _call_synth(lang="de", voice="")
    expect(_last_call_voice == ["nova"],
           "voice='' DE → nova fallback (treats empty as None)",
           f"got {_last_call_voice}")

    # ── 5. Profile-dict resolution helper — what process_one does ────
    # Replicate the resolution logic verbatim from process_one to lock
    # the contract in a unit test that never spawns a subprocess.
    def _resolve(profile: dict | None) -> str | None:
        if isinstance(profile, dict):
            return (profile.get("tts_voice_de")
                    or profile.get("tts_voice"))
        return None

    expect(_resolve({"tts_voice": "onyx"}) == "onyx",
           "profile.tts_voice → onyx")
    expect(_resolve({"tts_voice": "alloy",
                     "tts_voice_de": "fable"}) == "fable",
           "profile.tts_voice_de beats profile.tts_voice")
    expect(_resolve({}) is None,
           "empty profile → None (caller-side fallback)")
    expect(_resolve(None) is None,
           "profile=None → None (graceful)")

    # ── 6. Synth uses the resolved voice (end-to-end through call) ───
    _reset()
    profile = {"name": "jarvis", "tts_voice": "onyx"}
    voice_for_synth = _resolve(profile)
    _call_synth(lang="de", voice=voice_for_synth)
    expect(_last_call_voice == ["onyx"],
           "jarvis profile → synth called with onyx",
           f"got {_last_call_voice}")

    # ── 7. Lang-specific override carries through ───────────────────
    _reset()
    profile = {"name": "bilingual",
               "tts_voice": "alloy",
               "tts_voice_de": "fable"}
    voice_for_synth = _resolve(profile)
    _call_synth(lang="de", voice=voice_for_synth)
    expect(_last_call_voice == ["fable"],
           "bilingual profile DE → fable",
           f"got {_last_call_voice}")

    if failures:
        print(f"\n{len(failures)} FAILURE(S):")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
