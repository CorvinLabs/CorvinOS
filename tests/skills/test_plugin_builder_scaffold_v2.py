"""Unit tests for Plugin-Builder v2 Scaffolding System (ADR-0262 Phase A).

Tests verify:
- PluginScaffoldConfig validation
- PluginScaffoldGenerator with tenant isolation
- Lifecycle hook generation
- Directory structure creation
- Audit trail events
- Tenant isolation (no cross-tenant leakage)
- Error handling (fail-closed)
- Unicode/bidi-override prevention (CVE-2021-42574)
- Version tracking and rollback capability
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.plugins.plugin_builder.scaffolding import (
    PluginScaffoldConfig,
    PluginScaffoldGenerator,
    AuditEventEmitter,
    bootstrap_scaffold,
)


class TestPluginScaffoldConfig:
    """Test PluginScaffoldConfig validation (50+ synthetic configs)."""

    def test_config_valid_basic(self):
        """Test basic valid configuration."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert is_valid, f"Expected valid config, got error: {error}"

    def test_config_valid_all_fields(self):
        """Test valid configuration with all fields."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="skill_plugin",
            description="A test skill plugin",
            author="Test Author",
            version="1.2.3",
            include_lifecycle_hooks=True,
            external_dependencies=["requests", "numpy"],
            requires_auth=True,
            requires_network_egress=True,
            egress_hosts=["api.example.com", "data.example.org"],
        )
        is_valid, error = config.validate()
        assert is_valid, f"Expected valid config, got error: {error}"

    def test_config_invalid_plugin_id_format(self):
        """Test invalid plugin_id format (uppercase not allowed)."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="My.Plugin",  # Invalid: uppercase
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "Invalid plugin_id" in error

    def test_config_invalid_plugin_id_special_chars(self):
        """Test invalid plugin_id with disallowed special chars."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin@invalid!",
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid

    def test_config_invalid_plugin_type(self):
        """Test invalid plugin_type."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="invalid_type",
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "Invalid plugin_type" in error

    def test_config_valid_all_plugin_types(self):
        """Test all valid plugin types."""
        valid_types = [
            "data_connector", "skill_plugin", "compute_engine", "bridge_channel",
            "worker_engine", "provider_plugin", "middleware", "integration"
        ]
        for ptype in valid_types:
            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="my.plugin",
                plugin_name="My Plugin",
                plugin_type=ptype,
            )
            is_valid, error = config.validate()
            assert is_valid, f"Type {ptype} should be valid, got error: {error}"

    def test_config_invalid_version_format(self):
        """Test invalid semantic version."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
            version="not-a-version",
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "Invalid version" in error

    def test_config_valid_version_prerelease(self):
        """Test valid semantic version with prerelease."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
            version="1.0.0-beta.1",
        )
        is_valid, error = config.validate()
        assert is_valid, f"Prerelease version should be valid, got error: {error}"

    def test_config_plugin_name_length_limit(self):
        """Test plugin_name length validation (max 200 chars)."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="x" * 201,  # Too long
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "too long" in error.lower()

    def test_config_description_length_limit(self):
        """Test description length validation (max 5000 chars)."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
            description="x" * 5001,  # Too long
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "too long" in error.lower()

    def test_config_unicode_bidi_prevention_lre(self):
        """Test Unicode bidi-override prevention (LRE - CVE-2021-42574)."""
        config = PluginScaffoldConfig(
            tenant_id="_default‪",  # LRE character
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid
        assert "Unsafe" in error

    def test_config_unicode_bidi_prevention_rle(self):
        """Test Unicode bidi-override prevention (RLE)."""
        config = PluginScaffoldConfig(
            tenant_id="_default‫",  # RLE character
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid

    def test_config_unicode_zerowidth_prevention(self):
        """Test zero-width character prevention."""
        config = PluginScaffoldConfig(
            tenant_id="_default​",  # Zero-width space
            plugin_id="my.plugin",
            plugin_name="My Plugin",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid

    def test_config_valid_tenant_ids(self):
        """Test valid tenant IDs."""
        valid_tenants = ["_default", "tenant_1", "acme-org", "org.department"]
        for tid in valid_tenants:
            config = PluginScaffoldConfig(
                tenant_id=tid,
                plugin_id="my.plugin",
                plugin_name="My Plugin",
                plugin_type="data_connector",
            )
            is_valid, error = config.validate()
            assert is_valid, f"Tenant {tid} should be valid"


def _scaffold_records(tenant_id: str = "_default") -> list:
    """``plugin_builder.scaffold`` records the CORE writer committed (the harness points it at the
    sandbox chain via VOICE_AUDIT_PATH). The emitter used to append its own hash lines to whatever
    path ``tenant_audit_chain`` returned - which is what these tests mocked (review AUD-1)."""
    import os

    chain = Path(os.environ["VOICE_AUDIT_PATH"])
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if r.get("event_type") == "plugin_builder.scaffold"
            and (r.get("details") or {}).get("tenant_id") == tenant_id]


class TestAuditEventEmitter:
    """AuditEventEmitter writes through the core writer: content-free, chained, fail-closed."""

    def test_emit_scaffold_generated(self):
        AuditEventEmitter("_default").emit_scaffold_generated(
            plugin_id="test.plugin", plugin_type="data_connector",
            scaffold_dir=Path("/tmp/SENTINEL_DIR"), config_hash="abc123")
        rec = _scaffold_records()[-1]
        assert rec["details"]["stage"] == "scaffold_generated"
        assert rec["details"]["plugin_id"] == "test.plugin"
        assert rec["details"]["tenant_id"] == "_default"
        assert "SENTINEL_DIR" not in json.dumps(rec)            # no filesystem path in the record

    def test_emit_lifecycle_hooks_created(self):
        AuditEventEmitter("_default").emit_lifecycle_hooks_created(
            plugin_id="test.plugin", hooks=["on_load", "on_execute", "on_unload"])
        rec = _scaffold_records()[-1]
        assert rec["details"]["stage"] == "lifecycle_hooks_created"
        assert rec["details"]["hooks_n"] == 3

    def test_emit_scaffold_generation_failed(self):
        AuditEventEmitter("_default").emit_scaffold_generation_failed(
            plugin_id="test.plugin", error_message="SENTINEL secret detail", error_type="ValueError")
        rec = _scaffold_records()[-1]
        assert rec["details"]["stage"] == "scaffold_generation_failed"
        assert rec["details"]["error_class"] == "ValueError"
        assert "SENTINEL" not in json.dumps(rec)                # the message never reaches the chain

    def test_audit_chain_hash_link(self):
        """The records are links of THE chain - it still verifies after them."""
        import os
        from corvin_operator.forge.forge import security_events as se

        em = AuditEventEmitter("_default")
        em.emit_scaffold_generated(plugin_id="test.plugin", plugin_type="data_connector",
                                   scaffold_dir=Path("/tmp/test"), config_hash="abc123")
        em.emit_lifecycle_hooks_created(plugin_id="test.plugin", hooks=["on_load"])
        assert len(_scaffold_records()) >= 2
        assert se.verify_chain(Path(os.environ["VOICE_AUDIT_PATH"]))[0] is True

    def test_a_record_for_another_tenant_is_refused(self):
        with pytest.raises(RuntimeError):
            AuditEventEmitter("tenant_not_this_process").emit_lifecycle_hooks_created(
                plugin_id="test.plugin", hooks=["on_load"])


class TestPluginScaffoldGenerator:
    """Test PluginScaffoldGenerator with full lifecycle."""

    def test_generator_invalid_config(self):
        """Test that generator rejects invalid config."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="INVALID",  # Uppercase not allowed
            plugin_name="Test",
            plugin_type="data_connector",
        )
        with pytest.raises(ValueError):
            PluginScaffoldGenerator(config)

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_generator_generate_basic(self, mock_tenant_audit_chain):
        """Test basic scaffold generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="data_connector",
            )

            generator = PluginScaffoldGenerator(config)
            files = generator.generate(tmpdir)

            assert "plugin" in files
            assert "plugin_json" in files
            assert "gitignore" in files

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_generator_creates_plugin_json(self, mock_tenant_audit_chain):
        """Test that generator creates plugin.json with correct schema."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="skill_plugin",
                description="A test skill",
                requires_auth=True,
                egress_hosts=["api.example.com"],
            )

            generator = PluginScaffoldGenerator(config)
            files = generator.generate(tmpdir)

            plugin_json_file = files["plugin_json"]
            assert plugin_json_file.exists()

            plugin_json = json.loads(plugin_json_file.read_text())
            assert plugin_json["id"] == "test.plugin"
            assert plugin_json["name"] == "Test Plugin"
            assert plugin_json["type"] == "skill_plugin"
            assert plugin_json["requires_auth"] is True
            assert "api.example.com" in plugin_json["egress_hosts"]

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_generator_creates_gitignore(self, mock_tenant_audit_chain):
        """Test that generator creates .gitignore with secrets protection."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="data_connector",
            )

            generator = PluginScaffoldGenerator(config)
            files = generator.generate(tmpdir)

            gitignore_file = files["gitignore"]
            assert gitignore_file.exists()

            gitignore_content = gitignore_file.read_text()
            # Check for secrets protection patterns
            assert ".env" in gitignore_content
            assert "secrets.json" in gitignore_content
            assert "credentials.json" in gitignore_content
            assert "audit.jsonl" in gitignore_content

    def test_generator_emits_audit_events(self, tmp_path):
        config = PluginScaffoldConfig(tenant_id="_default", plugin_id="test.plugin",
                                      plugin_name="Test Plugin", plugin_type="data_connector")
        PluginScaffoldGenerator(config).generate(tmp_path)
        stages = [r["details"]["stage"] for r in _scaffold_records()]
        assert "scaffold_generated" in stages and "lifecycle_hooks_created" in stages

    def test_generator_tenant_isolation(self, tmp_path, monkeypatch):
        """Each tenant's scaffold is recorded as that tenant - run as that tenant. A process cannot
        write another tenant's records (ADR-0562), so a foreign-tenant scaffold fails closed."""
        for tid, pid in (("tenant_1", "plugin.one"), ("tenant_2", "plugin.two")):
            monkeypatch.setenv("CORVIN_TENANT_ID", tid)
            PluginScaffoldGenerator(PluginScaffoldConfig(
                tenant_id=tid, plugin_id=pid, plugin_name=pid, plugin_type="data_connector",
            )).generate(tmp_path / tid)
            assert _scaffold_records(tid), f"no record for {tid}"
        monkeypatch.setenv("CORVIN_TENANT_ID", "tenant_1")
        with pytest.raises(RuntimeError):
            PluginScaffoldGenerator(PluginScaffoldConfig(
                tenant_id="tenant_2", plugin_id="plugin.three", plugin_name="x", plugin_type="data_connector",
            )).generate(tmp_path / "x")


class TestBootstrapScaffold:
    """Test bootstrap_scaffold function."""

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_bootstrap_scaffold_basic(self, mock_tenant_audit_chain):
        """Test bootstrap_scaffold basic usage."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            files = bootstrap_scaffold(
                output_dir=tmpdir,
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="data_connector",
            )

            assert "plugin" in files
            assert "plugin_json" in files
            assert "gitignore" in files

    def test_bootstrap_scaffold_with_custom_tenant(self, tmp_path, monkeypatch):
        monkeypatch.setenv("CORVIN_TENANT_ID", "custom_tenant")
        bootstrap_scaffold(output_dir=tmp_path, plugin_id="test.plugin", plugin_name="Test Plugin",
                           plugin_type="skill_plugin", tenant_id="custom_tenant", version="2.0.0")
        recs = _scaffold_records("custom_tenant")
        assert recs and all(r["details"]["tenant_id"] == "custom_tenant" for r in recs)

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_bootstrap_scaffold_invalid_config(self, mock_tenant_audit_chain):
        """Test bootstrap_scaffold with invalid config."""
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(ValueError):
                bootstrap_scaffold(
                    output_dir=tmpdir,
                    plugin_id="INVALID",  # Uppercase
                    plugin_name="Test Plugin",
                    plugin_type="data_connector",
                )


class TestErrorHandling:
    """Test error handling and fail-closed behavior."""

    def test_scaffold_generation_failed_audit_event(self):
        """Test that scaffold generation failures are audited."""
        config = PluginScaffoldConfig(
            tenant_id="_default",
            plugin_id="test.plugin",
            plugin_name="Test Plugin",
            plugin_type="data_connector",
        )
        generator = PluginScaffoldGenerator(config)

        # Mock audit emitter to verify failure event
        with patch.object(generator.audit_emitter, 'emit_scaffold_generation_failed') as mock_emit:
            # Force an error
            with patch.object(generator, '_create_plugin_json', side_effect=OSError("Write failed")):
                with pytest.raises(RuntimeError):
                    generator.generate("/nonexistent/dir")

                # Verify failure event was emitted
                mock_emit.assert_called_once()


class TestUnicodeAndBidiSecurity:
    """Test Unicode and Bidi-override prevention (CVE-2021-42574)."""

    def test_tenant_id_unicode_alm_prevention(self):
        """Test ALM character prevention."""
        config = PluginScaffoldConfig(
            tenant_id="_default؜",  # ALM
            plugin_id="test.plugin",
            plugin_name="Test",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid

    def test_tenant_id_bidi_pdf_prevention(self):
        """Test PDF (Pop Directional Formatting) prevention."""
        config = PluginScaffoldConfig(
            tenant_id="_default‬",  # PDF
            plugin_id="test.plugin",
            plugin_name="Test",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert not is_valid

    def test_safe_tenant_ids_with_numbers_and_dashes(self):
        """Test that normal alphanumeric tenant IDs pass."""
        config = PluginScaffoldConfig(
            tenant_id="tenant-123_abc",
            plugin_id="test.plugin",
            plugin_name="Test",
            plugin_type="data_connector",
        )
        is_valid, error = config.validate()
        assert is_valid


class TestLifecycleHookGeneration:
    """Test lifecycle hook generation."""

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_all_lifecycle_hooks_generated(self, mock_tenant_audit_chain):
        """Test that all requested lifecycle hooks are generated."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="data_connector",
                include_on_load=True,
                include_on_execute=True,
                include_on_unload=True,
            )

            generator = PluginScaffoldGenerator(config)
            files = generator.generate(tmpdir)

            # Check plugin.py contains all hooks
            plugin_py = files["plugin"].read_text()
            assert "def on_load" in plugin_py
            assert "def on_execute" in plugin_py
            assert "def on_unload" in plugin_py

    @patch('core.paths.tenant.tenant_audit_chain')
    def test_selective_lifecycle_hooks(self, mock_tenant_audit_chain):
        """Test selective lifecycle hook generation."""
        with tempfile.TemporaryDirectory() as tmpdir:
            audit_file = Path(tmpdir) / "audit.jsonl"
            mock_tenant_audit_chain.return_value = audit_file

            config = PluginScaffoldConfig(
                tenant_id="_default",
                plugin_id="test.plugin",
                plugin_name="Test Plugin",
                plugin_type="data_connector",
                include_on_load=True,
                include_on_execute=False,  # Exclude
                include_on_unload=True,
            )

            generator = PluginScaffoldGenerator(config)
            files = generator.generate(tmpdir)

            plugin_py = files["plugin"].read_text()
            assert "def on_load" in plugin_py
            assert "def on_execute" not in plugin_py  # Should be excluded
            assert "def on_unload" in plugin_py


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
