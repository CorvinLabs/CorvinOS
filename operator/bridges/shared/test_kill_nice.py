#!/usr/bin/env python3
"""test_kill_nice.py — E2E for Phase-4.1 /kill + /nice.

Drives the kill + nice subcommands of phase3_cli.py against a real
subprocess (Python sleep loop registered in process_table) and
verifies:

  - /kill <session_id> sends SIGTERM to the process group, the
    subprocess terminates, the process_table reflects the change
  - /kill -9 <session_id> sends SIGKILL
  - /kill of an unknown session_id reports a clear error
  - /kill of an already-exited session reports its exit_code
  - /kill when the pid is gone but the registry says running:
    deregisters and reports
  - /nice <session_id> <±N> updates the registry record
  - /nice with out-of-range value rejected
  - /nice on unknown session rejected

Per-subtask E2E rule from CLAUDE.md: real subprocess, real
process_table file, real OS-level signals, no mocks.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

CLI = ROOT / "phase3_cli.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _setup_home() -> Path:
    home = Path(tempfile.mkdtemp(prefix="kill-test-"))
    return home


def _spawn_real_subprocess(home: Path, session_id: str,
                            chat_key: str = "discord:1") -> subprocess.Popen:
    """Spawn a real subprocess (sleep loop) and register it in
    process_table just like the adapter would."""
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    # 60s sleep — long enough that the test races can finish; the
    # finalizer below kills any survivor.
    proc = subprocess.Popen(
        ["python3", "-c", "import time; time.sleep(60)"],
        env=env, start_new_session=True,
    )
    # Register it via the real process_table API. Need a fresh import
    # against the test home.
    os.environ["CORVIN_HOME"] = str(home)
    sys.modules.pop("process_table", None)
    sys.modules.pop("paths", None)
    import process_table  # type: ignore
    process_table.register_session(
        session_id, chat_key=chat_key, persona="coder",
        pid=proc.pid,
    )
    return proc


def _run_cli(*args: str, home: Path) -> subprocess.CompletedProcess:
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        env=env, capture_output=True, text=True,
    )


# --------------------------------------------------------------------- cases

def case_kill_sigterm_real_subprocess(home: Path) -> None:
    _section("kill SIGTERMs the real process group")
    proc = _spawn_real_subprocess(home, "s_kill_term")
    try:
        # Verify subprocess is alive before kill
        assert proc.poll() is None, "subprocess should be running"
        r = _run_cli("kill", "s_kill_term", home=home)
        assert r.returncode == 0, f"stderr={r.stderr}"
        assert "SIGTERM" in r.stdout, r.stdout
        # Wait for subprocess to actually exit
        rc = proc.wait(timeout=5.0)
        assert rc != 0 or rc == -signal.SIGTERM, (
            f"expected non-zero exit or SIGTERM, got {rc}"
        )
        print(f"  PASS subprocess terminated (rc={rc}); cli output: {r.stdout.strip()}")
    finally:
        if proc.poll() is None:
            proc.kill()


def case_kill_dash_9_sigkill(home: Path) -> None:
    _section("kill -9 SIGKILLs the real process group")
    proc = _spawn_real_subprocess(home, "s_kill_9")
    try:
        r = _run_cli("kill", "-9", "s_kill_9", home=home)
        assert r.returncode == 0, f"stderr={r.stderr}"
        assert "SIGKILL" in r.stdout, r.stdout
        rc = proc.wait(timeout=5.0)
        assert rc < 0, f"SIGKILL produces negative rc, got {rc}"
        # rc == -9 on Linux
        print(f"  PASS subprocess SIGKILLed (rc={rc})")
    finally:
        if proc.poll() is None:
            proc.kill()


def case_kill_unknown_session_errors(home: Path) -> None:
    _section("kill of unknown session_id is a clear error")
    sys.modules.pop("process_table", None)
    r = _run_cli("kill", "s_does_not_exist", home=home)
    assert r.returncode == 1, r.stdout
    assert "unknown session" in r.stderr, r.stderr
    print(f"  PASS clear error: {r.stderr.strip()}")


def case_kill_already_exited_reports_state(home: Path) -> None:
    _section("kill of already-exited session reports its exit_code")
    proc = _spawn_real_subprocess(home, "s_already_exited")
    try:
        # Kill it externally first, then mark as exited
        proc.terminate()
        proc.wait(timeout=3.0)
        # Update the registry to reflect the exit
        sys.modules.pop("process_table", None)
        os.environ["CORVIN_HOME"] = str(home)
        import process_table  # type: ignore
        process_table.deregister_session(
            "s_already_exited", exit_reason="ok", keep=True,
        )
        r = _run_cli("kill", "s_already_exited", home=home)
        assert r.returncode == 1
        assert "already" in r.stderr, r.stderr
        print(f"  PASS clear error: {r.stderr.strip()}")
    finally:
        if proc.poll() is None:
            proc.kill()


def case_kill_when_pid_already_dead(home: Path) -> None:
    _section("kill when pid already gone updates registry")
    proc = _spawn_real_subprocess(home, "s_pid_gone")
    try:
        # Kill the process directly (bypassing CLI), but DON'T update
        # registry — simulates the case where the adapter hasn't yet
        # noticed the exit.
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(timeout=3.0)
        # Now CLI kill will hit ProcessLookupError
        r = _run_cli("kill", "s_pid_gone", home=home)
        assert r.returncode == 1
        assert "already gone" in r.stderr, r.stderr
        # Registry should now show the session as killed
        sys.modules.pop("process_table", None)
        os.environ["CORVIN_HOME"] = str(home)
        import process_table  # type: ignore
        rec = process_table.get_session("s_pid_gone")
        assert rec is not None
        assert rec["status"] == "killed", rec
        print(f"  PASS registry updated: status={rec['status']}")
    finally:
        if proc.poll() is None:
            proc.kill()


def case_nice_updates_registry(home: Path) -> None:
    _section("nice updates the registry record")
    proc = _spawn_real_subprocess(home, "s_nice")
    try:
        r = _run_cli("nice", "s_nice", "5", home=home)
        assert r.returncode == 0, r.stderr
        assert "nice=5" in r.stdout, r.stdout
        sys.modules.pop("process_table", None)
        os.environ["CORVIN_HOME"] = str(home)
        import process_table  # type: ignore
        rec = process_table.get_session("s_nice")
        assert rec["nice"] == 5, rec
        print(f"  PASS nice updated: {rec['nice']}")

        # Negative also works
        r = _run_cli("nice", "s_nice", "-3", home=home)
        assert r.returncode == 0
        sys.modules.pop("process_table", None)
        import process_table  # type: ignore
        rec = process_table.get_session("s_nice")
        assert rec["nice"] == -3, rec
        print(f"  PASS negative nice updated: {rec['nice']}")
    finally:
        proc.terminate()
        proc.wait(timeout=3.0)


def case_nice_out_of_range_rejected(home: Path) -> None:
    _section("nice out of range [-20, 19] rejected")
    proc = _spawn_real_subprocess(home, "s_nice_oor")
    try:
        for bad in ("-21", "20", "100", "abc"):
            r = _run_cli("nice", "s_nice_oor", bad, home=home)
            assert r.returncode == 1, (bad, r.stdout)
            print(f"  PASS rejected nice={bad}: {r.stderr.strip()}")
    finally:
        proc.terminate()
        proc.wait(timeout=3.0)


def case_nice_unknown_session_rejected(home: Path) -> None:
    _section("nice on unknown session rejected")
    sys.modules.pop("process_table", None)
    r = _run_cli("nice", "s_does_not_exist", "5", home=home)
    assert r.returncode == 1, r.stdout
    assert "unknown session" in r.stderr, r.stderr
    print(f"  PASS clear error: {r.stderr.strip()}")


# --------------------------------------------------------------------- driver

def main() -> None:
    saved_env = os.environ.get("CORVIN_HOME")
    cases = [
        case_kill_sigterm_real_subprocess,
        case_kill_dash_9_sigkill,
        case_kill_unknown_session_errors,
        case_kill_already_exited_reports_state,
        case_kill_when_pid_already_dead,
        case_nice_updates_registry,
        case_nice_out_of_range_rejected,
        case_nice_unknown_session_rejected,
    ]
    failures = 0
    for case in cases:
        home = _setup_home()
        try:
            case(home)
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()
        finally:
            shutil.rmtree(home, ignore_errors=True)

    if saved_env is None:
        os.environ.pop("CORVIN_HOME", None)
    else:
        os.environ["CORVIN_HOME"] = saved_env

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
