"""Tests for robust TTS timeout logic — long voice summaries don't fail silently.

ADR feedback (2026-07-28): Voice must be robust on long summaries/recaps.
When `voice_synthesis_unavailable` appears after "hold space to speak",
it means TTS slots were exhausted or synthesis timeout was exceeded —
a 204 (no content) response from slot overflow or 20s wait timeout.

Solution: Dynamic timeout based on text length. Longer texts get more time.
"""
import pytest


def test_compute_tts_wait_timeout_short_text():
    """Short text (≤400 chars) gets small extra: 20 + (400/500)*2 = 21.6."""
    from corvin_console.routes.voice import _compute_tts_wait_timeout

    # 400 chars = ~5-8s synthesis; base 20s + 1.6s extra = 21.6s
    timeout = _compute_tts_wait_timeout(400)
    assert abs(timeout - 21.6) < 0.01


def test_compute_tts_wait_timeout_medium_text():
    """Medium text (2000 chars) gets +8s: base 20 + (2000/500)*2 = 28s."""
    from corvin_console.routes.voice import _compute_tts_wait_timeout

    timeout = _compute_tts_wait_timeout(2000)
    # 2000 / 500 * 2 + 20 = 8 + 20 = 28
    assert timeout == 28.0


def test_compute_tts_wait_timeout_long_text():
    """Long text (4000 chars) gets +16s but clamped to max 45s."""
    from corvin_console.routes.voice import _compute_tts_wait_timeout

    timeout = _compute_tts_wait_timeout(4000)
    # 4000 / 500 * 2 + 20 = 16 + 20 = 36 (under max 45)
    assert timeout == 36.0


def test_compute_tts_wait_timeout_clamped_at_max():
    """Very long text (10000+ chars) clamped to max 45s."""
    from corvin_console.routes.voice import _compute_tts_wait_timeout

    # 10000 / 500 * 2 + 20 = 40 + 20 = 60 → clamped to 45
    timeout = _compute_tts_wait_timeout(10000)
    assert timeout == 45.0


def test_compute_tts_wait_timeout_zero_text():
    """Empty text uses base timeout."""
    from corvin_console.routes.voice import _compute_tts_wait_timeout

    timeout = _compute_tts_wait_timeout(0)
    assert timeout == 20.0  # base only


def test_tts_slot_wait_max_and_base_defined():
    """Constants are defined and sensible."""
    from corvin_console.routes import voice

    assert voice._TTS_SLOT_WAIT_BASE_S == 20.0
    assert voice._TTS_SLOT_WAIT_MAX_S == 45.0
    assert voice._TTS_MAX_CONCURRENCY == 4
    # Ensure we didn't accidentally leave the old _TTS_SLOT_WAIT_S
    assert not hasattr(voice, '_TTS_SLOT_WAIT_S')


def test_tts_concurrency_sensible():
    """TTS concurrency is bounded to avoid thread pool starvation."""
    from corvin_console.routes import voice

    # 4 concurrent TTS tasks is reasonable for default 40-thread pool
    assert voice._TTS_MAX_CONCURRENCY >= 2
    assert voice._TTS_MAX_CONCURRENCY <= 10
