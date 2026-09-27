"""core.vibe default audit backend writes THE tenant chain (was: fabricated hash).

Adversarial review 2026-09-27: LossSignalEmitter / LearningLoopTracker defaulted
to ``_NoOpAudit``, which returned ``"test_hash_<type>"`` and wrote nothing, and
the signal getters returned every tenant's signals.
"""
import json

import pytest

from core.vibe.learning_loop_tracker import LearningLoopTracker
from core.vibe.loss_signal_emitter import LossSignalEmitter
from forge import paths as forge_paths
from forge import security_events


def _chain():
    p = forge_paths.tenant_audit_chain("_default")
    return [json.loads(l) for l in p.read_text().splitlines() if l.strip()]


def test_loss_signal_is_chained_with_the_real_hash():
    em = LossSignalEmitter()
    sig = em.emit_latency_signal(tenant_id="_default", p99_baseline=100.0, p99_current=150.0)
    assert sig is not None
    rec = [r for r in _chain() if r["event_type"] == "vibe.loss_signal"][-1]
    assert sig.hash == rec["hash"] and not sig.hash.startswith("test_hash_")
    assert rec["details"]["metric_name"] == sig.metric_name
    assert "_dropped_fields" not in rec["details"]
    assert security_events.verify_chain(forge_paths.tenant_audit_chain("_default"))[0]


def test_getters_are_tenant_scoped(monkeypatch):
    em = LossSignalEmitter(audit_backend=type("B", (), {
        "write_event": lambda self, e: "h", "last_hash": lambda self: ""})())
    em.emit_latency_signal(tenant_id="tenant-a", p99_baseline=100.0, p99_current=200.0)
    em.emit_latency_signal(tenant_id="tenant-b", p99_baseline=100.0, p99_current=200.0)
    assert {s.tenant_id for s in em.get_recent_signals(tenant_id="tenant-a")} == {"tenant-a"}
    assert {s.tenant_id for s in em.get_signals_by_type("latency", tenant_id="tenant-b")} == {"tenant-b"}
    with pytest.raises(TypeError):
        em.get_critical_signals()  # tenant is required


def test_tracker_default_backend_chains():
    tr = LearningLoopTracker()
    pt = tr.track_confidence_point(tenant_id="_default", confidence=0.8, sample_count=10)
    rec = [r for r in _chain() if r["event_type"] == "vibe.convergence_point"][-1]
    assert pt.hash == rec["hash"]
