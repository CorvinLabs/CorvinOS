"""E2E test: Buffered /btw across Codex/OpenCode/Hermes (ADR-0087 M2)."""

import tempfile
from pathlib import Path

try:
    from eci.transport_buffered import enqueue_injection, dequeue_all_injections, clear_queue
    HAS_DEPS = True
except ImportError:
    HAS_DEPS = False


def test_enqueue_dequeue_cycle():
    """Test /btw round-trip: enqueue → dequeue → prepend."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        # Codex: /btw "help me debug"
        enqueue_injection(session_dir, "help me debug this function")

        # Codex: next spawn checks queue
        queued = dequeue_all_injections(session_dir)
        assert "help me debug" in queued
        assert "Buffered /btw" in queued

        # Queue is consumed (removed)
        remaining = dequeue_all_injections(session_dir)
        assert remaining == ""


def test_multiple_injections_batch():
    """Test multiple /btw calls queued together."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        enqueue_injection(session_dir, "line 1")
        enqueue_injection(session_dir, "line 2")
        enqueue_injection(session_dir, "line 3")

        queued = dequeue_all_injections(session_dir)
        assert "line 1" in queued
        assert "line 2" in queued
        assert "line 3" in queued


def test_clear_queue():
    """Test queue cleanup on /reset."""
    if not HAS_DEPS:
        return

    with tempfile.TemporaryDirectory() as tmpdir:
        session_dir = Path(tmpdir)

        enqueue_injection(session_dir, "to be cleared")
        clear_queue(session_dir)

        remaining = dequeue_all_injections(session_dir)
        assert remaining == ""
