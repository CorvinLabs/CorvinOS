"""ADR-0562 D2: a refused cross-tenant record is chained under the CONTEXT tenant - never appended to
the foreign tenant's chain the caller resolved (review 2026-10-12, AUD-2)."""
from __future__ import annotations

import json

import pytest


def test_refusal_goes_to_the_process_tenants_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.setenv("CORVIN_TENANT_ID", "other")
    from corvin_operator.forge.forge import paths, security_events as se

    foreign = paths.tenant_audit_chain("_default")
    foreign.parent.mkdir(parents=True, exist_ok=True)
    with pytest.raises(se.AuditTenantMismatch):
        se.write_event(foreign, "learning.regeneration", details={"tenant_id": "_default", "stage": "x"})

    assert not foreign.exists() or "audit.tenant_mismatch" not in foreign.read_text(), \
        "the refusal was written into the FOREIGN tenant's chain"
    own = paths.tenant_audit_chain("other")
    recs = [json.loads(l) for l in own.read_text().splitlines() if l.strip()]
    assert [r["event_type"] for r in recs][-1] == "audit.tenant_mismatch"
    assert recs[-1]["details"]["dropped_event_type"] == "learning.regeneration"
    assert se.verify_chain(own)[0] is True
