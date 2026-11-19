"""Review 2026-11-19 round 3, R3-BR-4: the worker workspace rule.

* every first-level dot directory of the home is refused, not a list of some;
* a session / ACS run directory of the runtime root is a workspace of ITS tenant only.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "delegate", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from corvin_delegate import delegation  # noqa: E402
from corvin_delegate.delegation import DelegateError, _validate_working_dir  # noqa: E402


@pytest.fixture()
def world(tmp_path, monkeypatch):
    user = tmp_path / "user"
    corvin = user / ".corvin"
    user.mkdir()
    monkeypatch.setenv("HOME", str(user))
    monkeypatch.setenv("CORVIN_HOME", str(corvin))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user / ".config"))
    monkeypatch.setenv("TMPDIR", str(tmp_path / "tmp"))
    (tmp_path / "tmp").mkdir()
    import tempfile
    monkeypatch.setattr(tempfile, "tempdir", None)
    for var in ("CORVIN_TENANT_ID", "CORVIN_SESSION_DIR", delegation.WORKSPACE_ROOTS_ENV,
                "CLAUDE_CONFIG_DIR", "CODEX_HOME"):
        monkeypatch.delenv(var, raising=False)
    return user, corvin


@pytest.mark.parametrize("rel", [
    ".local/bin", ".password-store", ".cargo", ".cargo/bin", ".mozilla/firefox",
    ".cache", ".cache/pip", ".local/share/uv", ".local/share/uv/tools", ".ssh",
    ".some-new-tool/state", ".gitconfig.d",
])
def test_every_home_dot_directory_is_refused(world, rel):
    user, _ = world
    target = user / rel
    target.mkdir(parents=True)
    with pytest.raises(DelegateError):
        _validate_working_dir(str(target))


def test_an_ordinary_project_below_the_home_is_still_a_workspace(world):
    user, _ = world
    project = user / "projects" / "demo"
    project.mkdir(parents=True)
    assert _validate_working_dir(str(project)) == project.resolve()
    hidden_inside = project / ".venv"  # a dot directory deeper down is not a home dot dir
    hidden_inside.mkdir()
    assert _validate_working_dir(str(hidden_inside)) == hidden_inside.resolve()


def test_an_operator_root_admits_a_dot_directory(world, monkeypatch):
    user, _ = world
    target = user / ".workspaces" / "a"
    target.mkdir(parents=True)
    with pytest.raises(DelegateError):
        _validate_working_dir(str(target))
    monkeypatch.setenv(delegation.WORKSPACE_ROOTS_ENV, str(user / ".workspaces"))
    assert _validate_working_dir(str(target)) == target.resolve()


def test_a_session_directory_is_a_workspace_of_its_own_tenant_only(world, monkeypatch):
    _, corvin = world
    own = corvin / "tenants" / "_default" / "sessions" / "c1"
    other = corvin / "tenants" / "other" / "sessions" / "c1"
    other_run = corvin / "tenants" / "other" / "global" / "acs" / "runs" / "r1" / "ws"
    own_run = corvin / "tenants" / "_default" / "global" / "acs" / "runs" / "r1" / "ws"
    for d in (own, other, other_run, own_run):
        d.mkdir(parents=True)
    # process tenant _default (CORVIN_TENANT_ID unset)
    assert _validate_working_dir(str(own)) == own.resolve()
    assert _validate_working_dir(str(own_run)) == own_run.resolve()
    for foreign in (other, other_run):
        with pytest.raises(DelegateError):
            _validate_working_dir(str(foreign))
        with pytest.raises(DelegateError):
            _validate_working_dir(str(foreign), "_default")
    # the explicit tenant decides, not the environment
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    assert _validate_working_dir(str(other), "other") == other.resolve()
    with pytest.raises(DelegateError):
        _validate_working_dir(str(own), "other")


def test_the_legacy_sessions_root_is_the_default_tenants(world):
    _, corvin = world
    legacy = corvin / "sessions" / "c1"
    legacy.mkdir(parents=True)
    assert _validate_working_dir(str(legacy)) == legacy.resolve()
    with pytest.raises(DelegateError):
        _validate_working_dir(str(legacy), "other")


def test_run_delegate_hands_its_tenant_to_the_workspace_rule(world, monkeypatch):
    _, corvin = world
    other = corvin / "tenants" / "other" / "sessions" / "c1"
    other.mkdir(parents=True)
    with pytest.raises(DelegateError):
        delegation.run_delegate(engine="claude_code", prompt="hello", working_dir=str(other),
                                tenant_id="_default")
