"""UNIT: the L10AdapterStage contract (shadow mode, ADR-0532 Phase 1 / ADR-0613 shape).

These supply a fake booted integration and call ``stage.run`` directly — they
prove behaviour-when-called, NOT reachability. The production-boundary E2E
(``pipeline.build_brief`` → stage → Skill → hash-chained audit) is
``tests/e2e/test_os_skills_l5_l10_wiring.py::TestL10ProductionCallSite``.

(The previous version of this file patched a module attribute
``l10_adapter.adapt_context_l10`` that never existed, so five of its tests
errored before reaching the stage.)
"""
from __future__ import annotations

import pytest

from corvin_operator.context_engineering.stages import ContextBundle, StageCtx
from corvin_operator.context_engineering.stages import l10_adapter
from corvin_operator.context_engineering.stages.l10_adapter import L10AdapterStage
from corvin_operator.context_engineering.stages._util import task_adapter


class _FakeRegistry:
    def __init__(self, audited=True, has_skill=True):
        self.audit_backend = object() if audited else None
        self._has = has_skill

    def get(self, skill_id):
        return object() if (self._has and skill_id == "os.context_adapter") else None


class _FakeIntegration:
    def __init__(self, registry, tenant_id="_default", result=None, exc=None):
        self.registry = registry
        self.tenant_id = tenant_id
        self.result = result
        self.exc = exc
        self.calls = []

    def adapt_context_l10(self, **kw):
        self.calls.append(kw)
        if self.exc:
            raise self.exc
        return self.result


_RESULT = {
    "base_tier": {"engine": "claude-sonnet-4", "priority": 5},
    "injected_tier": {"engine": "claude-sonnet-4", "priority": 6},
    "merged_tier": {"engine": "claude-sonnet-4", "priority": 6},
    "skill_executed": True,
    "error": None,
}


@pytest.fixture
def fake_boot(monkeypatch):
    """Install a fake booted integration into the real modules the stage reads."""
    from core.skills import os_skills_integration as integ_mod
    from core.skills import skill_registry_phase1 as reg_mod

    def _install(integration, registry):
        monkeypatch.setattr(reg_mod, "_global_registry", registry)
        monkeypatch.setattr(integ_mod, "_integration_instance", integration)
        # The stage imports the module-level entry point; route it to the fake.
        monkeypatch.setattr(integ_mod, "adapt_context_l10",
                            lambda **kw: integration.adapt_context_l10(**kw))
        return integration

    return _install


@pytest.fixture
def audit_sink(monkeypatch):
    written = []

    def _emit(tenant_id, summary):
        written.append((tenant_id, dict(summary)))
        return True

    monkeypatch.setattr(l10_adapter, "_emit_context_adapted", _emit)
    return written


def _run(tenant="_default", task="Some task text"):
    bundle = ContextBundle(task=task, brief=object())
    ctx = StageCtx(tenant_id=tenant, task_obj=task_adapter(task))
    brief_before = bundle.brief
    out, tel = L10AdapterStage().run(bundle, ctx)
    assert out is bundle and out.brief is brief_before, "shadow: brief object untouched"
    return out, tel


def test_stage_metadata():
    stage = L10AdapterStage()
    assert stage.id == "l10_adapter"
    assert stage.requires == ("graph",)
    assert stage.effect == "pure" and stage.trust == "builtin"


def test_executes_in_shadow_and_audits(fake_boot, audit_sink):
    reg = _FakeRegistry()
    integ = fake_boot(_FakeIntegration(reg, result=_RESULT), reg)
    out, tel = _run(task="Secret customer name Zyx")
    assert tel.status == "ok" and tel.reason == "shadow"
    assert len(integ.calls) == 1
    call = integ.calls[0]
    assert call["tenant_id"] == "_default"
    assert call["task_description"] == "" and call["user_context"] == {}, (
        "task text must never reach the Skill")
    assert call["timeout_ms"] == l10_adapter._TIMEOUT_MS
    assert out.scratch["l10_shadow"] == {
        "skill_executed": True, "engine": "claude-sonnet-4", "priority": 6,
        "injected": True, "audited": True,
    }
    assert audit_sink and audit_sink[0][0] == "_default"


def test_skipped_when_skills_not_booted(fake_boot, audit_sink):
    fake_boot(None, None)
    out, tel = _run()
    assert (tel.status, tel.reason) == ("skipped", "skills_not_booted")
    assert "l10_shadow" not in out.scratch and not audit_sink


def test_skipped_when_registry_has_no_audit_backend(fake_boot, audit_sink):
    reg = _FakeRegistry(audited=False)
    integ = fake_boot(_FakeIntegration(reg, result=_RESULT), reg)
    _out, tel = _run()
    assert (tel.status, tel.reason) == ("skipped", "skills_not_booted")
    assert not integ.calls, "an unaudited registry must never execute the Skill"


def test_skipped_for_a_tenant_the_registry_was_not_booted_for(fake_boot, audit_sink):
    reg = _FakeRegistry()
    integ = fake_boot(_FakeIntegration(reg, tenant_id="_default", result=_RESULT), reg)
    _out, tel = _run(tenant="acme")
    assert (tel.status, tel.reason) == ("skipped", "tenant_not_booted")
    assert not integ.calls and not audit_sink


def test_skill_error_degrades_without_breaking_the_turn(fake_boot, audit_sink):
    reg = _FakeRegistry()
    fake_boot(_FakeIntegration(reg, exc=RuntimeError("boom")), reg)
    out, tel = _run()
    assert tel.status == "failed" and tel.confidence_tier == "low" and tel.reason == "error"
    assert "l10_shadow" not in out.scratch


def test_audit_write_failure_is_surfaced_not_swallowed(fake_boot, monkeypatch, caplog):
    reg = _FakeRegistry()
    fake_boot(_FakeIntegration(reg, result=_RESULT), reg)

    import forge.security_events as se

    def _boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(se, "write_event", _boom)
    with caplog.at_level("ERROR"):
        out, tel = _run()
    assert tel.status == "failed" and tel.reason == "audit_write_failed"
    assert out.scratch["l10_shadow"]["audited"] is False
    assert any("AUDIT-WRITE FAILED" in r.getMessage() for r in caplog.records)
