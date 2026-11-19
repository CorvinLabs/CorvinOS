"""R3-NEW-4 — the CEL memory stage reads the HOST auto-memory only for the operator.

``~/.claude/projects/<repo>/memory`` is the Claude Code auto-memory of the operator's
own account. The memory stage read it for every tenant and every bridge chat. It is now
the source only for the boot tenant's owner turns; everything else reads
``<tenant_home>/memory``.
"""
from __future__ import annotations

import json
import types

import pytest

from .. import memory_lookup as ml
from ..pipeline import build_context

SECRET = "zebracorn"
TASK = f"what do we know about {SECRET} deployment"


@pytest.fixture
def host_memory(tmp_path, monkeypatch):
    """A host auto-memory holding one note, and a channel whitelist naming the owner."""
    host = tmp_path / "host-memory"
    host.mkdir()
    (host / "operator_note.md").write_text(
        f"---\nname: {SECRET} deployment\n---\n{SECRET} deployment password rotation notes "
        f"{SECRET} {SECRET} deployment\n", encoding="utf-8")
    monkeypatch.setattr(ml, "_default_memory_dir", lambda: host)
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    settings = tmp_path / "discord-settings.json"
    settings.write_text(json.dumps({"whitelist": ["111"]}), encoding="utf-8")
    roles = pytest.importorskip("corvin_operator.bridges.shared.roles")
    monkeypatch.setattr(roles, "_channel_settings_path", lambda channel: settings)
    import sys
    monkeypatch.setitem(sys.modules, "roles", roles)
    # No tenant policy in the sandbox home: L34 is the opt-in default (allowed).
    monkeypatch.setattr(ml, "_host_memory_passes_l34", lambda tenant, session: True)
    return host


def _sources(tenant, session):
    bundle, trace = build_context(TASK, tenant, session, meter=False)
    assert bundle is not None, trace
    names = [getattr(m, "filename", "") for m in bundle.scratch.get("memory_matches", [])]
    return names, bundle.scratch.get("memory_source")


def _bridge(sender, chat="999", channel="discord"):
    return types.SimpleNamespace(sid=f"{channel}:{chat}", sender=sender)


def test_positive_control_owner_of_the_boot_tenant_gets_host_memory(host_memory):
    names, source = _sources("_default", _bridge("111", chat="111"))
    assert source == "host"
    assert "operator_note.md" in names


def test_other_tenant_bridge_chat_gets_no_host_memory(host_memory):
    names, source = _sources("tenant_b", _bridge("999"))
    assert source == "tenant"
    assert names == []


def test_other_tenant_gets_none_even_for_a_whitelisted_sender(host_memory):
    names, source = _sources("tenant_b", _bridge("111", chat="111"))
    assert (names, source) == ([], "tenant")


def test_boot_tenant_stranger_gets_no_host_memory(host_memory):
    names, source = _sources("_default", _bridge("999"))
    assert (names, source) == ([], "tenant")


def test_no_session_and_no_sender_get_no_host_memory(host_memory):
    assert _sources("_default", None) == ([], "tenant")
    assert _sources("_default", _bridge("")) == ([], "tenant")


def test_empty_whitelist_makes_nobody_the_owner(host_memory, tmp_path, monkeypatch):
    roles = pytest.importorskip("corvin_operator.bridges.shared.roles")
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({"whitelist": []}), encoding="utf-8")
    monkeypatch.setattr(roles, "_channel_settings_path", lambda channel: empty)
    assert _sources("_default", _bridge("111", chat="111")) == ([], "tenant")


def test_console_session_counts_only_for_its_own_boot_tenant(host_memory):
    own = types.SimpleNamespace(sid="abc", tenant_id="_default")
    other = types.SimpleNamespace(sid="abc", tenant_id="tenant_b")
    assert _sources("_default", own)[1] == "host"
    assert _sources("tenant_b", other) == ([], "tenant")
    # A session whose record names another tenant than the one the turn runs for.
    assert _sources("_default", other) == ([], "tenant")


def test_explicit_caller_statement_wins(host_memory):
    denied = types.SimpleNamespace(sid="discord:111", sender="111", host_memory=False)
    assert _sources("_default", denied) == ([], "tenant")


def test_l34_denial_withholds_the_host_memory(host_memory, monkeypatch):
    monkeypatch.setattr(ml, "_host_memory_passes_l34", lambda tenant, session: False)
    assert _sources("_default", _bridge("111", chat="111")) == ([], "tenant")


def test_l34_check_error_is_a_denial(monkeypatch):
    import sys
    boom = types.ModuleType("spawn_gates")

    def _raise(*a, **k):
        raise RuntimeError("policy unreadable")
    boom.check_l34 = _raise
    monkeypatch.setitem(sys.modules, "spawn_gates", boom)
    assert ml._host_memory_passes_l34("_default", _bridge("111")) is False
    seen = {}

    def _deny(engine, tenant, **kw):
        seen.update(kw, engine=engine, tenant=tenant)
        return "[data-flow] denied"
    boom.check_l34 = _deny
    assert ml._host_memory_passes_l34("_default", _bridge("111", chat="42#3")) is False
    assert seen["classification"] == "INTERNAL" and seen["engine"] == "claude_code"
    assert (seen["channel"], seen["chat_key"]) == ("discord", "42")
    boom.check_l34 = lambda *a, **k: None
    assert ml._host_memory_passes_l34("_default", _bridge("111")) is True


def test_tenant_memory_is_tenant_bound(host_memory, monkeypatch):
    d = ml.tenant_memory_dir("tenant_b")
    assert d.name == "memory" and "tenant_b" in d.parts
    d.mkdir(parents=True)
    (d / "tenant_note.md").write_text(
        f"---\nname: {SECRET} deployment\n---\n{SECRET} deployment tenant runbook "
        f"{SECRET} {SECRET} deployment\n", encoding="utf-8")
    names, source = _sources("tenant_b", _bridge("999"))
    assert source == "tenant" and names == ["tenant_note.md"]
    assert _sources("tenant_c", _bridge("999")) == ([], "tenant")
