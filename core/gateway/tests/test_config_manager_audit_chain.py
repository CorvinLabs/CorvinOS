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
    import json
    types = [json.loads(ln)["event_type"]
             for ln in chain.read_text(encoding="utf-8").splitlines()]
    # A fresh CORVIN_HOME mints its instance signing key on the first signed
    # write and records that on the chain (``instance.key_rotated``); it is not
    # a config record, so it is not counted here.
    types = [t for t in types if t != "instance.key_rotated"]
    assert types == ["audit.chain_anchor_written", "config.set_rejected",
                     "audit.chain_anchor_written"], types


def test_config_event_lands_on_the_events_own_tenant_chain(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from forge import paths as fp
    from corvin_gateway import config_audit as gw

    gw.build_config_manager()._log_audit(
        tenant_id="acme", event_type="config.set_success", details={}, severity="info")
    import json
    from forge import security_events as se
    acme = fp.tenant_audit_chain("acme")
    assert acme.exists()
    assert not fp.tenant_audit_chain("_default").exists()
    recs = [json.loads(ln) for ln in acme.read_text(encoding="utf-8").splitlines()]
    assert [r["event_type"] for r in recs if r["event_type"].startswith("config.")] \
        == ["config.set_success"]
    # The chain is the tenant: no foreign tenant tag (the chokepoint refuses one).
    assert all("tenant_id" not in (r.get("details") or {}) for r in recs
               if r["event_type"] == "config.set_success")
    ok, problems = se.verify_chain(acme)
    assert ok, problems
