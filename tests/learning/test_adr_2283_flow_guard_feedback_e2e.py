"""ADR-2283 decision 2 — the flow-guard learning route is audited and strengthen-only.

Drives the REAL console router (``/v1/console/flow/...``) over HTTP with a real console
session (``tests/learning/console_client.py``) inside a sandboxed ``CORVIN_HOME`` whose
core chain is redirected to the sandbox. Until 2026-10-25 the route built its
``LearningIntegration`` with ``audit_backend=None`` (nothing reached the chain),
``result="success"`` / ``"approved"`` added an ALLOW rule, and ``GET /flow/audit``
returned the operator's free-text ``reasoning``.

Asserted here:
  * a DENY-strengthening outcome is recorded as ONE hash-chained ``skill.feedback``
    record (metadata only — the reasoning text is nowhere in the chain) BEFORE the
    policy changes, and the policy then holds a DENY rule;
  * ``success`` / ``approved`` (would add or raise an ALLOW rule) and unknown results
    are refused with 422 and leave no ALLOW rule and no record;
  * ``GET /flow/audit`` returns no free text;
  * when the chain cannot commit the route refuses (503) and the policy is unchanged.
"""
from __future__ import annotations

import json
import os

import pytest

from tests.learning.console_client import console_client

SECRET_REASON = "Contained Jane Roe's home address 12 Elm Street"
DC = "personal_email"
DEST = "openai/gpt-5"


def _feedback(sb, result: str, reasoning: str = SECRET_REASON, data_class: str = DC):
    return sb.client.post(
        "/v1/console/flow/feedback",
        json={"data_class": data_class, "destination_engine": DEST,
              "result": result, "reasoning": reasoning},
        headers=sb.csrf_headers,
    )


def _feedback_records(sb):
    return [r for r in sb.chain_records() if r.get("event", r.get("event_type")) == "skill.feedback"]


def _rules(sb):
    r = sb.client.get("/v1/console/flow/policy")
    assert r.status_code == 200, r.text
    return r.json()["rules"]


@pytest.fixture()
def sb(tmp_path):
    with console_client(tmp_path) as sandbox:
        yield sandbox


def test_deny_strengthening_feedback_is_chained_metadata_only(sb):
    r = _feedback(sb, "rejected")
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["status"] == "recorded"

    recs = _feedback_records(sb)
    assert len(recs) == 1, [x.get("event_type") for x in sb.chain_records()]
    details = recs[0]["details"]
    assert details["data_class"] == DC
    assert details["destination_engine"] == DEST
    assert details["result"] == "rejected"
    assert details["rule_effect"] == "deny_strengthened"
    assert details["event_id"] == body["event_id"]
    assert details.get("audit_ref")
    assert "reasoning" not in details
    assert not details.get("_dropped_fields"), details
    # No free text anywhere on the chain file.
    assert "Jane Roe" not in sb.chain.read_text()

    rules = _rules(sb)
    assert [(x["data_class"], x["destination_engine"], x["decision"]) for x in rules] == [
        (DC, DEST, "deny")]

    # A leak outcome strengthens further (same DENY rule) and is recorded too.
    r = _feedback(sb, "pii_leak_detected")
    assert r.status_code == 201, r.text
    assert len(_feedback_records(sb)) == 2
    assert all(x["decision"] == "deny" for x in _rules(sb))

    from forge import security_events
    ok, problems = security_events.verify_chain(sb.chain)
    assert ok, problems


@pytest.mark.parametrize("result", ["success", "approved"])
def test_feedback_that_would_add_or_raise_an_allow_rule_is_refused(sb, result):
    r = _feedback(sb, result)
    assert r.status_code == 422, r.text
    assert r.json()["detail"]["reason"] == "feedback_may_only_strengthen"
    assert not any(x["decision"] == "allow" for x in _rules(sb))
    assert _feedback_records(sb) == []

    # Also with an existing DENY rule: nothing may raise ALLOW over it.
    assert _feedback(sb, "rejected").status_code == 201
    assert _feedback(sb, result).status_code == 422
    assert [x["decision"] for x in _rules(sb)] == ["deny"]
    assert len(_feedback_records(sb)) == 1


def test_unknown_result_is_refused(sb):
    r = _feedback(sb, "looks_fine_to_me")
    assert r.status_code == 422, r.text
    assert _rules(sb) == []
    assert _feedback_records(sb) == []


def test_audit_view_returns_no_free_text(sb):
    assert _feedback(sb, "rejected").status_code == 201
    r = sb.client.get("/v1/console/flow/audit")
    assert r.status_code == 200, r.text
    events = r.json()["audit_events"]
    assert len(events) == 1
    assert "reasoning" not in events[0]
    assert "Jane Roe" not in r.text


def test_feedback_is_refused_when_the_chain_cannot_commit(sb, tmp_path):
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("x")
    prev = os.environ.get("VOICE_AUDIT_PATH")
    os.environ["VOICE_AUDIT_PATH"] = str(blocker / "audit.jsonl")
    try:
        r = _feedback(sb, "rejected")
    finally:
        os.environ["VOICE_AUDIT_PATH"] = prev
    assert r.status_code == 503, r.text
    assert r.json()["detail"]["reason"] == "audit_not_committed"
    # Audit-first: no record, no policy change, no history entry.
    assert _rules(sb) == []
    assert _feedback_records(sb) == []
    audit = sb.client.get("/v1/console/flow/audit").json()
    assert audit["audit_events"] == []
    assert json.dumps(audit).find("Jane Roe") == -1
