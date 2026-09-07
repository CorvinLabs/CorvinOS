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
    MIGRATABLE_DIRS,
    MIGRATABLE_FILES,
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

        # Create non-conflicting file (must be on the migration allow-list --
        # "other.json" used to be used here and is now correctly NOT migrated)
        (legacy_dir / "config.json").write_text("{}")

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        result = manager.migrate_from_legacy()

        assert result.success is True
        # Only "config.json" should be migrated
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


class TestMigrationAllowList:
    """2026-09-07 adversarial review: the migration used to ``copytree`` EVERY
    subdirectory of the legacy dir. On the maintainer's host that is 1.4 GB /
    275,414 files -- an audit chain, an anchor key, 268k dead mac markers, a
    147 MB virtualenv, pid files and logs -- copied into every fresh voice home.
    """

    @staticmethod
    def _legacy_with_runtime_junk(tmp_path):
        legacy_dir = tmp_path / "legacy-voice"
        legacy_dir.mkdir(parents=True)

        # Real configuration (must migrate)
        (legacy_dir / "profile.json").write_text('{"name": "Test User"}')
        (legacy_dir / "config.json").write_text('{"tts": "piper"}')
        (legacy_dir / "service.env").write_text("FOO=bar\n")
        (legacy_dir / "vault").mkdir()
        (legacy_dir / "vault" / "INDEX.json").write_text("{}")
        (legacy_dir / "piper-models").mkdir()
        (legacy_dir / "piper-models" / "de.onnx").write_bytes(b"model")

        # Runtime / audit / build state (must NOT migrate)
        (legacy_dir / "audit.jsonl").write_text('{"hash": "deadbeef"}\n')
        (legacy_dir / "audit_anchor.key").write_bytes(b"\x00" * 32)
        (legacy_dir / "audit_mac_active").write_text('{"since": 1.0}')
        (legacy_dir / "voice.log").write_text("noise\n")
        (legacy_dir / "current.pgid").write_text("1234")
        (legacy_dir / "tts.lock").write_text("")
        (legacy_dir / "maintainer.key").write_text("secret")
        markers = legacy_dir / "mac_active_chains"
        markers.mkdir()
        for i in range(50):
            (markers / f"{i:032x}").write_text('{"since": 1.0}')
        (legacy_dir / "chain_tails").mkdir()
        (legacy_dir / "chain_tails" / "g-abc").write_text("tail")
        (legacy_dir / "forge").mkdir()
        (legacy_dir / "forge" / "audit.jsonl").write_text('{"hash": "x"}\n')
        (legacy_dir / "google").mkdir()
        (legacy_dir / "google" / "venv").mkdir()
        (legacy_dir / "google" / "venv" / "pyvenv.cfg").write_text("home=/usr")
        (legacy_dir / "whatsapp").mkdir()
        (legacy_dir / "whatsapp" / "daemon.pid").write_text("99")
        return legacy_dir

    def test_real_operator_upgrade_still_migrates_configuration(
        self, monkeypatch, tmp_path
    ):
        """A genuine upgrade must still carry configuration, vault and models."""
        corvin_home = tmp_path / "corvin"
        legacy_dir = self._legacy_with_runtime_junk(tmp_path)

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.needs_migration() is True
        result = manager.migrate_from_legacy()

        assert result.success is True, result.errors
        new_dir = manager.voice_home()
        assert json.loads((new_dir / "profile.json").read_text())["name"] == "Test User"
        assert (new_dir / "config.json").exists()
        assert (new_dir / "service.env").exists()
        assert (new_dir / "vault" / "INDEX.json").exists()
        assert (new_dir / "piper-models" / "de.onnx").exists()
        # Resolvers now point at the new home
        assert "tenants/_default/voice" in str(manager.profile_path())
        assert "tenants/_default/voice" in str(manager.vault_dir())

    def test_runtime_and_audit_state_is_never_migrated(self, monkeypatch, tmp_path):
        corvin_home = tmp_path / "corvin"
        legacy_dir = self._legacy_with_runtime_junk(tmp_path)

        monkeypatch.setenv("CORVIN_HOME", str(corvin_home))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))

        manager = VoiceConfigManager(tenant_id="_default")
        result = manager.migrate_from_legacy()
        new_dir = manager.voice_home()

        for forbidden in (
            "mac_active_chains", "chain_tails", "forge", "google", "whatsapp",
            "audit.jsonl", "audit_anchor.key", "audit_mac_active",
            "voice.log", "current.pgid", "tts.lock", "maintainer.key",
        ):
            assert not (new_dir / forbidden).exists(), (
                f"{forbidden} must not be copied into the tenant voice home"
            )

        # Total copied entries stay bounded by the allow-list, not by the size
        # of the legacy directory.
        copied = {p.name for p in new_dir.iterdir()} - {".migrated"}
        assert copied <= (MIGRATABLE_FILES | MIGRATABLE_DIRS), copied
        # Everything left behind is reported, not silently dropped.
        assert any("mac_active_chains" in w for w in result.warnings)


class TestLegacySourceIsolation:
    """An isolated CORVIN_HOME must never reach into the operator's real
    ~/.config/corvin-voice. That coupling is what made every console-app test
    pay a 1.4 GB copytree.
    """

    def test_redirected_home_does_not_use_ambient_legacy_dir(
        self, monkeypatch, tmp_path
    ):
        fake_xdg = tmp_path / "xdg"
        (fake_xdg / "corvin-voice").mkdir(parents=True)
        (fake_xdg / "corvin-voice" / "profile.json").write_text('{"who": "operator"}')

        monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(fake_xdg))
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "isolated-home"))

        manager = VoiceConfigManager(tenant_id="_default")

        # The path resolver still NAMES the ambient legacy dir ...
        assert manager.legacy_voice_config_dir() == fake_xdg / "corvin-voice"
        # ... but it is not an admissible source for this redirected home.
        assert manager.legacy_source_allowed() is False
        assert manager.has_legacy_config() is False
        assert manager.needs_migration() is False

        result = manager.migrate_from_legacy()
        assert result.success is True
        assert result.migrated_items == 0

        # And the read fallbacks stay inside the isolated home.
        assert str(tmp_path / "isolated-home") in str(manager.profile_path())
        assert "xdg" not in str(manager.profile_path())

    def test_explicit_voice_config_dir_is_always_an_allowed_source(
        self, monkeypatch, tmp_path
    ):
        legacy_dir = tmp_path / "explicit-legacy"
        legacy_dir.mkdir()
        (legacy_dir / "profile.json").write_text("{}")

        monkeypatch.setenv("VOICE_CONFIG_DIR", str(legacy_dir))
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "isolated-home"))

        manager = VoiceConfigManager(tenant_id="_default")
        assert manager.legacy_source_allowed() is True
        assert manager.needs_migration() is True

    def test_ambient_home_may_migrate_from_ambient_legacy_dir(
        self, monkeypatch, tmp_path
    ):
        """The real operator upgrade path: no CORVIN_HOME redirection.

        CORVIN_HOME and VOICE_CONFIG_DIR are unset, so the effective home IS the
        ambient home and the ambient (XDG) legacy dir is an admissible source.
        Only the migration DESTINATION is redirected, so the test never writes
        into the operator's live home.
        """
        xdg = tmp_path / "xdg"
        (xdg / "corvin-voice").mkdir(parents=True)
        (xdg / "corvin-voice" / "profile.json").write_text('{"who": "operator"}')

        monkeypatch.delenv("VOICE_CONFIG_DIR", raising=False)
        monkeypatch.delenv("CORVIN_HOME", raising=False)
        monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

        manager = VoiceConfigManager(tenant_id="_default")
        dest = tmp_path / "dest-home" / "tenants" / "_default"
        monkeypatch.setattr(manager, "_tenant_home", lambda: dest)

        assert manager.legacy_voice_config_dir() == xdg / "corvin-voice"
        assert manager.legacy_source_allowed() is True
        assert manager.needs_migration() is True

        result = manager.migrate_from_legacy()
        assert result.success is True, result.errors
        assert result.migrated_items == 1
        assert json.loads(
            (manager.voice_home() / "profile.json").read_text()
        )["who"] == "operator"
