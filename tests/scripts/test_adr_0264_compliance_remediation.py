"""scripts/adr_0264_compliance_remediation.py must not touch ADRs unless --fix.

Regression (2026-09-27 review): main() passed ``dry_run=args.dry_run``, so a
bare run rewrote/unlinked files in the canonical Corvin-ADR repo, and the
archive rule matched legitimate legacy ADR names such as 0030-plugin-system.md.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "adr_0264_compliance_remediation.py"


@pytest.fixture
def mod(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("adr_0264_rem", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    monkeypatch.setattr(m, "ADR_REPO", decisions)
    monkeypatch.setattr(m, "ARCHIVE_DIR", tmp_path / "archive")
    (decisions / "0030-plugin-system.md").write_text("# ADR-0030 Plugin system\n")
    (decisions / "ADR-0001-x.md").write_text("---\nid: ADR-0001\n---\nbody\n")
    return m, decisions


def _run(m, monkeypatch, *argv):
    monkeypatch.setattr(sys, "argv", ["prog", *argv])
    m.main()


def test_bare_invocation_changes_nothing(mod, monkeypatch):
    m, decisions = mod
    before = {p.name: p.read_text() for p in decisions.iterdir()}
    _run(m, monkeypatch)
    assert {p.name: p.read_text() for p in decisions.iterdir()} == before


def test_fix_keeps_legacy_named_adrs_in_decisions(mod, monkeypatch):
    m, decisions = mod
    _run(m, monkeypatch, "--fix")
    assert (decisions / "0030-plugin-system.md").exists()
    assert "status:" in (decisions / "ADR-0001-x.md").read_text()
