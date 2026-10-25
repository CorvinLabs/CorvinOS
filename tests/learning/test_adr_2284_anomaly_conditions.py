"""ADR-2284 decision 2 through the real learning host: stored data -> ``LearningHost.tick`` ->
one chained anomaly record -> ``GET /v1/console/api/learning/daemon``; edge-triggered (a second
tick over unchanged data records nothing).

Seeds are written through the production writers only: OUTCOME learning events through the
audit-first ``EventStore.write_event``, ``skill.executed`` records through the core chain writer
``forge.security_events.write_event`` (its ``ts`` argument places the baseline in the past).
"""
from __future__ import annotations

import asyncio
import json
import time

from tests.learning.console_client import console_client


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _tenant_chain(sb) -> list[dict]:
    chain = sb.tenant_home / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    return [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]


def _records(sb, name: str) -> list[dict]:
    return [r for r in _tenant_chain(sb) if name in (r.get("event"), r.get("event_type"))]


def _tick(host, now=None):
    host._last_anomaly_check = 0.0
    return _run(host.tick(now=now))


def _daemon_anomalies(sb) -> list[dict]:
    r = sb.client.get("/v1/console/api/learning/daemon")
    assert r.status_code == 200, r.text
    return r.json()["data"]["anomalies"]


def _write_outcomes(sb, skill_id: str, successes: list[bool]) -> None:
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent

    store = EventStore(sb.tenant_home, tenant_id=sb.tenant_id)
    for i, ok in enumerate(successes):
        store.write_event(LearningEvent.create(
            event_type=EventType.OUTCOME, skill_id=skill_id, tenant_id=sb.tenant_id,
            signal={"task_id": f"{skill_id}-{i}", "status": "completed" if ok else "failed",
                    "success": ok},
            lom="tests/learning/test_adr_2284_anomaly_conditions.py:_write_outcomes"))


def test_convergence_stall_is_recorded_once_and_shown_by_the_daemon_route(tmp_path):
    with console_client(tmp_path) as sb:
        # stalled: loss 0.4 in each of four windows of five
        _write_outcomes(sb, "os.stalled_probe", [False, False, True, True, True] * 4)
        # improving: losses 0.8 / 0.6 / 0.4 / 0.2 -> learning, not stalled
        _write_outcomes(sb, "os.improving_probe",
                        [False] * 4 + [True] + [False] * 3 + [True] * 2
                        + [False] * 2 + [True] * 3 + [False] + [True] * 4)
        # stalled shape but only 19 events -> not enough data
        _write_outcomes(sb, "os.short_probe", ([False, False, True, True, True] * 4)[:19])
        # good: loss 0.0 everywhere -> below the 0.2 floor
        _write_outcomes(sb, "os.good_probe", [True] * 20)

        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        _tick(host)
        recs = _records(sb, "skill.convergence_stall")
        assert len(recs) == 1, recs
        details = recs[0]["details"]
        assert details["skill_id"] == "os.stalled_probe"
        assert (details["sample_count"], details["window_count"]) == (20, 4)
        assert details["loss_min"] == details["loss_max"] == 0.4
        assert details["loss_spread"] == 0.0
        assert recs[0]["severity"] == "WARNING" and recs[0].get("hash")

        # edge-triggered: the same host and a restarted host record nothing new
        _tick(host)
        _tick(LearningHost(sb.tenant_id))
        assert len(_records(sb, "skill.convergence_stall")) == 1

        stall = [a for a in _daemon_anomalies(sb) if a["event"] == "skill.convergence_stall"]
        assert [(a["skill_id"], a["loss_min"]) for a in stall] == [("os.stalled_probe", 0.4)]


def _write_executions(sb, skill_id: str, latencies: list[float], ts: float) -> None:
    from core.deployment.audit_sink import _forge

    se, fp = _forge()
    chain = fp.tenant_audit_chain(sb.tenant_id)
    for i, latency in enumerate(latencies):
        se.write_event(chain, "skill.executed", tool=skill_id, ts=ts + i,
                       details={"skill_id": skill_id, "latency_ms": latency,
                                "tenant_id": sb.tenant_id, "status": "success"})


def test_latency_regression_is_recorded_once_from_the_chained_executions(tmp_path):
    with console_client(tmp_path) as sb:
        now = time.time()
        three_days_ago = now - 3 * 86400
        two_hours_ago = now - 2 * 3600
        # slower: p95 100 ms -> 200 ms (2x > 1.5x)
        _write_executions(sb, "os.slow_probe", [100.0] * 20, three_days_ago)
        _write_executions(sb, "os.slow_probe", [200.0] * 20, two_hours_ago)
        # steady: 100 -> 140 ms (1.4x) -> no anomaly
        _write_executions(sb, "os.steady_probe", [100.0] * 20, three_days_ago)
        _write_executions(sb, "os.steady_probe", [140.0] * 20, two_hours_ago)
        # slower but only 19 recent records -> not enough data
        _write_executions(sb, "os.thin_probe", [100.0] * 20, three_days_ago)
        _write_executions(sb, "os.thin_probe", [500.0] * 19, two_hours_ago)
        # baseline older than 7 days -> ignored
        _write_executions(sb, "os.old_probe", [10.0] * 20, now - 9 * 86400)
        _write_executions(sb, "os.old_probe", [500.0] * 20, two_hours_ago)

        from core.background.learning_host import LearningHost

        host = LearningHost(sb.tenant_id)
        _tick(host, now=now)
        recs = _records(sb, "skill.latency_regression")
        assert len(recs) == 1, recs
        d = recs[0]["details"]
        assert d["skill_id"] == "os.slow_probe"
        assert (d["p95_before_ms"], d["p95_after_ms"], d["ratio"]) == (100.0, 200.0, 2.0)
        assert (d["samples_before"], d["samples_after"]) == (20, 20)
        assert recs[0]["severity"] == "WARNING" and recs[0].get("hash")

        # edge-triggered: no second record from the same or a restarted host
        _tick(host, now=now + 1)
        _tick(LearningHost(sb.tenant_id), now=now + 2)
        assert len(_records(sb, "skill.latency_regression")) == 1

        lat = [a for a in _daemon_anomalies(sb) if a["event"] == "skill.latency_regression"]
        assert [(a["skill_id"], a["ratio"]) for a in lat] == [("os.slow_probe", 2.0)]
