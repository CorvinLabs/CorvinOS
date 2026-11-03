"""The console chat system prompt names THIS tenant's knowledge base (ADR-2205 / ADR-2265).

Without the block a chat agent asked to record a task did not know where the tenant KB is
(live test, 2026-11-03): the gate skills reach chat only through CEL, which cuts them first.
"""
from __future__ import annotations

from types import SimpleNamespace

from corvin_console import chat_runtime as cr


def _sess(tenant: str) -> SimpleNamespace:
    return SimpleNamespace(tenant_id=tenant)


def test_block_names_the_tenant_kb_and_the_pinned_command(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    kb = tmp_path / "tenants" / "acme" / "knowledge"
    (kb / "scripts").mkdir(parents=True)
    (kb / "scripts" / "kb.py").write_text("# engine\n", encoding="utf-8")
    block = cr._knowledge_base_block(_sess("acme"))
    assert (kb / "scripts" / "kb.py").as_posix() in block
    assert "new task" in block and "blocking: 0" in block
    assert "{{" not in block


def test_no_block_before_the_tenant_kb_exists(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    assert cr._knowledge_base_block(_sess("acme")) == ""


def test_a_tenant_never_gets_another_tenants_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    for t in ("acme", "beta"):
        d = tmp_path / "tenants" / t / "knowledge" / "scripts"
        d.mkdir(parents=True)
        (d / "kb.py").write_text("#\n", encoding="utf-8")
    block = cr._knowledge_base_block(_sess("beta"))
    assert "/tenants/beta/knowledge" in block and "/tenants/acme/" not in block
