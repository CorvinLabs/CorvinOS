"""CLI E2E — round-trip every report-type through `python -m`.

Operators today reach the reports through this CLI; the route
wiring into corvin-admin / corvin-console is a follow-up
session. Verifying the CLI guarantees the feature is usable
even before UI integration ships.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest

from corvin_compliance_reports import cli


def test_list_command(capsys):
    code = cli.main(["list"])
    assert code == 0
    out = capsys.readouterr().out
    assert "ai-act-50" in out
    assert "gdpr-30" in out
    assert "audit-attestation" in out


def test_parse_duration_valid():
    assert cli._parse_duration("30d") == 30 * 86400
    assert cli._parse_duration("7d") == 7 * 86400
    assert cli._parse_duration("12h") == 12 * 3600
    assert cli._parse_duration("60s") == 60
    assert cli._parse_duration("2w") == 14 * 86400


def test_parse_duration_invalid_raises():
    with pytest.raises(Exception):
        cli._parse_duration("garbage")


@pytest.mark.parametrize("report_type", ["ai-act-50", "gdpr-30", "audit-attestation"])
def test_generate_round_trip(
    sandbox_home, seed_chain, tmp_path, report_type, capsys,
):
    seed_chain("disclosure.shown", uid="u1")
    seed_chain("consent.granted", uid="u1", mode="durable")
    seed_chain("gateway.run_created", engine="claude_code",
               compliance_zone="eu-central")
    out = tmp_path / f"{report_type}.pdf"
    code = cli.main([
        "generate", report_type,
        "--tenant", "_default",
        "--since", "30d",
        "--output", str(out),
    ])
    assert code == 0
    assert out.exists()
    assert out.read_bytes().startswith(b"%PDF")
    text = capsys.readouterr().out
    assert "wrote" in text
    assert "chain" in text


def test_generate_unknown_report_type(capsys):
    code = cli.main([
        "generate", "totally-fake-report",
        "--tenant", "_default",
    ])
    assert code == 2
    err = capsys.readouterr().err
    assert "unknown report type" in err


def test_generate_quiet_mode_prints_only_path(
    sandbox_home, seed_chain, tmp_path, capsys,
):
    seed_chain("disclosure.shown", uid="u")
    out = tmp_path / "quiet.pdf"
    code = cli.main([
        "generate", "audit-attestation",
        "--tenant", "_default",
        "--output", str(out),
        "--quiet",
    ])
    assert code == 0
    text = capsys.readouterr().out.strip()
    assert text == str(out)


def test_generate_emits_audit_event(
    sandbox_home, seed_chain, chain_path, tmp_path,
):
    """compliance.report_generated must land in the chain after CLI run."""
    seed_chain("disclosure.shown", uid="u")
    out = tmp_path / "audit.pdf"
    cli.main([
        "generate", "ai-act-50",
        "--tenant", "_default",
        "--output", str(out),
        "--quiet",
    ])
    events = [
        json.loads(line)
        for line in chain_path.read_text().splitlines()
        if line.strip()
    ]
    types = [e["event_type"] for e in events]
    assert "compliance.report_generated" in types
    # The last event is the audit emit
    audit_ev = next(e for e in events if e["event_type"] == "compliance.report_generated")
    assert audit_ev["details"]["report_type"] == "ai_act_art_50"
    # MUST NOT carry the report body or any raw event data
    details_str = json.dumps(audit_ev["details"])
    assert "report_body" not in details_str
    assert "pdf_bytes" not in details_str
