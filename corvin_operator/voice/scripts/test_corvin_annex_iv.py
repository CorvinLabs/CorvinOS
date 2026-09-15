"""Smoke tests for corvin_annex_iv.py (ADR-0057 Component 4 / ADR-0060).

Verifies that the Annex IV generator, ISO 42001 SoA, NIST profile, and
cross-reference map produce non-empty, structurally correct output.
No LLM is called; all operations are purely data-assembly.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Point to the Corvin repo root for CORVIN_REPO_ROOT resolution
_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent.parent.parent
os.environ.setdefault("CORVIN_REPO_ROOT", str(_REPO))

sys.path.insert(0, str(_HERE))
from corvin_annex_iv import (
    generate_annex_iv,
    generate_cross_reference_map,
    generate_nist_profile,
    generate_soa_iso42001,
)


class TestGenerateSoaIso42001:
    def test_returns_string(self):
        out = generate_soa_iso42001()
        assert isinstance(out, str) and len(out) > 100

    def test_contains_iso_header(self):
        out = generate_soa_iso42001()
        assert "ISO/IEC 42001" in out or "42001" in out

    def test_contains_implemented_layers(self):
        out = generate_soa_iso42001()
        assert "L16" in out or "L10" in out or "audit" in out.lower()


class TestGenerateNistProfile:
    def test_returns_string(self):
        out = generate_nist_profile()
        assert isinstance(out, str) and len(out) > 100

    def test_contains_nist_functions(self):
        out = generate_nist_profile()
        assert "GOVERN" in out or "MANAGE" in out or "MAP" in out

    def test_contains_corvin_layers(self):
        out = generate_nist_profile()
        assert "L16" in out or "L10" in out or "audit" in out.lower()


class TestGenerateCrossReferenceMap:
    def test_returns_string(self):
        out = generate_cross_reference_map()
        assert isinstance(out, str) and len(out) > 50

    def test_all_frameworks_requested(self):
        out = generate_cross_reference_map(frameworks=["eu-ai-act", "gdpr", "iso-42001", "nist-ai-rmf"])
        assert isinstance(out, str) and len(out) > 50

    def test_single_framework(self):
        out = generate_cross_reference_map(frameworks=["eu-ai-act"])
        assert isinstance(out, str)


class TestGenerateAnnexIv:
    def test_returns_string(self):
        out = generate_annex_iv()
        assert isinstance(out, str) and len(out) > 200

    def test_contains_required_sections(self):
        out = generate_annex_iv()
        assert "Annex IV" in out or "Technical Documentation" in out

    def test_contains_risk_classification(self):
        out = generate_annex_iv()
        assert "Limited" in out or "Risk" in out

    def test_no_anthropic_import(self):
        import ast
        src = (_HERE / "corvin_annex_iv.py").read_text()
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, (ast.Import, ast.ImportFrom)):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        assert "anthropic" not in alias.name, "Must not import anthropic"
                else:
                    assert node.module is None or "anthropic" not in node.module


if __name__ == "__main__":
    # Simple runner without pytest dependency
    suites = [
        TestGenerateSoaIso42001(),
        TestGenerateNistProfile(),
        TestGenerateCrossReferenceMap(),
        TestGenerateAnnexIv(),
    ]
    passed = failed = 0
    for suite in suites:
        for name in [m for m in dir(suite) if m.startswith("test_")]:
            try:
                getattr(suite, name)()
                print(f"  OK  {type(suite).__name__}.{name}")
                passed += 1
            except Exception as exc:
                print(f"  FAIL {type(suite).__name__}.{name}: {exc}")
                failed += 1
    print(f"\ncorvin_annex_iv: {passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
