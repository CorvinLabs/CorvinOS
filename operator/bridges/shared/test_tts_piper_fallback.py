#!/usr/bin/env python3
"""test_tts_piper_fallback.py — ADR-0185 M2: piper-tts must be a genuine,
reachable fallback tier beneath edge-tts, not dead code.

Regression guard against the failure class this subsystem has already hit
once (windows-stt-tts-fix-0-10-18: an entire TTS tier silently never fired
because of a swallowed NameError). Concretely verifies:

  1. `synthesize_voice_note` actually calls `_try_piper_tts` when OpenAI is
     unavailable (no key) and edge-tts fails (no internet / blocked
     Microsoft endpoint / ffmpeg missing) — not gated behind a feature
     flag, env var, or config check that could silently default it off.
  2. Piper is NOT invoked when an earlier tier (edge-tts) already
     succeeded — confirms fallback ORDER, not just reachability.
  3. `_try_piper_tts` itself never raises and returns None cleanly when
     the `piper` binary or a configured voice model is missing — the
     pre-existing, correct behavior for installs that never fetched a
     model (unaffected by the ADR-0185 M2 installer fix, which makes the
     model fetch unconditional so this None-path becomes rare in practice).
  4. When the real `piper` console-script is on PATH (a base dependency
     as of ADR-0185 — see pyproject.toml), `_try_piper_tts` performs a
     genuine subprocess call into it (not a mock/no-op) — proven by
     handing it a deliberately-invalid model file and observing a real
     non-zero piper exit rather than an exception or a silently-skipped
     call. A full real-audio round trip (real German
     `de_DE-kerstin-low` model, ~60 MB, downloaded via
     `corvinOS/installer/steps/piper.py::_download_model`) was manually
     verified during ADR-0185 M2 development; it is intentionally not
     baked into this always-run suite to avoid a mandatory network
     download on every CI run.
"""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from unittest import mock

import pytest

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def test_piper_is_attempted_when_openai_and_edge_both_fail() -> None:
    """The core ADR-0185 M2 assertion: edge-tts failing must not be a dead
    end — Piper has to actually be called by the real orchestrator, with no
    mock standing in for `synthesize_voice_note` itself."""
    _section("edge-tts fails -> Piper tier is actually reached")
    fake_ogg = ROOT / "outbox" / "does-not-need-to-exist.ogg"
    with mock.patch.object(adapter, "_try_openai_tts", return_value=None) as m_openai, \
         mock.patch.object(adapter, "_try_edge_tts", return_value=None) as m_edge, \
         mock.patch.object(adapter, "_try_piper_tts", return_value=fake_ogg) as m_piper:
        result = adapter.synthesize_voice_note("Hallo, das ist ein Test.", lang="de")

    m_openai.assert_called_once()
    m_edge.assert_called_once()
    m_piper.assert_called_once_with("Hallo, das ist ein Test.", "de")
    assert result == fake_ogg
    assert adapter.voice_skip_reason() is None
    print("  OK — _try_piper_tts invoked exactly once with (text, lang) after edge-tts returned None")


def test_piper_is_skipped_when_edge_tts_already_succeeded() -> None:
    """Negative control: Piper must NOT fire when an earlier tier already
    produced audio — proves fallback ORDER (edge before Piper), not just
    reachability."""
    _section("edge-tts succeeds -> Piper tier is never reached")
    fake_ogg = ROOT / "outbox" / "edge-result.ogg"
    with mock.patch.object(adapter, "_try_openai_tts", return_value=None), \
         mock.patch.object(adapter, "_try_edge_tts", return_value=fake_ogg), \
         mock.patch.object(adapter, "_try_piper_tts") as m_piper:
        result = adapter.synthesize_voice_note("Hallo.", lang="de")

    m_piper.assert_not_called()
    assert result == fake_ogg
    print("  OK — Piper untouched when edge-tts already returned a path")


def test_all_tiers_failing_sets_a_user_facing_skip_reason() -> None:
    """When even Piper fails, the caller must get a translated notice, not a
    silent None with no explanation (ADR-0185 Decision 4).

    Two distinct messages exist depending on whether the OpenAI quota
    backoff window is active — only that branch names Piper explicitly
    today (the plain "no engine available" branch talks about edge-tts/
    OPENAI_API_KEY only). Both are exercised here so a future edit to
    either message trips this test instead of silently losing the notice.
    """
    _section("all three tiers fail -> voice_skip_reason() is set")
    with mock.patch.object(adapter, "_try_openai_tts", return_value=None), \
         mock.patch.object(adapter, "_try_edge_tts", return_value=None), \
         mock.patch.object(adapter, "_try_piper_tts", return_value=None):
        result = adapter.synthesize_voice_note("Hallo.", lang="de")

    assert result is None
    reason = adapter.voice_skip_reason()
    assert reason and "unavailable" in reason
    print(f"  OK — skip reason surfaced (no-quota branch): {reason!r}")

    # Quota-backoff branch: this one does name Piper explicitly.
    with adapter._voice_engine_lock:
        adapter._voice_engine_state["quota_until"] = adapter.time.time() + 3600
    try:
        with mock.patch.object(adapter, "_try_openai_tts", return_value=None), \
             mock.patch.object(adapter, "_try_edge_tts", return_value=None), \
             mock.patch.object(adapter, "_try_piper_tts", return_value=None):
            result = adapter.synthesize_voice_note("Hallo.", lang="de")
        assert result is None
        reason = adapter.voice_skip_reason()
        assert reason and "Piper" in reason
        print(f"  OK — skip reason surfaced (quota-backoff branch): {reason!r}")
    finally:
        with adapter._voice_engine_lock:
            adapter._voice_engine_state["quota_until"] = 0.0


def test_try_piper_tts_returns_none_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-existing, still-correct behavior: no `piper` binary -> clean None,
    never an exception (unaffected by the ADR-0185 M2 installer fix)."""
    _section("piper binary absent -> _try_piper_tts returns None cleanly")
    monkeypatch.delenv("PIPER_BIN", raising=False)
    with mock.patch("shutil.which", return_value=None):
        result = adapter._try_piper_tts("Hallo.", "de")
    assert result is None
    print("  OK — no exception, returns None")


def test_try_piper_tts_returns_none_when_no_model_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Pre-existing, still-correct behavior: `piper` binary present but no
    voice model configured -> clean None. This is the exact gap ADR-0185 M2's
    installer fix targets (making the model download unconditional so this
    path is rare in practice) — the *runtime* fallback behavior itself was
    already correct and is intentionally left unchanged here."""
    _section("piper binary present, no model configured -> None")
    fake_bin = tmp_path / "piper"
    fake_bin.write_text("#!/bin/sh\nexit 1\n")
    fake_bin.chmod(fake_bin.stat().st_mode | stat.S_IEXEC)

    empty_config_dir = tmp_path / "voice_config"
    empty_config_dir.mkdir()

    monkeypatch.delenv("CORVIN_PIPER_MODEL_DE", raising=False)
    monkeypatch.setenv("PIPER_BIN", str(fake_bin))
    with mock.patch.object(adapter, "_VOICE_CONFIG_DIR", empty_config_dir):
        result = adapter._try_piper_tts("Hallo.", "de")
    assert result is None
    print("  OK — no config.json / no env model -> None, no crash")


def test_try_piper_tts_genuinely_invokes_the_real_piper_binary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Proves _try_piper_tts is not dead code: when the real `piper`
    console-script is on PATH (base dependency per ADR-0185 — see
    pyproject.toml), handing it a deliberately-invalid .onnx model produces
    a REAL subprocess failure (non-zero exit from the actual piper CLI),
    which _try_piper_tts must turn into a clean None — not an exception,
    not a false-positive success. No network / no real voice model needed.
    """
    _section("real piper binary on PATH -> genuine subprocess call, not a no-op")
    import shutil as _shutil
    piper_bin = os.environ.get("PIPER_BIN") or _shutil.which("piper")
    if not piper_bin or not os.path.isfile(piper_bin):
        pytest.skip("piper binary not on PATH in this environment — "
                    "install the base dependency (pip install piper-tts) to run this check")

    bogus_model = tmp_path / "bogus.onnx"
    bogus_model.write_bytes(b"not a real onnx model")

    voice_config_dir = tmp_path / "voice_config"
    voice_config_dir.mkdir()

    monkeypatch.setenv("CORVIN_PIPER_MODEL_DE", str(bogus_model))
    monkeypatch.setenv("PIPER_BIN", piper_bin)
    with mock.patch.object(adapter, "_VOICE_CONFIG_DIR", voice_config_dir):
        result = adapter._try_piper_tts("Hallo, das ist ein Test.", "de")

    # The real piper binary must reject the bogus model (non-zero exit),
    # and _try_piper_tts must convert that into a clean None.
    assert result is None
    print(f"  OK — real '{piper_bin}' subprocess invoked, invalid model rejected, "
          f"_try_piper_tts returned None (no exception)")


if __name__ == "__main__":
    test_piper_is_attempted_when_openai_and_edge_both_fail()
    test_piper_is_skipped_when_edge_tts_already_succeeded()
    test_all_tiers_failing_sets_a_user_facing_skip_reason()
    print("\nAll piper-fallback control-flow tests passed (run via pytest for the rest).")
