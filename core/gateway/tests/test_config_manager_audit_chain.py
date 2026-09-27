"""The gateway's config manager must not corrupt THE tenant audit chain.

891dd38f1 wired ``CentralizedConfigManager.create_with_audit(tenant_audit_chain())``
into the gateway lifespan. That binds ``AuditChainWriter`` — a second hash
scheme — to the canonical forge chain: its first config event made
``security_events.verify_chain`` report the record as tampered, which is what the
ADR-0232 boot tripwire checks. The manager the gateway lifespan builds
(``config_audit.build_config_manager``) must write through the forge writer,
so the chain still verifies after a config event.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO, _REPO / "core" / "gateway", _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def test_config_event_keeps_tenant_chain_verifiable(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from forge import paths as fp
    from forge import security_events as se
    from corvin_gateway import config_audit as gw

    chain = fp.tenant_audit_chain("_default")
    chain.parent.mkdir(parents=True, exist_ok=True)
    se.write_event(chain, "audit.chain_anchor_written", details={}, hash_chain=True)

    mgr = gw.build_config_manager()
    mgr._log_audit(tenant_id="_default", event_type="config.set_rejected",
                   details={"reason": "validation_failed"}, severity="warning")
    se.write_event(chain, "audit.chain_anchor_written", details={}, hash_chain=True)

    ok, problems = se.verify_chain(chain)
    assert ok, problems
    lines = chain.read_text().splitlines()
    assert len(lines) == 3 and '"config.set_rejected"' in lines[1]


def test_config_event_lands_on_the_events_own_tenant_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from forge import paths as fp
    from corvin_gateway import config_audit as gw

    gw.build_config_manager()._log_audit(
        tenant_id="acme", event_type="config.set_success", details={}, severity="info")
    assert fp.tenant_audit_chain("acme").exists()
    assert not fp.tenant_audit_chain("_default").exists()
