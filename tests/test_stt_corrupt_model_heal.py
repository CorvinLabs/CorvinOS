"""Corrupt-model self-heal must be TRUNCATION-only and RATE-LIMITED (VOICE-F3).

The old self-heal renamed the ggml file to `.corrupt` and re-downloaded the
full model (~539 MB for medium) on ANY persistent load failure — a malloc
failure, an ABI/CPU mismatch, or a ggml version drift would re-fetch the whole
model on EVERY single voice note, unbounded. The fix:

  * heal ONLY a file that is implausibly small for its family (a truncated /
    aborted download — the only failure a re-download can repair);
  * cap heals to `_HEAL_MAX_HEALS` per `_HEAL_COOLDOWN_S` window so even a
    mis-detected truncation cannot loop downloads;
  * otherwise give up → STTProviderUnavailable (resolver falls through to the
    next provider) instead of re-downloading.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (_REPO, _REPO / "operator" / "voice" / "scripts"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from stt import local_whisper as lw  # type: ignore  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_heal_state():
    lw._heal_attempts.clear()
    yield
    lw._heal_attempts.clear()


def _write(model_file: Path, nbytes: int) -> None:
    model_file.write_bytes(b"\0" * nbytes)


# ── _looks_truncated ─────────────────────────────────────────────────────────


def test_truncated_file_is_detected(tmp_path):
    f = tmp_path / "ggml-medium-q5_0.bin"
    _write(f, 1024)  # 1 KiB — nowhere near medium's ~539 MB
    assert lw._looks_truncated(f, "medium-q5_0") is True


def test_full_size_file_is_not_truncated(tmp_path):
    f = tmp_path / "ggml-base-q5_1.bin"
    _write(f, 50 * 1024 * 1024)  # 50 MiB > base floor (40 MiB)
    assert lw._looks_truncated(f, "base-q5_1") is False


def test_unknown_family_never_truncated(tmp_path):
    f = tmp_path / "ggml-frobnicate.bin"
    _write(f, 1)
    assert lw._looks_truncated(f, "frobnicate") is False


# ── _plan_model_heal: the core VOICE-F3 fix ──────────────────────────────────


def test_truncated_download_heals_once(tmp_path):
    f = tmp_path / "ggml-medium-q5_0.bin"
    _write(f, 2048)  # truncated
    healable, detail = lw._plan_model_heal("medium-q5_0", f, now=1000.0)
    assert healable is True and detail == ""
    assert lw._heal_attempts["medium-q5_0"][0] == 1


def test_full_size_failure_never_re_downloads(tmp_path):
    """THE regression: a full-size file that still fails to load (malloc / ABI /
    version drift) must NOT be re-downloaded — heal must decline."""
    f = tmp_path / "ggml-medium-q5_0.bin"
    _write(f, 450 * 1024 * 1024)  # plausibly full (> 400 MiB floor)
    healable, detail = lw._plan_model_heal("medium-q5_0", f, now=1000.0)
    assert healable is False
    assert "full-size" in detail
    assert "medium-q5_0" not in lw._heal_attempts  # nothing recorded


def test_second_heal_within_window_is_refused(tmp_path):
    """Even a genuinely truncated file may be re-downloaded at most once per
    cooldown window — a still-truncated file after the first heal must not loop."""
    f = tmp_path / "ggml-small-q5_1.bin"
    _write(f, 4096)  # truncated
    ok1, _ = lw._plan_model_heal("small-q5_1", f, now=1000.0)
    assert ok1 is True
    ok2, detail = lw._plan_model_heal("small-q5_1", f, now=1000.0 + 5)
    assert ok2 is False
    assert "already re-downloaded" in detail


def test_heal_allowed_again_after_cooldown(tmp_path):
    f = tmp_path / "ggml-small-q5_1.bin"
    _write(f, 4096)
    ok1, _ = lw._plan_model_heal("small-q5_1", f, now=1000.0)
    assert ok1 is True
    later = 1000.0 + lw._HEAL_COOLDOWN_S + 1
    ok2, _ = lw._plan_model_heal("small-q5_1", f, now=later)
    assert ok2 is True
    assert lw._heal_attempts["small-q5_1"][0] == 1  # counter reset, then +1


def test_absent_file_does_not_heal(tmp_path):
    f = tmp_path / "ggml-medium-q5_0.bin"  # never created
    healable, detail = lw._plan_model_heal("medium-q5_0", f, now=1000.0)
    assert healable is False
    assert "absent" in detail
