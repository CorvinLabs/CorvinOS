"""Regression tests for forge/_winproc.py (round-6 Windows defects).

* bash-runtime tools hard-coded /bin/bash -> resolve Git Bash, never WSL.
* timeout kill reached only the direct child; a grandchild holding the pipes
  made the following communicate() block forever, and no RLIMIT_CPU meant a
  CPU spinner always ran the full wall budget.
"""
from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from forge import _winproc  # noqa: E402
from forge._winproc import BashNotFoundError, WinJob, kill_and_drain, resolve_bash  # noqa: E402

win_only = pytest.mark.skipif(os.name != "nt", reason="Windows-only behaviour")


@pytest.mark.skipif(os.name == "nt", reason="POSIX path")
def test_posix_bash_unchanged():
    assert resolve_bash() == "/bin/bash"


@win_only
def test_prefers_bash_next_to_git():
    git = r"D:\Tools\Git\cmd\git.exe"
    want = r"D:\Tools\Git\bin\bash.exe"
    got = resolve_bash(which=lambda n: git if n.startswith("git") else None,
                       exists=lambda p: os.path.normcase(p) == os.path.normcase(want))
    assert os.path.normcase(got) == os.path.normcase(want)


@win_only
def test_falls_back_to_program_files_git_bash():
    got = resolve_bash(which=lambda n: None, exists=lambda p: p == _winproc._GIT_BASH_DEFAULT)
    assert got == _winproc._GIT_BASH_DEFAULT


@win_only
@pytest.mark.parametrize("wsl", [
    os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "System32", "bash.exe"),
    r"C:\Users\x\AppData\Local\Microsoft\WindowsApps\bash.exe",
])
def test_never_picks_wsl_launcher(wsl):
    with pytest.raises(BashNotFoundError, match="Git Bash"):
        resolve_bash(which=lambda n: wsl if n.startswith("bash") else None,
                     exists=lambda p: False)


@win_only
def test_accepts_other_bash_on_path():
    other = r"C:\msys64\usr\bin\bash.exe"
    assert resolve_bash(which=lambda n: other if n.startswith("bash") else None,
                        exists=lambda p: False) == other


_GRANDCHILD_HOLDS_PIPES = (
    "import subprocess, sys, time\n"
    "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
    "time.sleep(120)\n"
)


@win_only
@pytest.mark.parametrize("use_job", [True, False])
def test_kill_and_drain_does_not_hang_on_grandchild(use_job):
    proc = subprocess.Popen([sys.executable, "-c", _GRANDCHILD_HOLDS_PIPES],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    job = WinJob() if use_job else None
    if job is not None:
        assert job.assign(proc)
    time.sleep(1.0)  # let the grandchild inherit the pipe handles
    with pytest.raises(subprocess.TimeoutExpired):
        proc.communicate(timeout=0.5)
    t0 = time.monotonic()
    kill_and_drain(proc, job, drain_timeout=5.0)
    assert time.monotonic() - t0 < 12.0
    assert proc.poll() is not None
    if job is not None:
        job.close()


@win_only
def test_job_cpu_limit_stops_spinner():
    proc = subprocess.Popen([sys.executable, "-c", "while True: pass"])
    job = WinJob(cpu_seconds=1)
    assert job.active and job.assign(proc)
    try:
        rc = proc.wait(timeout=20)
    finally:
        if proc.poll() is None:
            proc.kill()
        job.close()
    assert rc != 0


@win_only
def test_which_ignores_cwd_and_relative_path_entries(tmp_path, monkeypatch):
    # shutil.which on Windows probes .\git.exe / .\bash.exe first: a binary planted
    # in the server's working directory must never become the tool interpreter.
    planted = tmp_path / "work"
    planted.mkdir()
    (planted / "bash.exe").write_bytes(b"MZ")
    (planted / "git.exe").write_bytes(b"MZ")
    monkeypatch.chdir(planted)
    monkeypatch.setenv("PATH", os.pathsep.join([".", "work", ""]))
    assert _winproc._which_abs("bash.exe") is None
    assert _winproc._which_abs("git.exe") is None
    good = tmp_path / "abs"
    good.mkdir()
    (good / "bash.exe").write_bytes(b"MZ")
    monkeypatch.setenv("PATH", os.pathsep.join([".", str(good)]))
    assert _winproc._which_abs("bash.exe") == str(good / "bash.exe")


@win_only
def test_terminate_reports_false_when_never_assigned():
    # An empty job terminates "successfully"; the caller must then kill the tree itself.
    job = WinJob(cpu_seconds=None)
    try:
        assert job.active
        assert job.terminate() is False
    finally:
        job.close()


@win_only
def test_kill_and_drain_orphaned_pipe_holder_does_not_hang():
    # Parent exits at once; its orphan (outside any job, unreachable by taskkill /T)
    # keeps the pipes. Closing them under the blocked reader threads deadlocked.
    code = "import subprocess, sys; subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(40)'])"
    proc = subprocess.Popen([sys.executable, "-c", code],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    with pytest.raises(subprocess.TimeoutExpired):
        proc.communicate(timeout=2)
    t0 = time.monotonic()
    kill_and_drain(proc, None, drain_timeout=1)
    assert time.monotonic() - t0 < 15
