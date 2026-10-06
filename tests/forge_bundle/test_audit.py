"""Forge Bundle audit module (ADR-2229) — registration + fail-closed pin.

Mirrors ``tests/layer_forge/test_audit_fail_closed.py``'s shape exactly.
"""
from __future__ import annotations

import pytest

from core.forge_bundle import audit


def test_module_allowlist_matches_the_central_registry():
    audit._core_write_event()  # puts corvin_operator/forge on sys.path like production does
    from forge.security_events import EVENT_SEVERITY, _EVENT_ALLOWLIST

    for event, fields in audit.ALLOWED_FIELDS.items():
        assert _EVENT_ALLOWLIST[event] == fields, event
        assert EVENT_SEVERITY[event] == audit.SEVERITY[event], event


def test_unregistered_event_raises():
    with pytest.raises(audit.ForgeBundleAuditError, match="unregistered"):
        audit.emit("forge_bundle.nonexistent", tenant_id="_default")


def test_disallowed_field_raises(tmp_corvin_home):
    with pytest.raises(audit.ForgeBundleAuditError, match="not allow-listed"):
        audit.emit("forge_bundle.exported", tenant_id="_default", secret_payload="x")


def test_emit_writes_a_chained_record(tmp_corvin_home):
    digest = audit.emit(
        "forge_bundle.exported", tenant_id="_default",
        bundle_id="b", bundle_version="1.0.0", artifact_count=1, total_bytes=100,
    )
    assert digest
    chain = tmp_corvin_home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    assert chain.exists()
