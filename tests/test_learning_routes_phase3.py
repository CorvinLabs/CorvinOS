"""Learning console routes (ADR-0629 operator interface) — REAL boundary, REAL data.

Drives ``/v1/console/learning/{status,metrics,checkpoint,audit,override,rollback,
grade}`` and the rating routes through the real console router with a real
session cookie (``tests/learning/console_client.py``) and asserts against what
the audit-first ``EventStore`` and the core chain actually contain.

Replaces the former file, whose 36 tests asserted the hard-coded placeholder
answers (``alpha_core == 0.1``, ``convergence_percent == 87.5``, ``override →
"success"``) through a ``client`` fixture that did not exist (adversarial
review F-L2 + stale-test list).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.learning.console_client import boot_learning_registry, console_client, write_outcome


# ── status ───────────────────────────────────────────────────────────────────


def test_status_requires_session(tmp_path: Path):
    with console_client(tmp_path) as sb:
        sb.client.cookies.clear()
        assert sb.client.get("/v1/console/learning/status").status_code == 401


def test_status_is_computed_from_the_event_store(tmp_path: Path):
    with console_client(tmp_path) as sb:
        r = sb.client.get("/v1/console/learning/status")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["status"] == "no_data"
        assert body["source"] == "event_store"
        assert body["recent_outcomes"] == {"window": 50, "total": 0, "successes": 0, "success_rate": None}
        assert body["outcome_loss"] is None
        for key in ("alpha_core", "convergence_percent", "loss_core"):
            assert key not in body, f"placeholder field {key} is back"

        write_outcome(sb, task_id="t1", success=True)
        write_outcome(sb, task_id="t2", success=True)
        write_outcome(sb, task_id="t3", success=False)

        body = sb.client.get("/v1/console/learning/status").json()
        assert body["status"] == "collecting"
        assert body["event_counts"]["outcome"] == 3
        assert body["recent_outcomes"]["total"] == 3
        assert body["recent_outcomes"]["successes"] == 2
        assert body["outcome_loss"] == pytest.approx(1 - 2 / 3)
        assert body["last_outcome_at"] is not None


def test_status_is_tenant_isolated(tmp_path: Path):
    with console_client(tmp_path) as sb:
        # an outcome of ANOTHER tenant, written under that tenant's home
        write_outcome(sb, task_id="foreign", success=True, tenant_id="acme-corp")
        body = sb.client.get("/v1/console/learning/status").json()
        assert body["tenant_id"] == "_default"
        assert body["event_counts"]["outcome"] == 0


# ── metrics ──────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("window,seconds", [("1h", 300), ("6h", 1800), ("24h", 7200)])
def test_metrics_series_is_bucketed_over_the_window(tmp_path: Path, window: str, seconds: int):
    with console_client(tmp_path) as sb:
        write_outcome(sb, task_id="t1", success=True)
        r = sb.client.get(f"/v1/console/learning/metrics?window={window}")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["window"] == window
        assert body["bucket_seconds"] == seconds
        assert len(body["points"]) == 12
        assert body["sample_count"] == 1
        assert sum(p["outcomes"] for p in body["points"]) == 1
        assert sum(p["successes"] for p in body["points"]) == 1
        assert body["points"][-1]["success_rate"] == 1.0


def test_metrics_invalid_window_is_400(tmp_path: Path):
    with console_client(tmp_path) as sb:
        assert sb.client.get("/v1/console/learning/metrics?window=7d").status_code == 400


# ── checkpoint / audit ───────────────────────────────────────────────────────


def test_checkpoints_are_the_real_skill_config_versions(tmp_path: Path):
    with console_client(tmp_path) as sb:
        r = sb.client.get("/v1/console/learning/checkpoint")
        assert r.status_code == 200, r.text
        assert r.json() == {"checkpoints": []}  # nothing learned yet → no versions, not a placeholder

        from core.skills.os_skills.skill_adapter import SkillAdapter

        adapter = SkillAdapter("os.delegation_router", sb.tenant_id)
        adapter.state.epoch = 51
        adapter.state.baseline_success_rate = 0.0
        adapter._persist()
        emitter = boot_learning_registry(sb)
        from core.learning.outcome_sink import emit_task_outcome

        for i in range(10):
            assert emit_task_outcome(tenant_id=sb.tenant_id, task_id=f"t{i}", status="completed", exit_code=0,
                                     duration_ms=10, engine="native", task_type="chat", emitter=emitter)
        emitter.stop()  # flush the worker so the optimizer epoch sees all ten
        r = sb.client.post(
            "/v1/console/learning/feedback",
            json={"task_id": "t9", "outcome_quality": "excellent", "would_repeat": True},
            headers=sb.csrf_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["current_version"] == "v1"

        body = sb.client.get("/v1/console/learning/checkpoint").json()
        assert [c["checkpoint_id"] for c in body["checkpoints"]] == ["v1"]
        assert body["checkpoints"][0]["skill_id"] == "os.delegation_router"
        assert "confidence_threshold" in body["checkpoints"][0]["config"]


def test_audit_trail_reads_learning_records_from_the_core_chain(tmp_path: Path):
    with console_client(tmp_path) as sb:
        assert sb.client.get("/v1/console/learning/audit").json()["count"] == 0
        event_id = write_outcome(sb, task_id="t1", success=True)
        on_disk = [e for e in sb.events_on_disk() if e["event_id"] == event_id]
        assert on_disk and on_disk[0]["audit_ref"]

        r = sb.client.get("/v1/console/learning/audit?limit=10")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["count"] == 1
        rec = body["events"][0]
        assert rec["event_type"] == "learning.outcome"
        assert rec["audit_ref"] == on_disk[0]["audit_ref"]
        assert rec["skill_id"] == "os.delegation_router"
        assert rec["hash"]
        assert body["chain_path"] == str(sb.chain)


# ── override / rollback: honest 501, CSRF-gated ──────────────────────────────


def test_override_and_rollback_are_501_not_fake_success(tmp_path: Path):
    with console_client(tmp_path) as sb:
        r = sb.client.post(
            "/v1/console/learning/override",
            json={"loop": "core", "param": "alpha", "new_value": 0.2, "reason": "x"},
            headers=sb.csrf_headers,
        )
        assert r.status_code == 501
        assert "config/rollback" in r.json()["detail"]
        r = sb.client.post("/v1/console/learning/rollback/v1", headers=sb.csrf_headers)
        assert r.status_code == 501


def test_every_learning_post_requires_csrf(tmp_path: Path):
    with console_client(tmp_path) as sb:
        posts = [
            ("/v1/console/learning/override", {"loop": "core", "param": "alpha", "new_value": 0.2, "reason": "x"}),
            ("/v1/console/learning/rollback/v1", None),
            ("/v1/console/learning/grade", {"pattern_id": "p", "grade": 0.5}),
            ("/v1/console/learning/note", {"pattern_id": "p", "text": "n"}),
            ("/v1/console/tools/t1/rating", {"rating": 5}),
            ("/v1/console/skills/s1/rating", {"rating": 5}),
            ("/v1/console/learning/patterns/p1/confirm", None),
            ("/v1/console/learning/metrics/export", {"format": "json", "window": "1h"}),
        ]
        for path, body in posts:
            r = sb.client.post(path, json=body)  # session cookie, NO csrf header
            assert r.status_code == 403, (path, r.status_code, r.text)


# ── grade / ratings: chained, free text never persisted ─────────────────────


def test_grade_is_chained_and_reason_is_not_persisted(tmp_path: Path):
    with console_client(tmp_path) as sb:
        r = sb.client.post(
            "/v1/console/learning/grade",
            json={"pattern_id": "pattern_x", "grade": 0.7, "reason": "SECRET-REASON hunter2"},
            headers=sb.csrf_headers,
        )
        assert r.status_code == 200, r.text
        event_id = r.json()["event_id"]
        on_disk = [e for e in sb.events_on_disk() if e["event_id"] == event_id]
        assert len(on_disk) == 1
        assert on_disk[0]["event_type"] == "feedback"
        assert on_disk[0]["signal"]["grade"] == 0.7
        assert on_disk[0]["signal"]["has_reason"] is True
        assert on_disk[0]["signal"]["reason_length"] == len("SECRET-REASON hunter2")
        assert on_disk[0]["audit_ref"]
        assert "hunter2" not in json.dumps(on_disk)
        assert "hunter2" not in sb.chain.read_text()
        chain = [c for c in sb.chain_records() if c.get("event_type") == "learning.feedback"]
        assert any(c["details"].get("audit_ref") == on_disk[0]["audit_ref"] for c in chain)


@pytest.mark.parametrize("kind", ["tools", "skills"])
def test_rating_persists_has_text_only(tmp_path: Path, kind: str):
    with console_client(tmp_path) as sb:
        r = sb.client.post(
            f"/v1/console/{kind}/entity_1/rating",
            json={"rating": 4, "feedback_text": "the operator's private remark", "task_id": "t1"},
            headers=sb.csrf_headers,
        )
        assert r.status_code == 200, r.text
        assert r.json()["feedback_stats"]["sample_count"] == 1
        events = [e for e in sb.events_on_disk() if e["event_type"] == "feedback"]
        assert len(events) == 1
        signal = events[0]["signal"]
        assert "feedback_text" not in signal
        assert signal["has_text"] is True
        assert signal["text_length"] == len("the operator's private remark")
        assert "private remark" not in json.dumps(events)
        assert events[0]["audit_ref"]

        r = sb.client.post(f"/v1/console/{kind}/entity_1/rating", json={"rating": 9}, headers=sb.csrf_headers)
        assert r.status_code == 400
