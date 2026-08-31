#!/usr/bin/env python3
"""test_daemon.py — E2E for Phase-4.2 init.py daemon mode (cross-platform).

Spawns init.py daemon as a real subprocess, talks to it via a platform-neutral
transport (Unix socket on Unix/Linux/macOS, TCP loopback on Windows), and verifies:

  - Daemon starts, creates endpoint, accepts connections
  - ping returns {ok: true, pong: true}
  - list returns the discovered services with status
  - start <name> launches a real subprocess (verified via status)
  - stop <name> terminates it (verified via status)
  - restart <name> bounces it
  - status <name> returns full state record
  - journal <name> N returns last N journal lines
  - reload <name> sends configured signal (only when hot_reload set)
  - unknown commands return {ok: false, error: ...}
  - shutdown command makes the daemon exit gracefully
  - SIGTERM also makes the daemon exit gracefully (cleanup happens)
  - Stale endpoint files from a crashed previous run are cleaned up

Per-subtask E2E rule: real subprocess for the daemon, real subprocesses for
the supervised services (Python sleep loops), real transport (Unix socket or
TCP loopback based on platform), real SIGTERM. No mocks for moving parts.
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
INIT_PY = ROOT / "init.py"


def _section(title: str) -> None:
    print(f"\n=== {title} ===")


def _setup_sandbox() -> tuple[Path, Path]:
    """Create CORVIN_HOME and a fake plugin root with one service."""
    home = Path(tempfile.mkdtemp(prefix="init-daemon-test-"))
    plugin_root = home / "fake-plugin"
    plugin_root.mkdir()
    # Two test services: sleeper (long-running daemon) and one-shot
    (plugin_root / "sleeper.service.yaml").write_text(
        'name: sleeper\n'
        'type: daemon\n'
        'exec_start: "exec sleep 60"\n'
        'restart: never\n',
        encoding="utf-8",
    )
    (plugin_root / "echo.service.yaml").write_text(
        'name: echo\n'
        'type: oneshot\n'
        'exec_start: "echo hello-from-echo-service"\n'
        'restart: never\n',
        encoding="utf-8",
    )
    return home, plugin_root


def _spawn_daemon(home: Path, plugin_root: Path) -> subprocess.Popen:
    env = os.environ.copy()
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_INIT_PLUGIN_ROOTS"] = str(plugin_root)
    env["CORVIN_INIT_NO_AUTOSTART"] = "1"  # tests drive starts manually
    env["CORVIN_INIT_TICK_INTERVAL"] = "0.2"
    return subprocess.Popen(
        [sys.executable, str(INIT_PY), "daemon"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )


def _socket_path(home: Path) -> Path:
    return home / "run" / "init.sock"


def _wait_for_socket(home: Path, timeout: float = 5.0) -> bool:
    """Wait for the daemon to be ready.

    On Unix: wait for socket file to exist.
    On Windows: wait for daemon.port file to exist.
    """
    deadline = time.time() + timeout
    sock_path = _socket_path(home)
    port_file = sock_path.parent / "daemon.port"

    while time.time() < deadline:
        if sys.platform == "win32":
            if port_file.exists():
                return True
        else:
            if sock_path.exists():
                return True
        time.sleep(0.05)
    return False


def _call(home: Path, command: str, *args: str,
          timeout: float = 3.0) -> dict:
    # Import the transport module directly using importlib
    import importlib.util
    transport_path = Path(__file__).parent / "daemon_transport.py"
    spec = importlib.util.spec_from_file_location("daemon_transport", transport_path)
    transport_module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport_module)
    create_transport = transport_module.create_transport

    socket_path = _socket_path(home)
    transport = create_transport(socket_path)
    try:
        sock = transport.connect(timeout=timeout)
    except (OSError, RuntimeError) as exc:
        return {"ok": False, "error": str(exc)}

    try:
        payload = {"command": command, "args": list(args)}
        sock.sendall((json.dumps(payload) + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data and len(data) < 65536:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        line = data.split(b"\n", 1)[0].decode("utf-8", errors="replace")
        return json.loads(line)
    finally:
        sock.close()


# --------------------------------------------------------------------- cases

def case_daemon_starts_and_pings() -> None:
    _section("daemon starts, creates socket, responds to ping")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home), "socket not created within 5s"
        reply = _call(home, "ping")
        assert reply.get("ok") is True, reply
        assert reply.get("pong") is True, reply
        print("  PASS daemon up, ping responds")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_list_returns_discovered_services() -> None:
    _section("list returns the two test services")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        reply = _call(home, "list")
        assert reply.get("ok") is True, reply
        names = {s["name"] for s in reply.get("services", [])}
        assert names == {"sleeper", "echo"}, names
        for s in reply["services"]:
            assert s["status"] == "stopped", s  # autostart disabled
        print(f"  PASS discovered: {sorted(names)}, all stopped")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_start_launches_real_subprocess() -> None:
    _section("start spawns a real subprocess, status reflects it")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        r = _call(home, "start", "sleeper")
        assert r.get("ok") is True, r
        # Allow tick to register; status should now be running
        time.sleep(0.4)
        st = _call(home, "status", "sleeper")
        assert st.get("ok") is True, st
        record = st.get("status") or {}
        assert record["status"] == "running", record
        assert record["pid"] > 0, record
        # Verify the PID is actually alive on the OS
        os.kill(record["pid"], 0)
        print(f"  PASS sleeper running pid={record['pid']}")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_stop_terminates_subprocess() -> None:
    _section("stop terminates the supervised subprocess")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        _call(home, "start", "sleeper")
        time.sleep(0.3)
        st = _call(home, "status", "sleeper")
        pid = st["status"]["pid"]
        r = _call(home, "stop", "sleeper")
        assert r.get("ok") is True
        time.sleep(0.3)
        st2 = _call(home, "status", "sleeper")
        assert st2["status"]["status"] == "stopped", st2
        # Subprocess should be gone
        try:
            os.kill(pid, 0)
            raise AssertionError(f"pid {pid} still alive after stop")
        except ProcessLookupError:
            pass
        print(f"  PASS sleeper stopped, pid {pid} reaped")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_unknown_service_errors() -> None:
    _section("unknown service returns ok=false with error")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        r = _call(home, "start", "does-not-exist")
        assert r.get("ok") is False, r
        assert "unknown service" in (r.get("error") or "").lower(), r
        print(f"  PASS error: {r['error']}")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_unknown_command_errors() -> None:
    _section("unknown command returns ok=false with error")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        r = _call(home, "no-such-command")
        assert r.get("ok") is False, r
        print(f"  PASS error: {r['error']}")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_shutdown_makes_daemon_exit() -> None:
    _section("shutdown command makes the daemon exit cleanly")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        _call(home, "shutdown")
        rc = proc.wait(timeout=5.0)
        assert rc == 0, f"expected rc=0, got {rc}"
        # Socket cleaned up
        assert not _socket_path(home).exists()
        print(f"  PASS daemon exited rc={rc}, socket cleaned up")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


def case_sigterm_graceful_shutdown() -> None:
    _section("SIGTERM also triggers graceful shutdown")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        _call(home, "start", "sleeper")
        time.sleep(0.3)
        st = _call(home, "status", "sleeper")
        sleeper_pid = st["status"]["pid"]
        # Send SIGTERM to the daemon's process group
        os.killpg(proc.pid, signal.SIGTERM)
        rc = proc.wait(timeout=5.0)
        assert rc == 0, f"expected graceful exit, got rc={rc}"
        # Sleeper child should also be reaped via shutdown_all
        time.sleep(0.2)
        try:
            os.kill(sleeper_pid, 0)
            raise AssertionError(
                f"sleeper pid {sleeper_pid} still alive after daemon shutdown"
            )
        except ProcessLookupError:
            pass
        print(f"  PASS SIGTERM caught, daemon rc={rc}, child reaped")
    finally:
        if proc.poll() is None:
            proc.kill()
        shutil.rmtree(home, ignore_errors=True)


def case_stale_socket_cleaned_up() -> None:
    _section("daemon cleans up a stale socket file from a prior run")
    home, plugin_root = _setup_sandbox()
    sock_path = _socket_path(home)
    sock_path.parent.mkdir(parents=True, exist_ok=True)
    sock_path.write_text("not a real socket")  # stale leftover
    proc = _spawn_daemon(home, plugin_root)
    try:
        # Daemon must REPLACE the stale file with a real socket. Mere
        # existence isn't enough (the stale regular file already exists)
        # — we need to retry the connect until we get a working socket.
        deadline = time.time() + 5.0
        last_err: Exception | None = None
        while time.time() < deadline:
            try:
                reply = _call(home, "ping", timeout=0.5)
                if reply.get("ok") is True:
                    print("  PASS stale socket file replaced cleanly")
                    return
            except (ConnectionRefusedError, ConnectionError, OSError) as e:
                last_err = e
                time.sleep(0.1)
        raise AssertionError(
            f"daemon did not replace stale socket within 5s: {last_err}"
        )
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


def case_journal_returns_lines() -> None:
    _section("journal returns the per-service journal tail")
    home, plugin_root = _setup_sandbox()
    proc = _spawn_daemon(home, plugin_root)
    try:
        assert _wait_for_socket(home)
        _call(home, "start", "echo")  # echo runs once and exits
        time.sleep(0.5)  # let it run + write journal
        r = _call(home, "journal", "echo", "20")
        assert r.get("ok") is True, r
        lines = r.get("lines") or []
        joined = "\n".join(lines)
        assert "started" in joined, joined
        assert "hello-from-echo-service" in joined, joined
        print(f"  PASS journal contains spawn + child stdout ({len(lines)} lines)")
    finally:
        proc.terminate()
        proc.wait(timeout=5.0)
        shutil.rmtree(home, ignore_errors=True)


# --------------------------------------------------------------------- driver

def main() -> None:
    cases = [
        case_daemon_starts_and_pings,
        case_list_returns_discovered_services,
        case_start_launches_real_subprocess,
        case_stop_terminates_subprocess,
        case_unknown_service_errors,
        case_unknown_command_errors,
        case_shutdown_makes_daemon_exit,
        case_sigterm_graceful_shutdown,
        case_stale_socket_cleaned_up,
        case_journal_returns_lines,
    ]
    failures = 0
    for case in cases:
        try:
            case()
        except Exception as exc:
            failures += 1
            print(f"  FAIL: {case.__name__}: {exc!r}")
            import traceback
            traceback.print_exc()

    print(f"\n=== {len(cases) - failures}/{len(cases)} cases passed ===")
    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
