"""Drift-E2E — dual-root path resolver (reader == writer).

The classic context-drift bug is a reader≠writer split: one module writes state
under ``~/.corvin`` while another reads it under ``<repo>/.corvin`` (or vice
versa), so persisted state silently vanishes. ``forge.paths.corvin_home()`` is
the single source of truth; ``operator/bridges/shared/paths.py`` is a byte-copy
that MUST resolve identically.

These tests pin:
  1. With ``CORVIN_HOME`` set, both resolver copies return exactly that path —
     no ``~/.corvin`` split (real env isolation via subprocess, no monkeypatch).
  2. A real reader/writer pair (``feature_flags.set_enabled`` writes,
     ``is_enabled`` reads) round-trips under a ``CORVIN_HOME`` sandbox — the
     overlay lands where the reader looks.
  3. Without env, inside a recognisable repo, the home is ``<repo>/.corvin``.
  4. A whitespace-only ``CORVIN_HOME`` is treated as unset (not ``Path(" ")``).

Nothing here touches the live ``~/.corvin``.

Run: python3 -m pytest tests/test_drift_dual_root_resolver_e2e.py
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_FORGE = _REPO / "operator" / "forge"
_SHARED = _REPO / "operator" / "bridges" / "shared"


def _run_isolated(body: str, corvin_home: str | None) -> str:
    """Run a snippet in a fresh interpreter with a controlled CORVIN_HOME.

    Real env isolation: the child gets an explicit environment, so the live
    ~/.corvin and the parent's env can never leak in."""
    header = (
        f"import sys\n"
        f"sys.path.insert(0, {str(_FORGE)!r})\n"
        f"sys.path.insert(0, {str(_SHARED)!r})\n"
    )
    script = header + body
    env = {k: v for k, v in os.environ.items()
           if k not in ("CORVIN_HOME", "CORVIN_TENANT_ID")}
    if corvin_home is not None:
        env["CORVIN_HOME"] = corvin_home
    out = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, env=env)
    assert out.returncode == 0, f"child failed:\n{out.stderr}"
    return out.stdout.strip()


def test_env_home_makes_both_resolvers_agree(tmp_path):
    """reader == writer: forge.paths and the shared bridge copy resolve to the
    SAME CORVIN_HOME — no reader≠writer split. Real subprocess env."""
    target = tmp_path / "corvin_root"
    out = _run_isolated(
        "import paths as shared_paths\n"
        "from forge import paths as forge_paths\n"
        "a = str(forge_paths.corvin_home())\n"
        "b = str(shared_paths.corvin_home())\n"
        "print(a)\nprint(b)",
        corvin_home=str(target),
    )
    forge_home, shared_home = out.splitlines()
    assert forge_home == str(target), "forge resolver must honour CORVIN_HOME"
    assert shared_home == str(target), "shared resolver must honour CORVIN_HOME"
    assert forge_home == shared_home, "reader≠writer split — resolvers diverged"


def test_env_home_expands_user_and_vars(tmp_path, monkeypatch):
    """CORVIN_HOME with a ~ / $VAR is expanded, not taken literally."""
    out = _run_isolated(
        "from forge import paths as forge_paths\n"
        "print(str(forge_paths.corvin_home()))",
        corvin_home="~/some-corvin-home",
    )
    assert out == str(Path.home() / "some-corvin-home")


def test_reader_writer_roundtrip_under_env_home(tmp_path, monkeypatch):
    """The real drift target: a genuine writer (set_enabled) and reader
    (is_enabled) agree on where the overlay lives, under a CORVIN_HOME sandbox.
    Exercises the resolver through the actual feature-flags consumer + disk."""
    sys.path.insert(0, str(_FORGE))
    sys.path.insert(0, str(_REPO / "core" / "console"))
    home = tmp_path / "corvin"
    (home / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)

    from corvin_console import feature_flags as ff
    ff._spec_cache.clear()

    fid = ff.REGISTRY[0].id
    ff.set_enabled(fid, True)          # WRITER
    overlay = home / "tenants" / "_default" / "global" / "features.json"
    assert overlay.is_file(), "writer must land the overlay under CORVIN_HOME"
    assert ff.is_enabled(fid) is True  # READER reads the same location
    ff.set_enabled(fid, False)
    assert ff.is_enabled(fid) is False


def test_repo_context_without_env_uses_repo_dotcorvin(tmp_path):
    """Without CORVIN_HOME, a recognisable repo (has a ``plugins/`` dir or a
    ``.corvin_repo`` marker) resolves to ``<repo>/.corvin`` — NOT ``~/.corvin``.

    _repo_root() walks up from paths.py's own location, so a faithful filesystem
    test would need a full module copy inside a temp repo. We isolate the ONE
    seam that decides the branch (_repo_root) to a temp repo dir and assert the
    composed path; the env is still fully isolated (CORVIN_HOME unset)."""
    sys.path.insert(0, str(_FORGE))
    from forge import paths as forge_paths
    from unittest import mock

    repo = tmp_path / "myrepo"
    (repo / "plugins").mkdir(parents=True)  # the repo-detection marker
    import os
    env = {k: v for k, v in os.environ.items() if k != "CORVIN_HOME"}
    with mock.patch.dict(os.environ, env, clear=True):
        with mock.patch.object(forge_paths, "_repo_root", return_value=repo):
            assert forge_paths.corvin_home() == repo / ".corvin"


def test_whitespace_env_home_is_treated_as_unset(tmp_path):
    """A whitespace-only CORVIN_HOME must NOT resolve to Path(' ') — it is
    treated as unset, so the resolver falls through to repo/home detection."""
    out = _run_isolated(
        "from forge import paths as forge_paths\n"
        "h = str(forge_paths.corvin_home())\n"
        "print(h)",
        corvin_home="   ",
    )
    assert out.strip() not in ("", " "), "whitespace home must not become Path(' ')"
    assert out.endswith(".corvin"), f"expected a .corvin root, got {out!r}"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
