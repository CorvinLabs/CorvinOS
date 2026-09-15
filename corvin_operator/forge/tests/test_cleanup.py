"""E2E: forge_cleanup prunes old dirs, leaves young ones."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "operator" / "forge" / "scripts" / "forge_cleanup.py"

PASS = 0; FAIL = 0


def t(label, ok, *, detail=""):
    global PASS, FAIL
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{(' — '+detail) if detail else ''}")
    if ok: PASS += 1
    else: FAIL += 1


def _set_old_mtime(path: Path, age_seconds: float):
    """Set mtime to (now - age_seconds)."""
    target = time.time() - age_seconds
    os.utime(path, (target, target))


def test_tasks_prune_old_keep_young():
    print("\n[tasks cleanup — old gone, young stays]")
    with tempfile.TemporaryDirectory() as td:
        # The script scans both /tmp/.corvin/tasks (canonical) and
        # /tmp/.corvinOS/tasks (Phase 1 strangler-fig legacy fallback).
        # We synthesize task dirs under the canonical root and verify
        # the cleanup picks them up. CAVEAT: this writes to the real
        # /tmp; use a UUID-prefix to avoid clashing with anything else.
        import uuid
        prefix = f"test-{uuid.uuid4().hex[:8]}"
        tasks_root = Path("/tmp/.corvin/tasks")
        tasks_root.mkdir(parents=True, exist_ok=True)
        old_dir = tasks_root / f"{prefix}-old"
        young_dir = tasks_root / f"{prefix}-young"
        old_dir.mkdir(); young_dir.mkdir()
        # Simulate 2h-old vs 1min-old
        _set_old_mtime(old_dir, 7200)
        _set_old_mtime(young_dir, 60)

        # Run cleanup with default 1h TTL
        r = subprocess.run(
            [sys.executable, str(SCRIPT), "tasks", "--ttl-hours", "1"],
            capture_output=True, text=True, check=False,
        )
        t("script exit 0", r.returncode == 0,
          detail=f"stdout={r.stdout!r} stderr={r.stderr!r}")
        t("old dir gone", not old_dir.exists())
        t("young dir kept", young_dir.exists())

        # Cleanup test artefacts
        for p in (old_dir, young_dir):
            if p.exists(): p.rmdir()


def test_sessions_dry_run():
    print("\n[sessions cleanup — dry-run lists, doesn't rm]")
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        sess_root = Path(td) / "sessions"
        old_chan = sess_root / "discord:test-old"
        young_chan = sess_root / "discord:test-young"
        old_chan.mkdir(parents=True); young_chan.mkdir(parents=True)
        _set_old_mtime(old_chan, 86400 * 60)   # 60d
        _set_old_mtime(young_chan, 86400 * 1)  # 1d

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "--dry-run", "sessions",
             "--ttl-days", "30"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("dry-run exit 0", r.returncode == 0,
          detail=f"stderr={r.stderr!r}")
        t("WOULD-RM mentioned for old chan",
          "WOULD-RM" in r.stdout and "test-old" in r.stdout)
        t("old chan still exists (dry run)", old_chan.exists())
        t("young chan still exists", young_chan.exists())
        os.environ.pop("CORVIN_HOME", None)


def test_sessions_actual_rm():
    print("\n[sessions cleanup — real rm of old]")
    with tempfile.TemporaryDirectory() as td:
        os.environ["CORVIN_HOME"] = td
        sess_root = Path(td) / "sessions"
        old_chan = sess_root / "discord:test-real"
        young_chan = sess_root / "discord:test-young"
        old_chan.mkdir(parents=True); young_chan.mkdir(parents=True)
        # Add a file so we know rmtree works
        (old_chan / "tool.json").write_text("{}")
        _set_old_mtime(old_chan, 86400 * 60)
        _set_old_mtime(young_chan, 86400 * 1)

        r = subprocess.run(
            [sys.executable, str(SCRIPT), "sessions", "--ttl-days", "30"],
            capture_output=True, text=True, check=False,
            env={**os.environ, "CORVIN_HOME": td},
        )
        t("exit 0", r.returncode == 0)
        t("old chan removed", not old_chan.exists())
        t("young chan kept", young_chan.exists())
        os.environ.pop("CORVIN_HOME", None)


def main() -> int:
    test_tasks_prune_old_keep_young()
    test_sessions_dry_run()
    test_sessions_actual_rm()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
