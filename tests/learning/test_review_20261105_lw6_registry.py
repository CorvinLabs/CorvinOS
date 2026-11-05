"""Review 2026-11-05 LW-6: the learning retention alert must keep its severity and fields.

``event_persistence.EventStore.cleanup_old_events`` emits
``learning.cross_tenant_contamination_detected`` (a partition holding another tenant's
records) and ``learning.retention`` with ``contaminated_partitions``. Neither field set
was in ``_EVENT_ALLOWLIST`` and the alert had no ``EVENT_SEVERITY`` entry: it was written
at INFO with its details cut to the vocabulary floor. ``cleanup_old_events`` has no
production caller today (retention is not scheduled), so this is pinned through the REAL
chain writer, not through a call site.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "corvin_operator" / "forge"))

from forge import security_events  # noqa: E402


def _last_record(chain: Path) -> dict:
    return json.loads(chain.read_text(encoding="utf-8").splitlines()[-1])


def test_lw6_contamination_alert_is_critical_and_keeps_its_fields(tmp_path):
    chain = tmp_path / "audit.jsonl"
    security_events.write_event(
        chain, "learning.cross_tenant_contamination_detected",
        details={"tenant_id": "_default", "file": "events_2026-11-01.jsonl",
                 "cross_tenant_count": 3, "foreign_tenants": ["other-co"]},
    )
    rec = _last_record(chain)
    assert rec["severity"] == "CRITICAL", rec
    details = rec["details"]
    assert details["cross_tenant_count"] == 3, details
    assert details["foreign_tenants"] == ["other-co"], details
    assert details["file"] == "events_2026-11-01.jsonl", details
    assert "_dropped_fields" not in details, details


def test_lw6_retention_record_keeps_the_contaminated_partition_count(tmp_path):
    chain = tmp_path / "audit.jsonl"
    security_events.write_event(
        chain, "learning.retention",
        details={"retention_days": 90, "deleted_count": 0, "partitions_rewritten": 0,
                 "contaminated_partitions": 2},
    )
    details = _last_record(chain)["details"]
    assert details["contaminated_partitions"] == 2, details
    assert "_dropped_fields" not in details, details
