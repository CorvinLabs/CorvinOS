"""E2E for scripts/diagnostics/claude_strace_wrapper.py over real process boundaries.

Linux-only: the wrapper drives strace. Every run uses a sandboxed CORVIN_HOME,
XDG_CONFIG_HOME and HOME, an absolute CORVIN_AUDIT_ANCHOR_KEY, and no FORGE_ROOT.
The stand-in ``claude`` is a bash script; the wrapper is started through
``CORVIN_CLAUDE_BIN`` exactly as ``helper_model.resolve_claude_bin`` honours it.
"""

import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"), reason="strace wrapper is Linux-only")

REPO = Path(__file__).resolve().parents[2]
WRAPPER = REPO / "scripts" / "diagnostics" / "claude_strace_wrapper.py"


def _without_pid_line(text):
    """The fake prints its own PID, which differs per run; drop that line."""
    return [l for l in text.splitlines() if not l.startswith("fake-pid=")]


FAKE_CLAUDE = r"""#!/bin/bash
# Stand-in for the claude CLI. FAKE_MODE=exit: print, wait FAKE_DELAY, exit 255 (= -1).
# FAKE_MODE=sigterm: start a child, SIGTERM it after FAKE_DELAY, report its status.
delay="${FAKE_DELAY:-3}"
echo "fake-pid=$$"
echo "fake-start"
read -r line
echo "stdin:$line"
if [ "${FAKE_MODE:-exit}" = "sigterm" ]; then
  sleep 600 &
  child=$!
  sleep "$delay"
  kill -TERM "$child"
  wait "$child"
  echo "fake-child-rc=$?"
  exit 0
fi
sleep "$delay"
echo "fake-after-delay"
exit 255
"""


@pytest.fixture()
def sandbox(tmp_path):
    home = tmp_path / "home"
    corvin_home = tmp_path / "corvin_home"
    xdg = tmp_path / "xdg"
    for d in (home, corvin_home, xdg):
        d.mkdir()
    anchor = tmp_path / "anchor.key"
    anchor.write_bytes(os.urandom(32))
    fake = tmp_path / "claude-fake"
    fake.write_text(FAKE_CLAUDE)
    fake.chmod(0o755)
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(home),
        "XDG_CONFIG_HOME": str(xdg),
        "CORVIN_HOME": str(corvin_home),
        "CORVIN_AUDIT_ANCHOR_KEY": str(anchor.resolve()),
        "CORVIN_CLAUDE_BIN": str(WRAPPER),
        "CORVIN_CLAUDE_REAL_BIN": str(fake),
        "FAKE_DELAY": "3",
        "FAKE_MODE": "exit",
    }
    return {"tmp": tmp_path, "env": env, "fake": fake, "log_dir": corvin_home / "diagnostics" / "strace"}


def _run_direct(sb, mode, delay, stdin_text="ping\n"):
    env = dict(sb["env"], FAKE_MODE=mode, FAKE_DELAY=str(delay))
    return subprocess.run(
        [str(sb["fake"]), "-p", "x"],
        input=stdin_text.encode(), capture_output=True, env=env, timeout=60,
    )


def _run_wrapped(sb, mode, delay, stdin_text="ping\n", extra_env=None):
    env = dict(sb["env"], FAKE_MODE=mode, FAKE_DELAY=str(delay), **(extra_env or {}))
    # Resolve the way helper_model does: CORVIN_CLAUDE_BIN is the binary.
    binary = env["CORVIN_CLAUDE_BIN"]
    return subprocess.run(
        [binary, "-p", "x"],
        input=stdin_text.encode(), capture_output=True, env=env, timeout=60,
    )


def _logs(sb):
    files = sorted(sb["log_dir"].glob("*"))
    strace = [f for f in files if f.name.endswith(".strace.log")]
    wrapper = [f for f in files if f.name.endswith(".wrapper.log")]
    return strace, wrapper


def test_resolution_pins_wrapper_via_helper_model(sandbox):
    """The real resolver returns the wrapper for an absolute CORVIN_CLAUDE_BIN pin."""
    env = dict(os.environ, CORVIN_CLAUDE_BIN=str(WRAPPER), PYTHONPATH=str(REPO))
    res = subprocess.run(
        [sys.executable, "-c",
         "from corvin_operator.bridges.shared.helper_model import resolve_claude_bin as r; print(r())"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=60,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == str(WRAPPER)


def test_exit_code_stdout_and_timing_identical_and_unbuffered(sandbox):
    direct = _run_direct(sandbox, "exit", 3)
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [sandbox["env"]["CORVIN_CLAUDE_BIN"], "-p", "x"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=dict(sandbox["env"], FAKE_MODE="exit", FAKE_DELAY="3"),
    )
    proc.stdin.write(b"ping\n")
    proc.stdin.close()
    arrivals = []
    for raw in proc.stdout:  # line-by-line, as the console reads the stream
        arrivals.append((time.monotonic() - t0, raw.decode().rstrip("\n")))
    proc.wait(timeout=60)
    stderr = proc.stderr.read().decode()

    assert proc.returncode == direct.returncode == 255
    wrapped_lines = [l for _, l in arrivals]
    assert _without_pid_line(direct.stdout.decode()) == [l for l in wrapped_lines if not l.startswith("fake-pid=")]
    assert [l for l in wrapped_lines if not l.startswith("fake-pid=")] == [
        "fake-start", "stdin:ping", "fake-after-delay"]
    # Unbuffered: 'fake-start' reaches the reader well before 'fake-after-delay' (3 s later).
    gap = arrivals[-1][0] - arrivals[1][0]
    assert gap >= 2.4, f"stdout was buffered; gap={gap:.2f}s arrivals={arrivals}"
    assert stderr == direct.stderr.decode() == ""
    strace_logs, wrapper_logs = _logs(sandbox)
    assert len(strace_logs) == 1 and len(wrapper_logs) == 1
    for f in strace_logs + wrapper_logs:
        assert oct(f.stat().st_mode & 0o777) == "0o600"
        assert not str(f).startswith(str(sandbox["tmp"] / "corvin_home" / "tenants"))


def test_sigterm_child_kill_and_exit_events_logged_with_sender_pid(sandbox):
    direct = _run_direct(sandbox, "sigterm", 3)
    res = _run_wrapped(sandbox, "sigterm", 3)
    assert res.returncode == direct.returncode == 0
    out = res.stdout.decode()
    assert _without_pid_line(out) == _without_pid_line(direct.stdout.decode())
    assert "fake-child-rc=143" in out
    fake_pid = int(out.splitlines()[0].split("=")[1])

    strace_logs, wrapper_logs = _logs(sandbox)
    text = strace_logs[0].read_text()
    # kill(child, SIGTERM) issued by the fake itself: sender PID == fake_pid.
    kill_lines = [l for l in text.splitlines() if " kill(" in l and "SIGTERM" in l]
    assert kill_lines, text
    assert all(l.split()[0] == str(fake_pid) for l in kill_lines), kill_lines
    assert any("exit_group(" in l for l in text.splitlines()), text
    assert "+++ killed by SIGTERM +++" in text

    # Nothing from the trace may appear on the CLI's stdout or stderr.
    for needle in ("execve(", " kill(", "exit_group(", "+++ "):
        assert needle not in out
        assert needle not in res.stderr.decode()
    assert "start wrapper_pid=" in wrapper_logs[0].read_text()


def test_signal_sent_to_wrapper_is_forwarded_with_sender_pid(sandbox):
    env = dict(sandbox["env"], FAKE_MODE="exit", FAKE_DELAY="30")
    proc = subprocess.Popen(
        [sandbox["env"]["CORVIN_CLAUDE_BIN"], "-p", "x"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )
    proc.stdin.write(b"ping\n")
    proc.stdin.close()
    assert proc.stdout.readline().startswith(b"fake-pid=")
    assert proc.stdout.readline() == b"fake-start\n"
    proc.terminate()  # SIGTERM from the test process (the sender)
    rc = proc.wait(timeout=60)
    assert rc == -15, f"CLI death not passed through: rc={rc}"
    strace_logs, wrapper_logs = _logs(sandbox)
    wlog = wrapper_logs[0].read_text()
    assert f"SIGTERM from pid={os.getpid()}" in wlog
    assert "forwarded to pid=" in wlog
    assert "SIGTERM, si_code=SI_USER" in strace_logs[0].read_text()


def test_fail_open_when_strace_missing(sandbox):
    direct = _run_direct(sandbox, "exit", 1)
    res = _run_wrapped(sandbox, "exit", 1, extra_env={"CORVIN_CLAUDE_STRACE_BIN": "/nonexistent/strace"})
    assert res.returncode == direct.returncode == 255
    assert _without_pid_line(res.stdout.decode()) == _without_pid_line(direct.stdout.decode())
    assert res.stderr == b""
    strace_logs, wrapper_logs = _logs(sandbox)
    assert strace_logs == []
    assert "WARN strace inactive" in wrapper_logs[0].read_text()


def test_fail_open_when_ptrace_refused(sandbox):
    refusing = sandbox["tmp"] / "strace-refusing"
    refusing.write_text(
        "#!/bin/sh\necho 'strace: ptrace(PTRACE_TRACEME, ...): Operation not permitted' >&2\nexit 1\n"
    )
    refusing.chmod(0o755)
    direct = _run_direct(sandbox, "exit", 1)
    res = _run_wrapped(sandbox, "exit", 1, extra_env={"CORVIN_CLAUDE_STRACE_BIN": str(refusing)})
    assert res.returncode == direct.returncode == 255
    assert _without_pid_line(res.stdout.decode()) == _without_pid_line(direct.stdout.decode())
    assert res.stderr == b"", "strace's refusal leaked into the CLI's stderr"
    strace_logs, wrapper_logs = _logs(sandbox)
    assert strace_logs == []
    wlog = wrapper_logs[0].read_text()
    assert "WARN strace inactive (probe rc=1" in wlog
    assert "Operation not permitted" in wlog
