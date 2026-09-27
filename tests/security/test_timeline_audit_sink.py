"""video_producer timeline audit sink writes THE tenant chain (not memory).

Adversarial review 2026-09-27: ``_emit_audit_event`` built its own "hash chain"
over an in-memory list and wrote nothing to the audit trail.
"""
import json

import pytest

timeline = pytest.importorskip("core.skills.video_producer.api.timeline")

from forge import paths as forge_paths  # noqa: E402
from forge import security_events  # noqa: E402


def test_timeline_action_is_written_to_the_tenant_chain():
    timeline._emit_audit_event(
        "frame_status_changed",
        {"frameId": "f1", "status": "completed", "progress": 100, "metadata": {"x": "secret text"}},
        "task-9",
    )
    chain = forge_paths.tenant_audit_chain("_default")
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    rec = [r for r in recs if r["event_type"] == "video_producer.timeline_event"][-1]
    assert rec["details"]["action"] == "frame_status_changed"
    assert rec["details"]["task_id"] == "task-9"
    assert rec["details"]["frame_id"] == "f1"
    assert rec["details"]["progress"] == 100
    assert "secret text" not in chain.read_text()
    # the per-process index carries the REAL chain hash, not a private one
    assert timeline._AUDIT_EVENTS[-1]["hash"] == rec["hash"]
    assert security_events.verify_chain(chain)[0]
