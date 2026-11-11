"""T-0383 / REVIEW-0062 N5 - the repo-root conftest sandboxes the platform data directory for every test process, and a test that
writes below the REAL ``<data dir>/corvin/projects`` fails.

The managed project workspace (ADR-2321 Decision 7) lives in the platform data directory, outside ``CORVIN_HOME``; without this
sandbox the first test that creates a project with the shipped default storage would write into the operator's real workspace.
The planted "real" directory is a tmp path handed to a child pytest through ``CORVIN_TEST_REAL_DATA_DIRS`` - the real one is never
written.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]


def test_the_data_dir_variables_point_into_the_session_sandbox_in_a_subprocess():
    sandbox = os.environ.get("CORVIN_PYTEST_SESSION_SANDBOX")
    assert sandbox, "the repo conftest did not install a session sandbox"
    keys = ["XDG_DATA_HOME"] + (["LOCALAPPDATA", "APPDATA"] if sys.platform == "win32" else [])
    out = subprocess.run([sys.executable, "-c", f"import json, os; print(json.dumps({{k: os.environ.get(k) for k in {keys!r}}}))"],
                         capture_output=True, text=True, timeout=60, check=True)
    got = json.loads(out.stdout)
    for k in keys:
        assert got[k], f"{k} is not set in a child process"
        assert Path(got[k]).resolve().is_relative_to(Path(sandbox).resolve()), (k, got[k], sandbox)
    real = Path(os.path.expanduser("~")).resolve() / ".local" / "share"
    assert Path(got["XDG_DATA_HOME"]).resolve() != real


def test_the_managed_parent_resolves_into_the_sandbox():
    from core.paths import user_dirs
    sandbox = Path(os.environ["CORVIN_PYTEST_SESSION_SANDBOX"]).resolve()
    if sys.platform == "darwin":
        pytest.skip("on macOS the data dir follows HOME, which the conftest leaves alone; the projects Sandbox moves HOME")
    assert Path(str(user_dirs.managed_parent())).resolve().is_relative_to(sandbox)


def _child_pytest(tmp: Path, body: str, workspaces: tuple[str, ...] = ()) -> subprocess.CompletedProcess:
    real = tmp / "real-data" / "corvin" / "projects"
    real.mkdir(parents=True)
    for w in workspaces:                                      # an operator's existing workspace, there before the session
        (real / w / "existing").mkdir(parents=True)
    test = tmp / "planted" / "test_planted.py"
    test.parent.mkdir(parents=True)
    test.write_text(textwrap.dedent(body).replace("@REAL@", str(real)), encoding="utf-8")
    env = {**os.environ, "CORVIN_TEST_REAL_DATA_DIRS": str(real), "PYTHONPATH": str(_REPO)}
    env.pop("CORVIN_PYTEST_SESSION_SANDBOX", None)              # a fresh session in the child
    env.pop("PYTEST_ADDOPTS", None)
    return subprocess.run([sys.executable, "-m", "pytest", "-p", "conftest", "-q", "-p", "no:cacheprovider",
                           "--rootdir", str(test.parent), str(test)],
                          cwd=str(_REPO), env=env, capture_output=True, text=True, timeout=300)


def test_a_planted_test_that_writes_into_the_real_workspace_fails():
    with tempfile.TemporaryDirectory() as td:
        r = _child_pytest(Path(td), """
            from pathlib import Path
            def test_writes():
                (Path(r"@REAL@") / "w-planted").mkdir()
        """)
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "wrote into the real Corvin workspace" in out, out
        assert "w-planted" in out, out


def test_a_planted_test_that_writes_into_an_existing_workspace_fails():
    # REVIEW-0064 #5: one level deeper - a project written into the operator's own w-<key> workspace is caught too
    with tempfile.TemporaryDirectory() as td:
        r = _child_pytest(Path(td), """
            from pathlib import Path
            def test_writes():
                (Path(r"@REAL@") / "w-0123456789" / "alpha").mkdir()
        """, workspaces=("w-0123456789",))
        out = r.stdout + r.stderr
        assert r.returncode != 0, out
        assert "wrote into the real Corvin workspace" in out, out
        assert "alpha" in out, out


def test_a_planted_test_that_writes_nothing_there_passes():
    with tempfile.TemporaryDirectory() as td:
        r = _child_pytest(Path(td), """
            def test_quiet():
                assert True
        """)
        assert r.returncode == 0, r.stdout + r.stderr
