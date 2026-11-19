"""ADR-0215 F5 regression: EU AI Act / GDPR compliance checks (L10/L34/L35)
must actually run, not structurally always report "fail".

Before this fix, all three checks below did a dotted
``from corvin_operator.bridges.shared... import ...`` — which can NEVER resolve
(stdlib ``operator`` always wins) — so every call raised inside the ``try``
block and fell through to a generic ``except Exception`` that reported
``status="fail"``. The checks LOOKED fail-closed (correct posture for a
compliance gate) but were actually never checking anything; a genuinely
broken path_gate.py, a genuinely misconfigured data-classification matrix,
or a genuinely open egress policy would have reported the exact same
"fail" as a healthy system — false-negative-proof only by accident.

This file did not exist before ADR-0215 — the compliance auditor had zero
test coverage.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "corvin_operator" / "bridges" / "shared"))

from compliance.eu_ai_act_audit import ComplianceAuditor  # noqa: E402


def test_l10_path_gate_actually_runs_and_passes():
    result = ComplianceAuditor().audit_l10_path_gate("claude_code")
    assert result.status == "pass", result.error


def test_l34_data_classification_actually_runs_and_passes():
    result = ComplianceAuditor().audit_l34_data_classification("claude_code")
    assert result.status == "pass", result.error


def test_l35_egress_actually_runs_and_passes():
    result = ComplianceAuditor().audit_l35_egress("claude_code")
    assert result.status == "pass", result.error


def test_l34_reports_real_failure_for_unknown_engine():
    # A genuinely unregistered engine must still fail — proves the check
    # is discriminating, not just returning "pass" unconditionally now.
    result = ComplianceAuditor().audit_l34_data_classification("totally_made_up_engine_xyz")
    assert result.status == "fail"
    assert "not in DEFAULT_ENGINE_COMPLIANCE" in (result.error or "")


def test_l10_path_gate_import_uses_bare_module_not_dotted():
    # Regression guard for the specific bug class (ADR-0215 F5): a dotted
    # `operator.` import anywhere in this module must never reappear.
    # Only real code lines count — the fix's own explanatory comments
    # deliberately quote the old broken form as documentation.
    import re
    src = (Path(__file__).resolve().parent / "eu_ai_act_audit.py").read_text(encoding="utf-8")
    code_lines = [
        line for line in src.splitlines() if not line.strip().startswith("#")
    ]
    offender = next(
        (line for line in code_lines
         if re.search(r"from operator\.\w|import operator\.\w", line)),
        None,
    )
    assert offender is None, f"found a dotted `operator.` import: {offender!r}"


def test_l16_reads_the_resolver_chain_not_a_literal_home_path(tmp_path, monkeypatch):
    """The L16 check judged "~/.corvin/tenants/_default/global/audit.jsonl" — a legacy file
    under a hard-wired home. It must read tenant_audit_chain() of THIS install."""
    import json
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    import paths  # type: ignore
    chain = paths.tenant_audit_chain("_default")
    assert str(chain).startswith(str(tmp_path)), chain
    # No chain yet: the failure names the resolver's path, not the legacy literal.
    missing = ComplianceAuditor().audit_l16_audit_chain("claude_code")
    assert missing.status == "fail" and str(chain) in (missing.error or "")
    chain.parent.mkdir(parents=True)
    chain.write_text(
        json.dumps({"event_type": "a", "prev_hash": "", "hash": "h1"}) + "\n"
        + json.dumps({"event_type": "b", "prev_hash": "h1", "hash": "h2"}) + "\n", encoding="utf-8")
    ok = ComplianceAuditor().audit_l16_audit_chain("claude_code")
    assert ok.status == "pass", ok.error
    # Discriminating: a record with a forbidden field in THAT file fails the check.
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_type": "c", "prev_hash": "h2", "hash": "h3", "prompt": "x"}) + "\n")
    assert ComplianceAuditor().audit_l16_audit_chain("claude_code").status == "fail"
    src = (Path(__file__).resolve().parent / "eu_ai_act_audit.py").read_text(encoding="utf-8")
    code = [l for l in src.splitlines() if not l.strip().startswith("#")]
    assert not any("~/.corvin" in l for l in code)
