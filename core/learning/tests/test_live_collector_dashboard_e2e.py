"""Collector → dashboard, end to end (round-4 review, F4/F5).

The collector was rewritten to record only measured values; its ONLY reader was
not, and still indexed the pre-fix synthetic keys (``learning.loss_total``,
``learning.accuracy_routing``, ``system.latency_p99_ms``). Every honest record
therefore raised ``KeyError: 'loss_total'`` and the dashboard worked only on the
fabricated dataset. There was no test that ran the writer and the reader
together, which is exactly why the schema could drift apart.

These tests run the REAL collector against a REAL ``EventStore`` under a
throwaway ``CORVIN_HOME``, then hand the file it wrote to the REAL dashboard.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "corvin_home"
    root.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(root))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(root / "audit.jsonl"))
    return root


def _collector(tenant: str = "_default"):
    from core.learning.live_experiment_collector import LiveExperimentCollector

    return LiveExperimentCollector(tenant)


def _dashboard(tenant: str = "_default"):
    from core.learning.live_collection_dashboard import LiveCollectionDashboard

    return LiveCollectionDashboard(tenant)


def test_collector_output_is_readable_by_the_dashboard(home: Path):
    """The writer's schema and the reader's schema are the same schema."""
    collector = _collector()
    collector.save_measurement(collector.collect_all_metrics())

    report = _dashboard().generate_text_report(hours=1)

    assert "LIVE COLLECTION DASHBOARD" in report
    assert "No measurements collected yet" not in report
    assert "1 measurement(s)" in report
    # Provenance is surfaced, not silently dropped.
    assert "event_store" in report


def test_every_record_is_self_identifying(home: Path):
    from core.learning.live_experiment_collector import MEASUREMENT_SCHEMA

    collector = _collector()
    collector.save_measurement(collector.collect_all_metrics())

    line = collector.get_today_file().read_text().splitlines()[0]
    record = json.loads(line)
    assert record["schema"] == MEASUREMENT_SCHEMA
    assert record["provenance"]["measured"] is True
    assert record["provenance"]["generator"] == "core.learning.live_experiment_collector"


def test_pre_fix_synthetic_records_are_ignored_not_aggregated(home: Path):
    """A record with no ``schema`` marker is ``random.gauss`` output — quarantine it.

    This is the file that actually sits on the operator's disk
    (``measurements_20260906.jsonl``): nothing in it distinguishes it from a
    measurement, and it is the dataset the documented "export for papers" path
    would consume.
    """
    collector = _collector()
    synthetic = {
        "timestamp": collector.collect_all_metrics()["timestamp"],
        "unix_time": 1,
        "learning": {"loss_total": 0.1, "accuracy_routing": 0.95, "convergence_rate": 0.81},
        "system": {"latency_p99_ms": 48.2, "cpu_usage_percent": 52.8},
    }
    collector.get_today_file().write_text(json.dumps(synthetic) + "\n")

    dash = _dashboard()
    assert dash.load_recent_measurements(hours=1) == []
    # ...and the reader says so instead of crashing on the legacy keys.
    assert "No measurements collected yet" in dash.generate_text_report(hours=1)


def test_a_real_outcome_reaches_the_dashboard_as_a_measured_loss(home: Path):
    """One recorded task failure ⇒ outcome_loss 1.0 in the rendered report."""
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    from core.paths.tenant import tenant_home

    store = EventStore(tenant_home("_default"), tenant_id="_default")
    store.write_event(
        LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id="os.delegation_router",
            tenant_id="_default",
            signal={"task_id": "t1", "status": "failed", "success": False},
            lom="core/learning/tests/test_live_collector_dashboard_e2e.py",
        )
    )

    collector = _collector()
    measurement = collector.collect_all_metrics()
    assert measurement["learning"]["outcome_loss"] == 1.0
    collector.save_measurement(measurement)

    report = _dashboard().generate_text_report(hours=1)
    assert "1.0000" in report
    assert "outcome" in report
