"""Regressions for ``core.learning.learning_loop_audit_integration``
(adversarial review 2026-09-27).

* aware ISO timestamps (``...Z``) crashed ``_compute_status`` with TypeError
  (aware minus naive ``utcnow()``);
* the core writer's ``ts`` (epoch) records were never read;
* the default chain path was hand-composed and ignored ``CORVIN_HOME``;
* every skill execution counted toward every loop.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.learning.learning_loop_audit_integration import AuditQueryHelper

_REPO = Path(__file__).resolve().parents[3]


def test_aware_timestamp_does_not_crash():
    now = datetime.now(timezone.utc)
    ev = {"timestamp": now.isoformat().replace("+00:00", "Z")}
    assert AuditQueryHelper._compute_status(0.9, ev) == "active"          # was TypeError
    ev = {"timestamp": (now - timedelta(hours=30)).isoformat()}
    assert AuditQueryHelper._compute_status(0.9, ev) == "dormant"
    assert AuditQueryHelper._compute_status(0.9, {"timestamp": "garbage"}) == "unknown"
    assert AuditQueryHelper._compute_status(0.9, {"ts": now.timestamp()}) == "active"


def test_reads_real_chain_records_for_the_loop_only(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    fp = str(_REPO / "corvin_operator" / "forge")
    if fp not in sys.path:
        sys.path.insert(0, fp)
    from forge import paths, security_events as se
    from core.paths.tenant import tenant_audit_chain

    chain = paths.tenant_audit_chain("_default")
    assert chain == tenant_audit_chain("_default")
    chain.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(3):
        se.write_event(chain, "skill.executed", details={"skill_id": "os.delegation_router"})
    se.write_event(chain, "skill.executed", details={"skill_id": "os.other"})

    h = AuditQueryHelper.compute_loop_health_from_audit(
        loop_id="core:os.delegation_router", event_source="SkillExecutedEvent.confidence_score")
    assert h["event_count_7d"] == 3
    assert h["health_score"] == pytest.approx(3 / 7)
    assert h["status"] == "degrading"
    assert h["last_event_ts"].endswith("Z")

    none = AuditQueryHelper.compute_loop_health_from_audit(
        loop_id="plugin:unrelated", event_source="x")
    assert none["event_count_7d"] == 0 and none["status"] == "dormant"


def test_missing_chain_is_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    h = AuditQueryHelper.compute_loop_health_from_audit(loop_id="a:b", event_source="x")
    assert h["status"] == "unknown" and h["health_score"] is None
