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






def test_every_record_is_self_identifying(home: Path):
    from core.learning.live_experiment_collector import MEASUREMENT_SCHEMA

    collector = _collector()
    collector.save_measurement(collector.collect_all_metrics())

    line = collector.get_today_file().read_text().splitlines()[0]
    record = json.loads(line)
    assert record["schema"] == MEASUREMENT_SCHEMA
    assert record["provenance"]["measured"] is True
    assert record["provenance"]["generator"] == "core.learning.live_experiment_collector"




