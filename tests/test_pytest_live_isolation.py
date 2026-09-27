"""Regression: pytest must never write into a live install's audit chain.

Incident 2026-09-27: ``core/delegate/tests/test_delegation.py`` (+ the 7-record
``test_output_judge.py`` / ``test_prompt_safety.py``) ran without ``CORVIN_HOME``;
``data_classification.load_guard_for_tenant()`` fell back to
``forge.paths.corvin_home()``, which resolves the repo-marker home of the checkout
its FILE lives in, and 2 x 34 ``data_flow.approved`` records landed in the live
chain. The root ``conftest.py`` now (a) exports a sandbox ``CORVIN_HOME`` /
``XDG_CONFIG_HOME`` / ``CORVIN_AUDIT_ANCHOR_KEY`` that subprocesses inherit and
(b) stops the session, naming the test, when a protected chain gains foreign
records anyway (a subprocess that scrubbed its env).

Everything here runs against a FAKE live checkout under ``tmp_path`` — never the
real one.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_LIVE_IID = "live-0000-fake"

_WRITER = textwrap.dedent('''
    import json, sys
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    sys.path.insert(0, str(root))  # the split-brain shape: forge.paths from THIS checkout
    from corvin_operator.forge.forge.paths import tenant_audit_chain
    chain = tenant_audit_chain("_default")
    chain.parent.mkdir(parents=True, exist_ok=True)
    with open(chain, "a") as fh:
        fh.write(json.dumps({"event_type": "data_flow.approved",
                             "instance_id": "test-process"}) + "\\n")
    print(chain)
''')


def _fake_live_checkout(base: Path) -> "tuple[Path, Path, Path]":
    """A checkout with the repo marker, its own forge.paths and a live chain."""
    live = base / "fake_live"
    (live / "corvin_operator" / "forge" / "forge").mkdir(parents=True)
    (live / "corvin_operator" / "bridges" / "shared").mkdir(parents=True)
    (live / ".corvin_repo").write_text("")
    (live / "corvin_operator" / "__init__.py").write_text("")
    (live / "corvin_operator" / "forge" / "forge" / "__init__.py").write_text("")
    shutil.copy(_REPO / "corvin_operator" / "forge" / "forge" / "paths.py",
                live / "corvin_operator" / "forge" / "forge" / "paths.py")
    writer = live / "corvin_operator" / "bridges" / "shared" / "writer.py"
    writer.write_text(_WRITER)
    home = live / ".corvin"
    (home / "global").mkdir(parents=True)
    (home / "global" / "instance_id.json").write_text(json.dumps({"instance_id": _LIVE_IID}))
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    chain.parent.mkdir(parents=True)
    chain.write_text(json.dumps({"event_type": "boot", "instance_id": _LIVE_IID}) + "\n")
    return live, writer, chain


def _scrubbed_env(tmp_home: Path) -> dict:
    """The incident shape: temp HOME, no CORVIN_HOME, no PYTHONPATH."""
    return {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_home)}


def test_inherited_env_keeps_subprocess_out_of_live_home(tmp_path):
    _live, writer, chain = _fake_live_checkout(tmp_path)
    before = chain.read_bytes()
    home = tmp_path / "h"
    home.mkdir()

    # Positive control: with the env scrubbed the writer DOES resolve the
    # repo-marker home — otherwise the assertion below would be vacuous.
    r = subprocess.run([sys.executable, str(writer)], env=_scrubbed_env(home),
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()) == chain
    chain.write_bytes(before)  # fake live only — reset for the real assertion

    # The fix: a subprocess that inherits the pytest env (even with its own
    # temp HOME, as in the incident) lands in the sandbox.
    assert os.environ["CORVIN_HOME"] and not os.environ["CORVIN_HOME"].startswith(str(_live))
    r = subprocess.run([sys.executable, str(writer)],
                       env={**os.environ, "HOME": str(home)},
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert Path(r.stdout.strip()).is_relative_to(Path(os.environ["CORVIN_HOME"]))
    assert chain.read_bytes() == before, "live chain changed"


def _nested_session(tmp_path: Path, body: str, live: Path, extra_env: "dict | None" = None):
    proj = tmp_path / "nested"
    proj.mkdir()
    shutil.copy(_REPO / "conftest.py", proj / "conftest.py")
    (proj / "pytest.ini").write_text("[pytest]\n")
    (proj / "test_nested.py").write_text(textwrap.dedent(body))
    env = {**os.environ,
           "CORVIN_TEST_PROTECTED_HOMES": str(live / ".corvin"),
           "PYTHONPATH": os.pathsep.join([str(_REPO), os.environ.get("PYTHONPATH", "")])}
    env.update(extra_env or {})
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-v", "-p", "no:cacheprovider", "-o", "addopts=", str(proj)],
        cwd=proj, env=env, capture_output=True, text=True, timeout=300)


def test_scrubbed_subprocess_write_stops_the_session_naming_the_test(tmp_path):
    live, writer, chain = _fake_live_checkout(tmp_path)
    body = f'''
        import os, subprocess, sys
        def test_0_env_rearmed():
            # the shell handed us the protected home; the conftest replaced it
            assert not os.environ["CORVIN_HOME"].startswith({str(live)!r})
            assert os.environ["CORVIN_AUDIT_ANCHOR_KEY"]
        def test_a_scrubbed_writer(tmp_path):
            env = {{"PATH": os.environ["PATH"], "HOME": str(tmp_path)}}
            subprocess.run([sys.executable, {str(writer)!r}], env=env, check=True)
        def test_b_never_runs():
            pass
    '''
    r = _nested_session(tmp_path, body, live,
                        {"CORVIN_HOME": str(live / ".corvin")})
    out = r.stdout + r.stderr
    assert r.returncode == 3, out
    assert "test_0_env_rearmed PASSED" in out
    assert "LIVE AUDIT CHAIN TOUCHED during test_nested.py::test_a_scrubbed_writer" in out
    assert "1 foreign record(s)" in out
    assert "test_b_never_runs PASSED" not in out


def test_env_leak_to_live_is_rearmed_and_reported(tmp_path):
    live, _writer, _chain = _fake_live_checkout(tmp_path)
    body = '''
        import os
        def test_leaks():
            os.environ.pop("CORVIN_HOME")   # no restore
        def test_next_is_rearmed():
            assert os.environ.get("CORVIN_HOME")
    '''
    r = _nested_session(tmp_path, body, live)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "test_leaks PASSED" in out and "test_next_is_rearmed PASSED" in out
    assert "test_nested.py::test_leaks: left ['CORVIN_HOME'] unset/live (re-armed)" in out
