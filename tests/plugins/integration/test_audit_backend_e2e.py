"""E2E: an ``audit_backend`` plugin, loaded through the REAL registry, receives a
COPY of a REAL core audit event — and can never disturb the core write.

The previous version of this file defined its own ``AuditBackendPlugin`` class
and tested that; it proved nothing about CorvinOS. This one goes through the
real boundaries: ``corvin_plugins.registry.register`` (lifecycle + provider
slot), ``corvin_plugins.providers.audit_backend`` (the ADR-0233 fan-out
registry) and ``audit.audit_event`` (the hash-chained core writer that fans out
after its own commit). The chain is isolated per test by ``VOICE_AUDIT_PATH``.
"""
from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "plugins", _REPO / "operator" / "bridges" / "shared",
           _REPO / "operator" / "forge", _REPO / "operator"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from corvin_plugins import bootstrap  # noqa: E402
from corvin_plugins.protocol import HealthStatus, PluginContext  # noqa: E402
from corvin_plugins.providers import audit_backend as ab  # noqa: E402
from corvin_plugins.registry import PluginRegistry  # noqa: E402


class _CollectingBackend:
    """A minimal REAL audit_backend plugin: records every copy it is handed."""

    plugin_id = "test:collecting-audit-backend"
    plugin_type = "audit_backend"
    version = "1.0.0"
    display_name = "Collecting backend"

    def __init__(self, *, raise_on: str | None = None) -> None:
        self.received: list[dict] = []
        self.got = threading.Event()
        self._raise_on = raise_on

    def on_load(self, ctx: PluginContext) -> None:
        ctx.audit_registry.set_active(self)

    def on_unload(self) -> None:
        pass

    def health_check(self) -> HealthStatus:
        return HealthStatus(ok=True, details={"received": len(self.received)})

    def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
        if self._raise_on and event_type == self._raise_on:
            raise RuntimeError("hostile backend")
        self.received.append({
            "event_type": event_type, "details": dict(details),
            "severity": severity, "tenant_id": tenant_id,
        })
        self.got.set()

    def verify_chain(self):
        return HealthStatus(ok=True)

    def enforce_retention(self, max_age_days, *, tenant_id="_default"):
        return {"deleted": 0}


@pytest.fixture(autouse=True)
def _clean_slot():
    ab.clear()
    yield
    ab.clear()


def _load(backend, tmp_path, tenant="_default"):
    reg = PluginRegistry()
    ctx = bootstrap.build_context(
        plugin_id=backend.plugin_id, tenant_id=tenant, corvin_home=tmp_path, config={}
    )
    reg.register(backend, ctx)
    assert ab.get_active() is backend, "on_load did not take the provider slot"
    return reg, ctx


def test_plugin_loaded_through_the_registry_receives_a_core_event_copy(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    import audit

    backend = _CollectingBackend()
    reg, _ctx = _load(backend, tmp_path)

    audit.audit_event("e2e.probe", details={"reason": "probe"}, tenant_id="_default")
    ab.drain_now(timeout=5.0)
    assert backend.got.wait(5.0), "backend never received the fan-out copy"

    copies = [r for r in backend.received if r["event_type"] == "e2e.probe"]
    assert len(copies) == 1, backend.received
    assert copies[0]["tenant_id"] == "_default"
    assert copies[0]["details"]["reason"] == "probe"

    # The CORE chain committed regardless of the backend: it is the record of
    # truth, the backend only ever gets a copy after the write.
    chain = (tmp_path / "audit.jsonl").read_text()
    assert '"e2e.probe"' in chain

    reg.unregister(backend.plugin_id)
    assert ab.get_active() is None, "unload must release the slot"


def test_hostile_backend_cannot_break_the_core_write(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.delenv("CORVIN_TENANT_ID", raising=False)
    import audit

    backend = _CollectingBackend(raise_on="e2e.boom")
    _load(backend, tmp_path)

    audit.audit_event("e2e.boom", details={"reason": "probe"}, tenant_id="_default")
    ab.drain_now(timeout=5.0)

    chain = (tmp_path / "audit.jsonl").read_text()
    assert '"e2e.boom"' in chain, "core write must commit even when the backend raises"
    assert ab.failure_count() >= 1


def test_tenant_id_travels_with_every_copy(tmp_path):
    backend = _CollectingBackend()
    _load(backend, tmp_path, tenant="acme")
    ab.fanout("t.a1", {"k": "a1"}, tenant_id="tenant-a")
    ab.fanout("t.a2", {"k": "a2"}, tenant_id="tenant-a")
    ab.fanout("t.b1", {"k": "b1"}, tenant_id="tenant-b")
    ab.drain_now(timeout=5.0)
    by_tenant = {}
    for r in backend.received:
        by_tenant.setdefault(r["tenant_id"], []).append(r["event_type"])
    assert by_tenant == {"tenant-a": ["t.a1", "t.a2"], "tenant-b": ["t.b1"]}


def test_backend_cannot_mutate_the_details_the_core_wrote(tmp_path):
    class _Mutating(_CollectingBackend):
        def fanout(self, event_type, details, *, severity="INFO", tenant_id="_default"):
            details["injected"] = True
            super().fanout(event_type, details, severity=severity, tenant_id=tenant_id)

    backend = _Mutating()
    _load(backend, tmp_path)
    original = {"k": "v"}
    ab.fanout("t.mut", original, tenant_id="_default")
    ab.drain_now(timeout=5.0)
    assert original == {"k": "v"}, "the backend was handed the caller's dict, not a copy"


def test_health_reports_what_the_backend_received(tmp_path):
    backend = _CollectingBackend()
    reg, _ctx = _load(backend, tmp_path)
    ab.fanout("t.h", {}, tenant_id="_default")
    ab.drain_now(timeout=5.0)
    health = reg.health_check_all()[backend.plugin_id]
    # register() itself audits plugin.loaded through the real chain, which fans
    # out to this very backend — so "received" counts that copy too.
    assert health.ok and health.details["received"] >= 1
    assert [r["event_type"] for r in backend.received].count("t.h") == 1
