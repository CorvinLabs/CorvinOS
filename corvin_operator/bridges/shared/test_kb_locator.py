"""kb_locator: which knowledge base a tenant's agent session writes to (ADR-2265).

Mirrors corvin_console.kb_projection.kb_repo (the board binding): the operator's KB
(CORVIN_KB_REPO / sibling checkout) serves only CORVIN_KB_TENANT; every tenant otherwise
has its own <CORVIN_HOME>/tenants/<tenant>/knowledge, and never another tenant's.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("_kb_locator_test", Path(__file__).with_name("kb_locator.py"))
kl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(kl)  # type: ignore[union-attr]


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))       # a sandboxed home: never the sibling KB
    for v in ("CORVIN_KB_REPO", "CORVIN_KB_TENANT", "CORVIN_TENANT_ID", "CORVIN_ACS_TENANT_ID"):
        monkeypatch.delenv(v, raising=False)
    return tmp_path


def test_each_tenant_resolves_its_own_kb(env):
    assert kl.resolve("_default") == env / "home" / "tenants" / "_default" / "knowledge"
    assert kl.resolve("acme") == env / "home" / "tenants" / "acme" / "knowledge"
    assert kl.resolve("acme") != kl.resolve("globex")


def test_the_session_tenant_comes_from_the_worker_environment(env, monkeypatch):
    monkeypatch.setenv("CORVIN_TENANT_ID", "acme")
    assert kl.resolve() == env / "home" / "tenants" / "acme" / "knowledge"


def test_the_operators_explicit_kb_binds_only_the_kb_tenant(env, monkeypatch):
    monkeypatch.setenv("CORVIN_KB_REPO", str(env / "operator-kb"))
    assert kl.resolve("_default") == env / "operator-kb"
    assert kl.resolve("acme") == env / "home" / "tenants" / "acme" / "knowledge"   # never the operator's KB


@pytest.mark.parametrize("bad", ["../x", "a/b", "..", "x" * 70, "a\\b"])
def test_a_malformed_tenant_is_refused(env, bad):
    with pytest.raises(ValueError):
        kl.resolve(bad)


def test_render_fills_every_placeholder_with_forward_slashes(env):
    out = kl.render("{{PYTHON}} {{KB_PY}} check; git -C {{KB_DIR}} log", "acme")
    kb = (env / "home" / "tenants" / "acme" / "knowledge").as_posix()
    assert out == f"{kl.python_cmd()} {kb}/scripts/kb.py check; git -C {kb} log"
    assert "\\" not in out and "{{" not in out


def test_render_never_raises_and_leaves_text_alone_without_placeholders(env):
    assert kl.render("plain text", "acme") == "plain text"
    assert kl.render("{{KB_PY}}", "../evil") == "{{KB_PY}}"
