"""runner.run_tool must close the Windows Job Object on EVERY exit path.

Regression: the handle was closed only after a successful communicate() or
in the TimeoutExpired branch; any other exception (BrokenPipe on stdin,
KeyboardInterrupt, ...) leaked the handle and -- because the job carries
KILL_ON_JOB_CLOSE -- also left the tool tree running. Windows is simulated
(sys.platform + a fake WinJob) so this runs on every platform.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge import _winproc  # noqa: E402
from forge import runner as runner_mod  # noqa: E402
from forge.registry import Registry  # noqa: E402

ECHO_IMPL = '''#!/usr/bin/env python3
import json, sys
p = json.loads(sys.stdin.read())
print(json.dumps({"ok": True, "echo": p}))
'''
ECHO_SCHEMA = {"type": "object", "required": ["msg"],
               "properties": {"msg": {"type": "string"}}}


class _FakeJob:
    instances: list["_FakeJob"] = []

    def __init__(self, cpu_seconds=None):
        self.closed = 0
        _FakeJob.instances.append(self)

    def assign(self, proc):
        return True

    def terminate(self):
        return False

    def close(self):
        self.closed += 1


@pytest.fixture
def simulated_windows(monkeypatch, tmp_path):
    _FakeJob.instances.clear()
    monkeypatch.setattr(_winproc, "WinJob", _FakeJob)
    monkeypatch.setattr(runner_mod.sys, "platform", "win32")
    reg = Registry(tmp_path)
    reg.create("echo", "echo tool", ECHO_SCHEMA, ECHO_IMPL)
    return reg


def _patch_communicate(monkeypatch, exc):
    real_popen = subprocess.Popen
    spawned = []

    def _popen(*a, **kw):
        p = real_popen(*a, **kw)
        spawned.append(p)

        def _boom(*_a, **_kw):
            raise exc
        p.communicate = _boom
        return p

    monkeypatch.setattr(runner_mod.subprocess, "Popen", _popen)
    return spawned


def _reap(spawned):
    for p in spawned:
        try:
            p.kill()
        except OSError:
            pass
        for s in (p.stdin, p.stdout, p.stderr):
            try:
                s.close()
            except Exception:  # noqa: BLE001
                pass
        p.wait(timeout=10)


@pytest.mark.parametrize("exc", [BrokenPipeError("stdin closed"), RuntimeError("boom")])
def test_job_closed_when_communicate_raises_unexpected(simulated_windows, monkeypatch, exc):
    spawned = _patch_communicate(monkeypatch, exc)
    try:
        with pytest.raises(type(exc)):
            runner_mod.run_tool(simulated_windows, "echo", {"msg": "hi"},
                                permission_mode="yes", use_sandbox=False)
    finally:
        _reap(spawned)
    assert len(_FakeJob.instances) == 1
    assert _FakeJob.instances[0].closed >= 1


def test_job_closed_on_success(simulated_windows):
    r = runner_mod.run_tool(simulated_windows, "echo", {"msg": "hi"},
                            permission_mode="yes", use_sandbox=False)
    assert r.ok
    assert len(_FakeJob.instances) == 1
    assert _FakeJob.instances[0].closed >= 1
