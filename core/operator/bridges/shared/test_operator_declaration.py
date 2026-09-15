"""Tests for Operator Declaration Gate (ADR-0057 Component 3)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture()
def tmp_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


def _write_tenant_yaml(home: Path, content: str, tenant_id: str = "_default") -> Path:
    p = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


@pytest.fixture()
def decl():
    import operator_declaration
    return operator_declaration


class TestNonProductionProfiles:
    def test_no_yaml_is_ok(self, decl, tmp_home):
        result = decl.check_operator_declaration("_default")
        assert result.ok

    def test_dev_profile_no_declaration_required(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, "spec:\n  deployment_profile: dev\n")
        result = decl.check_operator_declaration("_default")
        assert result.ok

    def test_empty_profile_is_ok(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, "spec: {}\n")
        result = decl.check_operator_declaration("_default")
        assert result.ok


class TestEuProductionProfile:
    def test_missing_declaration_is_critical(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, "spec:\n  deployment_profile: eu_production\n")
        result = decl.check_operator_declaration("_default")
        assert not result.ok
        assert "operator_declaration missing" in result.error

    def test_dpia_not_completed(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, """
spec:
  deployment_profile: eu_production
  operator_declaration:
    version: "1.0"
    dpia_completed: false
    dpia_date: "2026-06-01"
""")
        result = decl.check_operator_declaration("_default")
        assert not result.ok
        assert "dpia_completed is false" in result.error

    def test_missing_dpia_date(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, """
spec:
  deployment_profile: eu_production
  operator_declaration:
    version: "1.0"
    dpia_completed: true
""")
        result = decl.check_operator_declaration("_default")
        assert not result.ok
        assert "dpia_date missing" in result.error

    def test_complete_declaration_passes(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, """
spec:
  deployment_profile: eu_production
  operator_declaration:
    version: "1.0"
    declared_by: "J. Müller, DPO"
    dpia_completed: true
    dpia_date: "2026-06-15"
    permitted_use: "internal-coding-assistant"
""")
        result = decl.check_operator_declaration("_default")
        assert result.ok
        assert result.dpia_date == "2026-06-15"
        assert result.dpia_completed is True

    def test_ollama_profile_also_requires_declaration(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home,
                           "spec:\n  deployment_profile: eu_production_ollama\n")
        result = decl.check_operator_declaration("_default")
        assert not result.ok

    def test_declared_by_not_in_audit_dict(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, """
spec:
  deployment_profile: eu_production
  operator_declaration:
    version: "1.0"
    declared_by: "SENSITIVE-PERSON-NAME"
    dpia_completed: true
    dpia_date: "2026-06-15"
""")
        result = decl.check_operator_declaration("_default")
        assert result.ok
        audit = result.audit_dict()
        assert "declared_by" not in audit
        assert "SENSITIVE-PERSON-NAME" not in str(audit)

    def test_permitted_use_not_in_audit_dict(self, decl, tmp_home):
        _write_tenant_yaml(tmp_home, """
spec:
  deployment_profile: eu_production
  operator_declaration:
    version: "1.0"
    dpia_completed: true
    dpia_date: "2026-06-15"
    permitted_use: "SECRET-USE-CASE"
""")
        result = decl.check_operator_declaration("_default")
        assert result.ok
        audit = result.audit_dict()
        assert "permitted_use" not in audit
        assert "SECRET-USE-CASE" not in str(audit)
