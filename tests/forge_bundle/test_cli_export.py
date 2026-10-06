"""Forge Bundle CLI — export, as a real subprocess (ADR-2229 Phase 2 E2E proof).

Runs ``scripts/forge_bundle_cli.py`` exactly as an operator would: a separate
Python process, args on the command line, a ZIP written to a real path.
"""
from __future__ import annotations

import json
from pathlib import Path

from core.forge_bundle import validate_bundle


def test_cli_export_writes_a_valid_bundle(cli_runner, make_skill, tmp_path):
    make_skill("summarize", "1.0.0")
    out = tmp_path / "bundle.zip"
    proc = cli_runner([
        "export", "--id", "acme-automation", "--version", "2.0.0",
        "--skill", "summarize@1.0.0", "--output", str(out),
    ])
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "SUCCESS"
    assert payload["artifact_count"] == 1
    assert out.exists()
    report = validate_bundle(out.read_bytes())
    assert report.envelope.id == "acme-automation"


def test_cli_export_missing_skill_exits_1(cli_runner, tmp_path):
    out = tmp_path / "bundle.zip"
    proc = cli_runner([
        "export", "--id", "b", "--version", "1.0.0",
        "--skill", "nope@1.0.0", "--output", str(out),
    ])
    assert proc.returncode == 1
    assert not out.exists()
    payload = json.loads(proc.stdout)
    assert payload["status"] == "FAILED"


def test_cli_export_no_selections_is_usage_error(cli_runner, tmp_path):
    out = tmp_path / "bundle.zip"
    proc = cli_runner(["export", "--id", "b", "--version", "1.0.0", "--output", str(out)])
    assert proc.returncode == 2
    assert not out.exists()


def test_cli_export_bad_spec_format_is_usage_error(cli_runner, tmp_path):
    out = tmp_path / "bundle.zip"
    proc = cli_runner([
        "export", "--id", "b", "--version", "1.0.0",
        "--skill", "no-at-sign", "--output", str(out),
    ])
    assert proc.returncode == 2
    assert not out.exists()


def test_cli_export_multiple_kinds(cli_runner, make_skill, make_tool, make_layer, tmp_path):
    make_skill("summarize", "1.0.0")
    make_tool("csv.count")
    make_layer("acme.audit-l34", "1.0.0")
    out = tmp_path / "bundle.zip"
    proc = cli_runner([
        "export", "--id", "acme-automation", "--version", "2.0.0", "--output", str(out),
        "--skill", "summarize@1.0.0",
        "--tool", "csv.count@0.2.0",
        "--layer", "acme.audit-l34@1.0.0",
    ])
    assert proc.returncode == 0, proc.stderr
    report = validate_bundle(out.read_bytes())
    assert len(report.envelope.artifacts) == 3
