"""ADR-0215 F5 regression: EU AI Act / GDPR compliance checks (L10/L34/L35)
must actually run, not structurally always report "fail".

Before this fix, all three checks below did a dotted
``from operator.bridges.shared... import ...`` — which can NEVER resolve
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
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

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
    src = (Path(__file__).resolve().parent / "eu_ai_act_audit.py").read_text()
    code_lines = [
        line for line in src.splitlines() if not line.strip().startswith("#")
    ]
    offender = next(
        (line for line in code_lines
         if re.search(r"from operator\.\w|import operator\.\w", line)),
        None,
    )
    assert offender is None, f"found a dotted `operator.` import: {offender!r}"
