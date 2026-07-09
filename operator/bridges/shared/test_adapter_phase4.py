#!/usr/bin/env python3
"""test_adapter_phase4.py — Tests for die Phase-4 Adapter-Aufräumarbeiten:

1. settings mtime-cache: load_settings() reads the file nur wenn mtime
   sich changed hat (otherwise ist der hot-path bei 1 Hz Polling teurer als needed).
2. in_flight TTL-Cleanup: ein abgestorbener Eintrag (Runner via SIGKILL/OOM
   weg) wed nach IN_FLIGHT_TTL automatically gedroppt.
3. chat_locks idle-Cleanup: unusede Locks werden nach CHAT_LOCK_IDLE_TTL
   gedroppt — verhindert unbegrenztes Lock-Wachstum auf Long-Run-daemons.
4. dup-Imports + import-re-in-function removed — Smoke: module loads sauber.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _fresh_adapter():
    for mod in list(sys.modules):
        if mod == "adapter":
            del sys.modules[mod]
    import adapter  # type: ignore
    return adapter


def test_settings_mtime_cache() -> None:
    _section("settings mtime cache")
    adapter = _fresh_adapter()
    tmp = Path(tempfile.mkdtemp(prefix="settings-cache-"))
    try:
        f = tmp / "settings.json"
        f.write_text('{"foo": "one"}')
        adapter.SETTINGS_FILE = f

        # Reset cache state.
        adapter._settings_cache = None
        adapter._settings_mtime = 0.0

        # 1st read: parses, populates cache.
        s1 = adapter.load_settings()
        assert s1["foo"] == "one"
        m1 = adapter._settings_mtime
        assert m1 > 0

        # Subsequent reads with unchanged mtime: must return SAME object
        # (identity, not just equality), proving no re-parse happened.
        s2 = adapter.load_settings()
        assert s1 is s2, "cache hit should return identity-same dict"
        print("PASS: cache hit returns same dict instance (no re-parse)")

        # mtime resolution on most filesystems = 1s; bump and verify reload.
        time.sleep(1.1)
        f.write_text('{"foo": "two"}')
        s3 = adapter.load_settings()
        assert s3["foo"] == "two", f"expected reload, got {s3}"
        assert adapter._settings_mtime > m1
        print("PASS: mtime change triggers reload")

        # Corrupt the file → keep last good cache.
        time.sleep(1.1)
        f.write_text('{not valid')
        s4 = adapter.load_settings()
        assert s4["foo"] == "two", "corrupt file should keep last good cache"
        print("PASS: corrupt file keeps last-good cache")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def test_in_flight_ttl_cleanup() -> None:
    _section("in_flight TTL cleanup")
    adapter = _fresh_adapter()
    # Reset module-level state.
    adapter._in_flight = {}
    adapter._chat_locks = {}
    adapter._chat_locks_last_used = {}
    adapter.IN_FLIGHT_TTL = 0.5  # short for the test

    # Entries are (submit-ts, runner-Future|None) since the 2026-07-10
    # duplicate-submit fix. A finished-runner Future stub:
    class _DoneFut:
        @staticmethod
        def done() -> bool:
            return True

    class _RunningFut:
        @staticmethod
        def done() -> bool:
            return False

    # Aged entry whose submit failed (no Future) → reaped.
    adapter._in_flight["dead-runner-msg"] = (time.time() - 5.0, None)
    # Aged entry whose runner finished but finally never popped → reaped.
    adapter._in_flight["done-runner-msg"] = (time.time() - 5.0, _DoneFut())
    # Fresh entry → retained.
    adapter._in_flight["live-runner-msg"] = (time.time(), None)
    # Aged entry with a STILL-RUNNING runner (long turn) → must be retained,
    # or the poll loop re-submits the same inbox file (duplicate execution).
    adapter._in_flight["long-turn-msg"] = (time.time() - 5.0, _RunningFut())

    removed = adapter._cleanup_in_flight()
    assert removed == 2, f"expected 2 stale dropped, got {removed}"
    assert "dead-runner-msg" not in adapter._in_flight
    assert "done-runner-msg" not in adapter._in_flight
    assert "live-runner-msg" in adapter._in_flight
    assert "long-turn-msg" in adapter._in_flight
    print("PASS: stale entries dropped, live + long-running entries retained")


def test_chat_locks_idle_cleanup() -> None:
    _section("chat_locks idle cleanup")
    adapter = _fresh_adapter()
    adapter._chat_locks = {}
    adapter._chat_locks_last_used = {}
    adapter.CHAT_LOCK_IDLE_TTL = 0.5

    # Touch 3 chats — the first one will go stale.
    lock_a = adapter._chat_lock_for("telegram:chatA")
    lock_b = adapter._chat_lock_for("discord:chatB")
    assert isinstance(lock_a, threading.Lock().__class__) or lock_a is not None

    # Mark one as stale.
    adapter._chat_locks_last_used["telegram:chatA"] = time.time() - 5.0
    # Hold lock_b — must NOT be removed even though we'll mark it idle below.
    lock_b.acquire()
    adapter._chat_locks_last_used["discord:chatB"] = time.time() - 5.0

    try:
        removed = adapter._cleanup_chat_locks()
    finally:
        lock_b.release()

    assert removed == 1, f"only the unheld stale lock should be dropped, got removed={removed}"
    assert "telegram:chatA" not in adapter._chat_locks
    assert "discord:chatB" in adapter._chat_locks, "held lock must be retained"
    print(f"PASS: idle unheld lock dropped, held lock retained (removed={removed})")

    # Touch the dropped chat again → fresh lock should appear.
    lock_a2 = adapter._chat_lock_for("telegram:chatA")
    assert lock_a2 is not None
    assert "telegram:chatA" in adapter._chat_locks
    print("PASS: re-acquire after cleanup recreates the lock")


def test_no_dup_imports() -> None:
    _section("dup imports removed")
    src = (ROOT / "adapter.py").read_text()
    # `import threading` should appear exactly once at module top.
    assert src.count("\nimport threading\n") == 1, \
        "import threading appears multiple times — Phase 4 cleanup regressed"
    # `import re` should appear exactly once at module top (was previously
    # so re-imported inside _strip_for_speech).
    assert src.count("\nimport re\n") == 1, \
        "import re appears multiple times — _strip_for_speech still has its inline import"
    # defaultdict was unused after switching _chat_locks to a plain dict.
    assert "from collections import defaultdict" not in src, \
        "defaultdict import is unused"
    print("PASS: import threading: single occurrence")
    print("PASS: import re: single occurrence")
    print("PASS: defaultdict import removed")


def main() -> int:
    test_settings_mtime_cache()
    test_in_flight_ttl_cleanup()
    test_chat_locks_idle_cleanup()
    test_no_dup_imports()
    print("\nALL CHECKS PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
