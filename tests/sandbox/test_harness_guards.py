"""Harness self-proofs: each guard must be able to fail, and the code under test is this tree."""
from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from ._preconditions import require

require()

from . import harness  # noqa: E402


def _wait_modules(sb, pid, name, timeout=15.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        mods = sb.modules_of(pid)
        if name in mods:
            return mods
        time.sleep(0.3)
    raise AssertionError(f"probe never saw {name} in pid {pid}")


def _assert_this_tree(sb, server):
    mods = _wait_modules(sb, server.pid, "corvin_console")
    assert Path(mods["corvin_console"]).resolve().is_relative_to(harness.REPO_ROOT), mods["corvin_console"]
    problems = harness.origin_problems(mods)
    assert not problems, f"modules outside REPO_ROOT: {problems[:5]}"


def test_guard_refuses_live_and_real_home_paths():
    for p in (harness.real_home() / ".corvin", harness.MAIN_ROOT / ".corvin",
              harness.REPO_ROOT / ".corvin", harness.real_home() / "anything"):
        with pytest.raises(RuntimeError):
            harness.refuse_live_path(p)


def test_guard_refuses_an_inherited_live_corvin_home(monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(harness.MAIN_ROOT / ".corvin"))
    with pytest.raises(RuntimeError, match="refuses live path"):
        harness.Sandbox.create()


def test_guard_live_tripwire_detects_each_change(tmp_path):
    """Positive control for every LiveStateTripwire check, on a fake home and fake live root."""
    home, live = tmp_path / "home", tmp_path / "live"
    mem = home / ".claude" / "projects" / "x" / "memory"
    mem.mkdir(parents=True)
    (mem / "a.md").write_text("a", encoding="utf-8")
    chain = live / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    chain.write_text(json.dumps({"event_type": "x", "tenant_id": "_default"}) + "\n", encoding="utf-8")
    voice = home / ".config" / "corvin-voice" / "chain_ids"
    voice.mkdir(parents=True)
    (voice / "preexisting").write_text("", encoding="utf-8")
    sbx_chain = tmp_path / "sbx" / "audit.jsonl"
    sbx_chain.parent.mkdir()
    sbx_chain.write_text(json.dumps({"hash": "abc"}) + "\n", encoding="utf-8")

    class _Sb:
        root = tmp_path / "sbx"

        def chain_files(self):
            return [sbx_chain]

        def instance_id(self):
            return "iid-sbx"

    trip = harness.LiveStateTripwire(home=home, corvin_roots=[live])
    trip.sandboxes.append(_Sb())
    assert trip.check() == []
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_type": "y", "tenant_id": "sbxq"}) + "\n")
    (mem / "a.md").write_text("changed", encoding="utf-8")
    (voice / harness._sha32(str(sbx_chain.resolve()))).write_text("", encoding="utf-8")
    problems = " ".join(trip.check())
    assert "live chain" in problems and "memory" in problems and "sandbox-keyed" in problems


def test_guard_this_tree_check_fails_on_main_checkout_modules():
    """Negative control: the module-origin guard rejects the pre-fix state (corvin_console
    resolved through the venv's editable .pth to the main checkout)."""
    if harness.MAIN_ROOT == harness.REPO_ROOT:
        pytest.skip("not a worktree: main checkout and REPO_ROOT coincide")

    class _Sb:
        def modules_of(self, pid):
            return {"corvin_console": str(harness.MAIN_ROOT / "core/console/corvin_console/__init__.py")}

    class _Srv:
        pid = 0

    with pytest.raises(AssertionError):
        _assert_this_tree(_Sb(), _Srv())


def test_guard_console_server_runs_this_tree(server, sandbox):
    """BLOCKER-1 guard: the RUNNING console resolves every corvin_* package under REPO_ROOT."""
    _assert_this_tree(sandbox, server)


def test_guard_gateway_runs_this_tree_and_the_boot_tripwire_ran(gateway, sandbox):
    _assert_this_tree(sandbox, gateway)
    calls = sandbox.tripwire_calls(gateway.pid)
    assert any(c["ok"] for c in calls), f"boot tripwire not observed in the gateway: {calls}"


def test_guard_server_env_is_allowlisted(server, sandbox):
    env = server.environ()
    extra = set(env) - harness.ENV_ALLOWLIST
    assert not extra, f"server env carries non-allowlisted keys: {sorted(extra)}"
    assert not (set(env) & harness.ENV_FORBIDDEN), "server can reach the operator's user session"
    assert not any(k.startswith(("PYTEST_", "CLAUDE")) for k in env)
    assert env["CORVIN_CONSOLE_PORT"] == str(server.port) != "8765"
    assert env["CORVIN_CONSOLE_BASE_URL"] == server.base
    assert env["CORVIN_A2A_INGRESS"] == "off"
    assert Path(env["CORVIN_AUDIT_ANCHOR_KEY"]).is_absolute()
    assert env["HOME"] != str(harness.real_home())
    launcher = Path(env["HOME"]) / ".config" / "corvin-launcher" / "config.json"
    assert json.loads(launcher.read_text())["auto_update"] is False
    yaml = sandbox.corvin_home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    assert (yaml.stat().st_mode & 0o777) == 0o600
    # Read-only `systemctl is-active …` from status routes is refused by the shim and reported
    # in WP-02.md; a mutating verb would be an update restart reaching for the live units.
    mutating = ("start", "stop", "restart", "reload", "enable", "disable", "daemon-reload", "kill")
    bad = [c for c in sandbox.tool_invocations("systemctl")
           if any(f" {v} " in f" {c} " for v in mutating)]
    assert bad == [], f"server tried to change systemd units: {bad}"


def test_guard_git_shim_intercepts_and_redacts(sandbox):
    before = len(sandbox.git_invocations())
    proc = subprocess.run(["git", "clone", "https://user:s3cr3t@github.com/x/y", "/nonexistent"],
                          env=sandbox.env, capture_output=True, text=True)
    assert proc.returncode == harness.GIT_EXIT
    new = sandbox.git_invocations()[before:]
    assert len(new) == 1 and "s3cr3t" not in new[0] and "://***@github.com" in new[0], new


def test_guard_server_is_its_own_process_group(server):
    assert os.getpgid(server.pid) == server.pid


def test_guard_server_owns_its_listening_socket(server):
    assert harness.owns_listener(server.pid, server.port)
    assert not harness.owns_listener(os.getpid(), server.port), "control: pytest holds no listener"


_PARENT = r"""
import json, subprocess, sys, time
spec = json.loads(sys.argv[1])
p = subprocess.Popen(spec["cmd"], env=spec["env"], cwd=spec["cwd"], start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
print(p.pid, flush=True)
time.sleep(600)
"""


def _alive(pid: int) -> bool:
    try:
        state = Path(f"/proc/{pid}/stat").read_text().split(")")[-1].split()[0]
    except OSError:
        return False
    return state != "Z"


def test_guard_sandbox_server_dies_with_its_parent(sandbox):
    """Orphan guard: kill the parent of a sandbox uvicorn server (SIGKILL, so no teardown runs)
    and require the server to be gone within a few seconds."""
    import signal
    import sys

    port = harness.free_port()
    env = dict(sandbox.env, CORVIN_CONSOLE_PORT=str(port),
               CORVIN_CONSOLE_BASE_URL=f"http://127.0.0.1:{port}")
    cmd = [sys.executable, "-m", "uvicorn", "corvin_console.app:create_app", "--factory",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    parent = subprocess.Popen([sys.executable, "-c", _PARENT,
                               json.dumps({"cmd": cmd, "env": env, "cwd": str(sandbox.root)})],
                              stdout=subprocess.PIPE, text=True)
    server_pid = int(parent.stdout.readline().strip())
    try:
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline and not (sandbox.probe_dir / f"modules-{server_pid}.json").exists():
            time.sleep(0.2)  # the guard is armed by sitecustomize at interpreter start
        assert _alive(server_pid), "server did not start"
        parent.send_signal(signal.SIGKILL)
        parent.wait(timeout=10)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _alive(server_pid):
            time.sleep(0.2)
        assert not _alive(server_pid), "sandbox server outlived its parent"
    finally:
        if _alive(server_pid):
            os.killpg(server_pid, signal.SIGKILL)
        if parent.poll() is None:
            parent.kill()
