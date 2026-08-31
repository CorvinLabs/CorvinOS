"""Tests for the compliance-reports audit emitter — allow-list + metadata-only."""
from __future__ import annotations

import json

import pytest

from corvin_compliance_reports import audit as cr_audit


def _read_chain(chain_path) -> list[dict]:
    if not chain_path.exists():
        return []
    return [
        json.loads(line)
        for line in chain_path.read_text().splitlines()
        if line.strip()
    ]


def test_report_generated_happy(sandbox_home, chain_path):
    cr_audit.report_generated(
        report_type="ai_act_art_50",
        tenant_id="_default",
        period_start_ts=1_700_000_000,
        period_end_ts=1_700_001_000,
        total_events=42,
        chain_intact=True,
        anchor_hash="deadbeef" * 8,
        page_count_estimate=12,
    )
    events = _read_chain(chain_path)
    assert len(events) == 1
    assert events[0]["event_type"] == "compliance.report_generated"
    assert events[0]["severity"] == "INFO"
    d = events[0]["details"]
    assert d["tenant_id"] == "_default"
    assert d["total_events"] == 42
    assert d["anchor_hash"] == "deadbeef" * 8


def test_report_generated_rejects_unknown_type(sandbox_home):
    with pytest.raises(cr_audit.ComplianceAuditFieldNotAllowed):
        cr_audit.report_generated(
            report_type="invented_report",
            tenant_id="_default",
            period_start_ts=0, period_end_ts=0,
            total_events=0, chain_intact=True, anchor_hash="",
        )


def test_emit_rejects_forbidden_fields(sandbox_home):
    """report_body / customer_id / token are forbidden."""
    with pytest.raises(cr_audit.ComplianceAuditFieldNotAllowed):
        cr_audit._validate_details(
            "compliance.report_generated",
            {"report_body": "X" * 1000},
        )
    with pytest.raises(cr_audit.ComplianceAuditFieldNotAllowed):
        cr_audit._validate_details(
            "compliance.report_generated",
            {"customer_id": "full-leak"},
        )


def test_emit_rejects_off_allowlist(sandbox_home):
    with pytest.raises(cr_audit.ComplianceAuditFieldNotAllowed):
        cr_audit._validate_details(
            "compliance.report_generated",
            {"random_extra_field": "x"},
        )


def test_report_failed_curated_reasons(sandbox_home, chain_path):
    cr_audit.report_failed(
        report_type="ai_act_art_50",
        tenant_id="_default",
        reason="chain-missing",
        period_start_ts=0,
        period_end_ts=0,
    )
    events = _read_chain(chain_path)
    assert events[-1]["event_type"] == "compliance.report_failed"
    assert events[-1]["details"]["reason"] == "chain-missing"


def test_report_failed_rejects_uncurated_reason(sandbox_home):
    with pytest.raises(cr_audit.ComplianceAuditFieldNotAllowed):
        cr_audit.report_failed(
            report_type="ai_act_art_50",
            tenant_id="_default",
            reason="just-because",
            period_start_ts=0,
            period_end_ts=0,
        )
