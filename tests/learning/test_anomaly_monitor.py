"""ADR-2044 Phase 3 anomaly rules: each fires on its condition and stays silent just below it."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from types import SimpleNamespace

from core.learning import anomaly_monitor as m

NOW = 1_800_000_000.0


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def test_rejection_rate_needs_both_volume_and_rate():
    ten = [{"ts": NOW - 60, "subject": "s", "decision": "rejected" if i < 3 else "accepted"} for i in range(10)]
    assert [a.details["rate"] for a in m._rejection_anomalies(ten, NOW)] == [0.3]
    nine = ten[:9]
    assert m._rejection_anomalies(nine, NOW) == []
    two = [dict(d, decision="rejected" if i < 2 else "accepted") for i, d in enumerate(ten)]
    assert m._rejection_anomalies(two, NOW) == []          # exactly 20 % is not "more than"
    old = [dict(d, ts=NOW - m.REJECTION_WINDOW_S - 1) for d in ten]
    assert m._rejection_anomalies(old, NOW) == []


def test_oscillation_counts_versions_in_the_last_hour(tmp_path):
    def write(n):
        versions = [{"version_id": f"v{i}", "skill_id": "os.delegation_router",
                     "timestamp": _iso(NOW - 60 * (i + 1))} for i in range(n)]
        (tmp_path / "os_delegation_router_config.json").write_text(json.dumps({"versions": versions}))
    write(3)
    assert m._oscillation_anomalies(tmp_path, NOW) == []
    write(4)
    [a] = m._oscillation_anomalies(tmp_path, NOW)
    assert (a.skill_id, a.details["change_count"]) == ("os.delegation_router", 4)


def _ev(ts, conf, skill="os.flow_guard"):
    return SimpleNamespace(timestamp=_iso(ts), skill_id=skill, signal={"output": {"confidence": conf}})


def test_confidence_regression_compares_last_day_with_the_week_before():
    before = [_ev(NOW - 2 * 86400, 0.9) for _ in range(20)]
    drop = [_ev(NOW - 3600, 0.79) for _ in range(20)]
    [a] = m._regression_anomalies(before + drop, NOW)
    assert (a.details["confidence_before"], a.details["confidence_after"]) == (0.9, 0.79)
    small = [_ev(NOW - 3600, 0.81) for _ in range(20)]
    assert m._regression_anomalies(before + small, NOW) == []
    assert m._regression_anomalies(before + drop[:19], NOW) == []
    junk = [SimpleNamespace(timestamp=_iso(NOW - 3600), skill_id="x", signal={"output": {"confidence": True}})]
    assert m._regression_anomalies(junk, NOW) == []
