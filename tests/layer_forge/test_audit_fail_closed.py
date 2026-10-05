"""Audit-first, fail-closed: no chain commit -> no registry change (ADR-2222 D6)."""
import pytest

from core.orchestration.layer_forge import audit
from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator


@pytest.fixture
def orch(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    return LayerForgeOrchestrator("_default")


def _manifest(version="0.1.0"):
    return {"id": "fc.rule", "version": version, "targets": [{"layer_id": "L34"}]}


def _break_chain(monkeypatch):
    def boom(*_a, **_k):
        raise OSError("disk full")
    monkeypatch.setattr(audit, "_core_write_event", lambda: boom)


def test_create_writes_nothing_when_the_chain_write_fails(orch, monkeypatch):
    _break_chain(monkeypatch)
    result = orch.create_layer_definition(_manifest(), skip_gates=True)
    assert result.status == "FAILED" and result.phase == "audit"
    assert orch.list_definitions() == []


def test_transition_does_not_happen_when_the_chain_write_fails(orch, monkeypatch):
    assert orch.create_layer_definition(_manifest(), skip_gates=True).status == "SUCCESS"
    _break_chain(monkeypatch)
    with pytest.raises(audit.LayerForgeAuditError):
        orch.promote("fc.rule", "0.1.0", "deployed")
    assert orch.get("fc.rule")["status"] == "accepted"


def test_unregistered_field_is_refused_before_writing(monkeypatch):
    called = []
    monkeypatch.setattr(audit, "_core_write_event", lambda: lambda *a, **k: called.append(1))
    with pytest.raises(audit.LayerForgeAuditError):
        audit.emit("layer_forge.definition_proposed", tenant_id="_default", manifest_body="x")
    assert called == []


def test_module_allowlist_matches_the_central_registry():
    audit._core_write_event()  # puts corvin_operator/forge on sys.path like production does
    from forge.security_events import EVENT_SEVERITY, _EVENT_ALLOWLIST

    for event, fields in audit.ALLOWED_FIELDS.items():
        assert _EVENT_ALLOWLIST[event] == fields, event
        assert EVENT_SEVERITY[event] == audit.SEVERITY[event], event
