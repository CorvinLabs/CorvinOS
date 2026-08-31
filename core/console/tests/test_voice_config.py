"""Tests for Phase 1a: Voice Config Manager.

Tests the VoiceConfigManager's path resolution and migration logic.

Key strategy: Tests use VOICE_CONFIG_DIR to override the legacy path,
avoiding direct manipulation of ~/.config/corvin-voice/.
"""
import json
import os
from pathlib import Path

import pytest

from corvin_console.voice_config import (
    VoiceConfigManager,
    MigrationResult,
    get_voice_config_manager,
)


class TestVoiceConfigManager:
    """Unit tests for VoiceConfigManager."""

    def test_voice_home_returns_tenant_path(self, monkeypatch, tmp_path):
        """voice_home() should return tenant-scoped directory."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        manager = VoiceConfigManager(tenant_id="_default")

        voice_home = manager.voice_home()
        assert "tenants" in str(voice_home)
        assert "_default" in str(voice_home)
        assert "voice" in str(voice_home)

    def test_legacy_voice_config_dir_with_voice_config_dir_override(
        self, monkeypatch, tmp_path
    ):
        """legacy_voice_config_dir() should use VOICE_CONFIG_DIR override."""
        override_dir = tmp_path / "override-voice"
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(override_dir))

        manager = VoiceConfigManager()
        legacy = manager.legacy_voice_config_dir()

        assert legacy == override_dir

    def test_legacy_voice_config_dir_with_xdg(self, monkeypatch, tmp_path):
        """legacy_voice_config_dir() should respect XDG_CONFIG_HOME."""
        xdg_dir = tmp_path / "xdg-config"
        monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))

        manager = VoiceConfigManager()
        legacy = manager.legacy_voice_config_dir()

        assert legacy == xdg_dir / "corvin-voice"

    def test_has_legacy_config_true_when_exists(self, monkeypatch, tmp_path):
        """has_legacy_config() should return True when legacy dir exists."""
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager()
        assert manager.has_legacy_config() is True

    def test_has_legacy_config_false_when_missing(self, monkeypatch, tmp_path):
        """has_legacy_config() should return False when legacy dir missing."""
        missing_dir = tmp_path / "missing-voice"
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(missing_dir))

        manager = VoiceConfigManager()
        assert manager.has_legacy_config() is False

    def test_needs_migration_true_when_legacy_only(self, monkeypatch, tmp_path):
        """needs_migration() should return True when only legacy exists."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.needs_migration() is True

    def test_needs_migration_false_when_both_exist(self, monkeypatch, tmp_path):
        """needs_migration() should return False when both exist."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        new_dir = corvin_home / "tenants" / "_default" / "voice"

        legacy_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.needs_migration() is False

    def test_needs_migration_false_when_neither_exist(self, monkeypatch, tmp_path):
        """needs_migration() should return False when neither exists."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.needs_migration() is False

    def test_profile_path_prefers_new(self, monkeypatch, tmp_path):
        """profile_path() should prefer new tenant location."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        new_dir = corvin_home / "tenants" / "_default" / "voice"

        legacy_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)
        (legacy_dir / "profile.json").write_text("{}")
        (new_dir / "profile.json").write_text('{"test": "new"}')

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        path = manager.profile_path()

        assert "tenants/_default/voice" in str(path)
        assert json.loads(path.read_text())["test"] == "new"

    def test_profile_path_falls_back_to_legacy(self, monkeypatch, tmp_path):
        """profile_path() should fall back to legacy location."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "profile.json").write_text('{"test": "legacy"}')

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        path = manager.profile_path()

        assert "legacy-voice" in str(path)
        assert json.loads(path.read_text())["test"] == "legacy"

    def test_profile_path_default_to_new_when_none_exist(self, monkeypatch, tmp_path):
        """profile_path() should default to new location when nothing exists."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        path = manager.profile_path()

        assert "tenants/_default/voice" in str(path)
        assert path.name == "profile.json"

    def test_vault_dir_path_resolution(self, monkeypatch, tmp_path):
        """vault_dir() should resolve paths correctly."""
        corvin_home = tmp_path / "corvin"
        new_dir = corvin_home / "tenants" / "_default" / "voice"
        new_dir.mkdir(parents=True)
        (new_dir / "vault").mkdir()

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))

        manager = VoiceConfigManager(tenant_id="_default")
        vault = manager.vault_dir()

        assert "tenants/_default/voice/vault" in str(vault)

    def test_memory_dir_path_resolution(self, monkeypatch, tmp_path):
        """memory_dir() should resolve paths correctly."""
        corvin_home = tmp_path / "corvin"
        new_dir = corvin_home / "tenants" / "_default" / "voice"
        new_dir.mkdir(parents=True)
        (new_dir / "memory").mkdir()

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))

        manager = VoiceConfigManager(tenant_id="_default")
        memory = manager.memory_dir()

        assert "tenants/_default/voice/memory" in str(memory)

    def test_piper_models_dir_path_resolution(self, monkeypatch, tmp_path):
        """piper_models_dir() should resolve paths correctly."""
        corvin_home = tmp_path / "corvin"
        new_dir = corvin_home / "tenants" / "_default" / "voice"
        new_dir.mkdir(parents=True)
        (new_dir / "piper-models").mkdir()

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))

        manager = VoiceConfigManager(tenant_id="_default")
        models = manager.piper_models_dir()

        assert "tenants/_default/voice/piper-models" in str(models)

    def test_migrate_from_legacy_copies_files(self, monkeypatch, tmp_path):
        """migrate_from_legacy() should copy files and directories."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)

        # Create test files
        (legacy_dir / "profile.json").write_text('{"name": "Test"}')
        (legacy_dir / "vault").mkdir()
        (legacy_dir / "vault" / "secret.json.gpg").write_bytes(b"encrypted")
        (legacy_dir / "memory").mkdir()
        (legacy_dir / "memory" / "notes.json").write_text('{"note": "data"}')

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        result = manager.migrate_from_legacy()

        assert result.success is True
        assert result.migrated_items >= 3
        assert len(result.errors) == 0

        # Check that files were copied
        new_dir = manager.voice_home()
        assert (new_dir / "profile.json").exists()
        assert (new_dir / "vault" / "secret.json.gpg").exists()
        assert (new_dir / "memory" / "notes.json").exists()

        # Check migration marker
        assert (new_dir / ".migrated").exists()

    def test_migrate_from_legacy_idempotent(self, monkeypatch, tmp_path):
        """migrate_from_legacy() should be idempotent."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "profile.json").write_text("{}")

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")

        # First migration
        result1 = manager.migrate_from_legacy()
        assert result1.success is True
        assert result1.migrated_items > 0

        # Second migration (should do nothing)
        result2 = manager.migrate_from_legacy()
        assert result2.success is True
        assert result2.migrated_items == 0

    def test_migrate_from_legacy_no_source(self, monkeypatch, tmp_path):
        """migrate_from_legacy() should succeed when no legacy config exists."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        result = manager.migrate_from_legacy()

        assert result.success is True
        assert result.migrated_items == 0

    def test_migrate_from_legacy_skips_existing_destination(
        self, monkeypatch, tmp_path
    ):
        """migrate_from_legacy() should skip files that already exist in destination."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"
        new_dir = corvin_home / "tenants" / "_default" / "voice"

        legacy_dir.mkdir(parents=True)
        new_dir.mkdir(parents=True)

        # Create conflicting files
        (legacy_dir / "profile.json").write_text('{"source": "legacy"}')
        (new_dir / "profile.json").write_text('{"source": "new"}')

        # Create non-conflicting file
        (legacy_dir / "other.json").write_text("{}")

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        result = manager.migrate_from_legacy()

        assert result.success is True
        # Only "other.json" should be migrated
        assert result.migrated_items == 1

        # Verify the existing file was not overwritten
        existing_data = json.loads((new_dir / "profile.json").read_text())
        assert existing_data["source"] == "new"

    def test_get_voice_config_manager_caches_instances(self, monkeypatch, tmp_path):
        """get_voice_config_manager() should cache instances per tenant."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        mgr1 = get_voice_config_manager(tenant_id="tenant1")
        mgr2 = get_voice_config_manager(tenant_id="tenant1")

        # Should be the same instance
        assert mgr1 is mgr2

    def test_get_voice_config_manager_different_tenants(self, monkeypatch, tmp_path):
        """get_voice_config_manager() should return different instances for different tenants."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        mgr1 = get_voice_config_manager(tenant_id="tenant1")
        mgr2 = get_voice_config_manager(tenant_id="tenant2")

        # Should be different instances
        assert mgr1 is not mgr2
        assert mgr1.tenant_id != mgr2.tenant_id

    def test_migration_result_dataclass(self):
        """MigrationResult should construct correctly."""
        result = MigrationResult(
            success=True,
            migrated_items=5,
            errors=[],
            warnings=["warning1"],
        )

        assert result.success is True
        assert result.migrated_items == 5
        assert result.errors == []
        assert result.warnings == ["warning1"]


class TestVoiceConfigManagerIntegration:
    """Integration tests for VoiceConfigManager."""

    def test_end_to_end_migration(self, monkeypatch, tmp_path):
        """Test a complete migration flow."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = tmp_path / "legacy-voice"

        # Set up legacy structure with realistic content
        legacy_dir.mkdir(parents=True)
        (legacy_dir / "profile.json").write_text(
            json.dumps({"name": "Test User", "display_language": "de"})
        )
        (legacy_dir / "vault").mkdir()
        (legacy_dir / "vault" / "INDEX.json").write_text("{}")
        (legacy_dir / "memory").mkdir()
        (legacy_dir / "memory" / "session.json").write_text("{}")

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        # Create manager and perform migration
        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.needs_migration() is True

        result = manager.migrate_from_legacy()
        assert result.success is True
        assert result.migrated_items > 0

        # Verify paths now resolve to new location
        assert "tenants/_default/voice" in str(manager.profile_path())
        assert "tenants/_default/voice" in str(manager.vault_dir())
        assert "tenants/_default/voice" in str(manager.memory_dir())

        # Verify content
        profile = json.loads(manager.profile_path().read_text())
        assert profile["name"] == "Test User"
