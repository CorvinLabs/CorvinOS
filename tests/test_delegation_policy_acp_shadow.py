"""ACP L5 shadow wiring in ``delegation_policy.resolve_worker_engine`` (2026-09-06, F1).

Proves, through the REAL shared routing entry point:
* the routing answer is byte-for-byte the bundled rule's (shadow never overrides);
* a booted registry receives one ``os.delegation_router`` execution per call,
  carrying the bundled engine, and the learning store gets the ``skill_executed``
  event — hash-chained (audit-first store);
* an un-booted process, or a Skill failure, leaves routing untouched.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
for p in (REPO / "operator" / "bridges" / "shared", REPO / "operator" / "forge", REPO):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import delegation_policy as dp  # noqa: E402

TENANT = "_default"


def _wait(pred, timeout=3.0):
    for _ in range(int(timeout / 0.05)):
        if pred():
            return True
        time.sleep(0.05)
    return pred()


@pytest.fixture
def booted(tmp_path: Path, monkeypatch):
    """A booted ACP registry whose learning emitter writes tmp_path (audit-first)."""
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    from core.learning.event_emitter import EventEmitter
    from core.learning.event_store import EventStore
    from core.skills.boot import boot_skills
    from core.skills.skill_registry_phase1 import LearningEmitterBackend

    store = EventStore(tmp_path / "tenant")
    emitter = EventEmitter(store)
    audit: list[tuple[str, dict]] = []
    boot_skills(
        TENANT,
        audit_emit=lambda et, d: audit.append((et, d)),
        learning_backend=LearningEmitterBackend(emitter, session_id="test"),
    )
    yield store, emitter, audit
    emitter.stop(timeout=5.0)


def _call(**kw) -> str:
    base = dict(mode="native", force_delegate=False, is_big_data=False, tde_available=False, quota_ok=False, tenant_id=TENANT)
    base.update(kw)
    return dp.resolve_worker_engine(**base)


class TestShadowWiring:
    def test_routing_answer_is_unchanged_and_shadow_record_lands(self, booted):
        store, emitter, audit = booted
        assert _call() == "native"
        assert _call(force_delegate=True) == "acs"
        assert _call(mode="tde", tde_available=True, quota_ok=True) == "tde"

        assert _wait(lambda: store.count_events(TENANT) >= 3)
        emitter.stop(timeout=5.0)
        from core.learning.learning_events import EventType

        events = store.query_events(TENANT, event_type=EventType.SKILL_EXECUTED, skill_id="os.delegation_router")
        assert len(events) == 3
        outputs = [e.signal["output"] for e in events]
        assert all(o["shadow"] is True for o in outputs)
        assert sorted(o["bundled_engine"] for o in outputs) == ["acs", "native", "tde"]
        assert all(o["engine"] in ("claude-haiku-4", "claude-sonnet-4", "claude-opus-5") for o in outputs)
        assert all(e.lom and "delegation_policy" in e.lom for e in events)
        # the core audit chain got the skill execution AND the learning record
        assert sum(1 for et, _ in audit if et == "skill.executed") == 3  # CoreAuditBackend dots the type
        chain = Path(__import__("os").environ["VOICE_AUDIT_PATH"])
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        assert sum(1 for r in recs if r.get("event_type") == "learning.skill_executed") == 3

    def test_unbooted_process_routes_without_shadow(self, monkeypatch):
        from core.skills import skill_registry_phase1 as reg

        monkeypatch.setattr(reg, "_global_registry", None)
        assert _call() == "native"
        assert reg._global_registry is None  # the shadow path must not create a phantom registry

    def test_skill_failure_never_alters_routing(self, booted, monkeypatch):
        store, emitter, audit = booted
        from core.skills.skill_registry_phase1 import get_registry

        def boom(*_a, **_k):
            raise RuntimeError("skill exploded")

        monkeypatch.setattr(get_registry(), "execute", boom)
        assert _call(is_big_data=True) == "acs"

    def test_foreign_tenant_is_refused_by_registry_not_by_routing(self, booted):
        store, emitter, audit = booted
        assert _call(tenant_id="other_tenant") == "native"
        # registry whitelist (fail-closed) → error result, nothing in the store for that tenant
        emitter.stop(timeout=5.0)
        assert store.count_events("other_tenant") == 0
