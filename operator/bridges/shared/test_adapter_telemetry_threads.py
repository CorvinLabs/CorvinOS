#!/usr/bin/env python3
"""test_adapter_telemetry_threads.py — bridge-side telemetry boot wiring.

Review findings (2026-07-20):

T2 (MEDIUM): the main inbox loop invoked run_upload_cycle() SYNCHRONOUSLY on
its ~1h cleanup interval — up to ~60s of network timeouts stalled inbox
polling and the SIGTERM drain while the comment claimed "Non-blocking".
The upload must run in a background daemon thread (start_upload_thread).

T3 (LOW): start_ping_thread() returns None by design, but the boot path
checked `if _ping_thread and _ping_thread.is_alive()` — every SUCCESSFUL
start was logged as "skipped (already running elsewhere or unavailable)".

T5 (LOW): bridge-only deployments started only the ping thread, never the
presence heartbeat — such instances were systematically missing from
online_now / online-geo. The same boot path must also start
corvin_console.aco.heartbeat.start_heartbeat_thread (soft-import, fail-soft).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import adapter  # type: ignore


def _fake_modules(tmp_path: Path) -> tuple[dict, Mock, Mock, Mock]:
    """Build fake corvin_console.aco.* / forge.paths modules for sys.modules."""
    hu_mod = types.ModuleType("corvin_console.aco.htrace_uploader")
    start_ping = Mock(return_value=None)
    start_upload = Mock(return_value=None)
    hu_mod.start_ping_thread = start_ping
    hu_mod.start_upload_thread = start_upload

    hb_mod = types.ModuleType("corvin_console.aco.heartbeat")
    start_hb = Mock(return_value=None)
    hb_mod.start_heartbeat_thread = start_hb

    fp_mod = types.ModuleType("forge.paths")
    fp_mod.corvin_home = lambda: tmp_path

    mods = {
        "corvin_console.aco.htrace_uploader": hu_mod,
        "corvin_console.aco.heartbeat": hb_mod,
        "forge.paths": fp_mod,
    }
    return mods, start_ping, start_upload, start_hb


def test_boot_starts_ping_upload_and_heartbeat_threads(tmp_path):
    """T2 + T5: the telemetry boot path must start all three daemon threads —
    ping, healing-trace upload (off the main loop), and presence heartbeat."""
    mods, start_ping, start_upload, start_hb = _fake_modules(tmp_path)
    with patch.dict(sys.modules, mods):
        adapter._start_telemetry_threads()

    start_ping.assert_called_once_with(tmp_path)
    start_upload.assert_called_once_with(tmp_path)
    start_hb.assert_called_once_with(tmp_path)


def test_successful_start_is_logged_as_success_not_skipped(tmp_path):
    """T3: a successful start must be logged as a success. The pre-fix check
    `if _ping_thread and _ping_thread.is_alive()` on a None return logged
    EVERY successful start as "skipped"."""
    mods, *_ = _fake_modules(tmp_path)
    lines: list[str] = []
    with (
        patch.dict(sys.modules, mods),
        patch.object(adapter, "log", lambda *a: lines.append(" ".join(map(str, a)))),
    ):
        adapter._start_telemetry_threads()

    assert any("started" in ln for ln in lines), (
        f"expected a success log line, got: {lines!r}"
    )
    assert not any("skipped" in ln for ln in lines), (
        f"success must not be logged as skipped: {lines!r}"
    )


def test_heartbeat_failure_is_fail_soft_and_ping_still_starts(tmp_path):
    """T5: the heartbeat is best-effort — a missing/broken heartbeat module
    must neither raise nor prevent the ping/upload threads from starting."""
    mods, start_ping, start_upload, _ = _fake_modules(tmp_path)
    # A heartbeat module WITHOUT start_heartbeat_thread → ImportError on the
    # from-import inside the boot path.
    mods["corvin_console.aco.heartbeat"] = types.ModuleType(
        "corvin_console.aco.heartbeat"
    )
    with patch.dict(sys.modules, mods):
        adapter._start_telemetry_threads()  # must not raise

    start_ping.assert_called_once_with(tmp_path)
    start_upload.assert_called_once_with(tmp_path)


def test_main_loop_no_longer_calls_upload_cycle_synchronously():
    """T2: the synchronous run_upload_cycle call must be gone from adapter.py
    entirely — the upload runs only via start_upload_thread's daemon thread."""
    source = (ROOT / "adapter.py").read_text(encoding="utf-8")
    assert "run_upload_cycle" not in source, (
        "adapter.py must not call run_upload_cycle synchronously — "
        "the healing-trace upload belongs in the background daemon thread"
    )
    assert "start_upload_thread" in source, (
        "adapter.py must start the background upload thread instead"
    )


if __name__ == "__main__":
    import pytest

    sys.exit(pytest.main([__file__, "-v"]))
