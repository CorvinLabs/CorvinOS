#!/usr/bin/env python3
"""test_secrets_manager.py — tests for Phase 1b SecretsStore encryption.

Tests cover:
- Encrypt/decrypt roundtrip
- Master key generation and persistence
- Single-secret operations (load/save/delete)
- Migration from legacy .env files
- Key material security (permissions)
- Error handling (corrupt files, key mismatch)
- SSOT: SecretsStore and resolve_key/resolve_by_env_var return same values
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (
    _REPO,
    _REPO / "operator" / "forge",
    _REPO / "operator" / "bridges" / "shared",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import provider_keys as provider_keys_module
from provider_keys import SecretsStore, resolve_key, resolve_by_env_var


class TestSecretsStoreBasics:
    """Basic SecretsStore encrypt/decrypt roundtrip tests."""

    def test_encrypt_decrypt_roundtrip(self, tmp_path, monkeypatch):
        """Encrypt then decrypt returns original secrets dict."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        secrets = {
            "ANTHROPIC_API_KEY": "sk-ant-123456789",
            "OPENAI_API_KEY": "sk-org-abcdef",
            "CUSTOM_TOKEN": "token-xyz-custom",
        }

        store = SecretsStore()
        envelope = store.encrypt_secrets(secrets)

        # Verify envelope structure
        assert envelope["version"] == "1.0"
        assert envelope["algorithm"] == "AES-128-CBC (Fernet)"
        assert "payload" in envelope
        assert "encrypted_at" in envelope

        # Verify decrypt
        loaded = store.decrypt_secrets()
        assert loaded == secrets

    def test_empty_store_returns_empty_dict(self, tmp_path, monkeypatch):
        """Decrypt from non-existent secrets.enc returns empty dict."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        result = store.decrypt_secrets()
        assert result == {}

    def test_master_key_generation(self, tmp_path, monkeypatch):
        """First call generates tenant_master.key with 0o600 permissions."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        assert not store.master_key_path.exists()

        key1 = store._ensure_master_key()

        # Verify key was created
        assert store.master_key_path.exists()
        # Check permissions (mode 0o600 = owner read/write only)
        assert store.master_key_path.stat().st_mode & 0o077 == 0

        # Verify key is re-used on second call
        key2 = store._ensure_master_key()
        assert key1 == key2

    def test_master_key_persistence(self, tmp_path, monkeypatch):
        """Master key persists across SecretsStore instances."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        # Store 1: create key
        store1 = SecretsStore()
        store1.encrypt_secrets({"KEY1": "value1"})
        key1 = store1.master_key_path.read_bytes()

        # Store 2: load same key
        store2 = SecretsStore()
        key2 = store2.master_key_path.read_bytes()

        assert key1 == key2

        # Store 2 can decrypt what Store 1 encrypted
        secrets = store2.decrypt_secrets()
        assert secrets == {"KEY1": "value1"}


class TestSecretsStoreSingleOps:
    """Single secret load/save/delete operations."""

    def test_load_secret(self, tmp_path, monkeypatch):
        """Load a single secret value."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("TEST_KEY", "test_value")

        result = store.load_secret("TEST_KEY")
        assert result == "test_value"

    def test_load_secret_not_found_returns_default(self, tmp_path, monkeypatch):
        """Load non-existent key returns default."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        result = store.load_secret("NONEXISTENT", default="default_val")
        assert result == "default_val"

    def test_save_secret_creates_new(self, tmp_path, monkeypatch):
        """Save a new secret creates it."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("NEW_KEY", "new_value")

        # Load it back
        loaded = store.load_secret("NEW_KEY")
        assert loaded == "new_value"

    def test_save_secret_overwrites_existing(self, tmp_path, monkeypatch):
        """Save overwrites an existing secret."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("KEY", "value1")
        store.save_secret("KEY", "value2")

        result = store.load_secret("KEY")
        assert result == "value2"

    def test_delete_secret_existing(self, tmp_path, monkeypatch):
        """Delete removes an existing secret."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("KEY_TO_DELETE", "value")
        assert store.load_secret("KEY_TO_DELETE") == "value"

        deleted = store.delete_secret("KEY_TO_DELETE")
        assert deleted is True
        assert store.load_secret("KEY_TO_DELETE") is None

    def test_delete_secret_nonexistent(self, tmp_path, monkeypatch):
        """Delete non-existent secret returns False."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        deleted = store.delete_secret("NONEXISTENT")
        assert deleted is False

    def test_delete_last_secret_removes_file(self, tmp_path, monkeypatch):
        """Deleting the last secret removes secrets.enc file."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("ONLY_KEY", "value")
        assert store.secrets_path.exists()

        store.delete_secret("ONLY_KEY")
        assert not store.secrets_path.exists()

    def test_list_secrets(self, tmp_path, monkeypatch):
        """List all secret keys."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.save_secret("KEY_A", "value_a")
        store.save_secret("KEY_B", "value_b")
        store.save_secret("KEY_C", "value_c")

        keys = store.list_secrets()
        assert sorted(keys) == ["KEY_A", "KEY_B", "KEY_C"]

    def test_list_secrets_empty(self, tmp_path, monkeypatch):
        """List secrets on empty store returns empty list."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        keys = store.list_secrets()
        assert keys == []


class TestSecretsStoreMigration:
    """Test migration from legacy .env files."""

    def test_migrate_from_env_success(self, tmp_path, monkeypatch):
        """Migrate .env file to secrets.enc."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        # Create legacy .env in tmp_path directly
        env_file = tmp_path / ".env"
        env_file.write_text(
            "ANTHROPIC_API_KEY=sk-ant-123\nOPENAI_API_KEY=sk-org-456\n"
        )

        store = SecretsStore()
        result = store.migrate_from_env(env_file)

        # Verify migration result
        assert result == {
            "ANTHROPIC_API_KEY": "sk-ant-123",
            "OPENAI_API_KEY": "sk-org-456",
        }

        # Verify .env was moved to .env.backup
        assert not env_file.exists()
        assert env_file.with_suffix(".env.backup").exists()

        # Verify secrets are encrypted
        secrets = store.decrypt_secrets()
        assert secrets == result

    def test_migrate_from_env_not_found(self, tmp_path, monkeypatch):
        """Migrate from non-existent .env returns empty dict."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        env_file = tmp_path / "nonexistent.env"
        store = SecretsStore()
        result = store.migrate_from_env(env_file)

        assert result == {}

    def test_migrate_from_env_strips_quotes(self, tmp_path, monkeypatch):
        """Migration strips surrounding quotes from values."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        env_file = tmp_path / ".env"
        env_file.write_text(
            'QUOTED_SINGLE=\'value1\'\nQUOTED_DOUBLE="value2"\nUNQUOTED=value3\n'
        )

        store = SecretsStore()
        result = store.migrate_from_env(env_file)

        assert result == {
            "QUOTED_SINGLE": "value1",
            "QUOTED_DOUBLE": "value2",
            "UNQUOTED": "value3",
        }

    def test_migrate_from_env_skips_comments(self, tmp_path, monkeypatch):
        """Migration skips comment lines."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        env_file = tmp_path / ".env"
        env_file.write_text(
            "# This is a comment\nKEY1=value1\n# Another comment\nKEY2=value2\n"
        )

        store = SecretsStore()
        result = store.migrate_from_env(env_file)

        assert result == {"KEY1": "value1", "KEY2": "value2"}


class TestSecretsStoreErrors:
    """Error handling tests."""

    def test_invalid_key_raises_on_decrypt(self, tmp_path, monkeypatch):
        """Decrypting with wrong key raises ValueError."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store1 = SecretsStore()
        store1.encrypt_secrets({"KEY": "value"})

        # Corrupt the master key
        from cryptography.fernet import Fernet
        store1.master_key_path.write_bytes(Fernet.generate_key())

        # Try to decrypt with new key
        with pytest.raises(ValueError, match="failed to decrypt secrets"):
            store1.decrypt_secrets()

    def test_corrupted_secrets_file_raises(self, tmp_path, monkeypatch):
        """Corrupted secrets.enc raises ValueError."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        store.secrets_path.write_text("{ invalid json }")

        with pytest.raises(ValueError, match="invalid secrets.enc format"):
            store.decrypt_secrets()

    def test_missing_payload_in_envelope_raises(self, tmp_path, monkeypatch):
        """Missing payload in envelope raises ValueError."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        envelope = {"version": "1.0", "encrypted_at": "2026-07-27T00:00:00Z"}
        store.secrets_path.write_text(json.dumps(envelope))

        with pytest.raises(Exception):  # Could be KeyError or ValueError
            store.decrypt_secrets()


class TestSecretsStoreTenantIsolation:
    """Test tenant-scoped storage."""

    def test_different_tenants_use_different_stores(self, tmp_path, monkeypatch):
        """Different tenant IDs use separate secrets.enc and master keys."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store1 = SecretsStore(tenant_id="tenant1")
        store1.save_secret("KEY", "tenant1_value")

        store2 = SecretsStore(tenant_id="tenant2")
        store2.save_secret("KEY", "tenant2_value")

        # Each tenant has its own master key
        assert store1.master_key_path != store2.master_key_path

        # Each tenant has its own secrets.enc
        assert store1.secrets_path != store2.secrets_path

        # Values are isolated
        assert store1.load_secret("KEY") == "tenant1_value"
        assert store2.load_secret("KEY") == "tenant2_value"


class TestSecretsStoreIntegrationWithResolver:
    """Integration tests with resolve_key and resolve_by_env_var."""

    def test_resolve_key_uses_secrets_store(self, tmp_path, monkeypatch):
        """resolve_key checks secrets.enc after env and before service.env."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))

        # Clean relevant env vars
        for key in ("OPENAI_API_KEY", "CORVIN_STT_OPENAI_KEY"):
            monkeypatch.delenv(key, raising=False)

        # Save to secrets.enc using the canonical env var name
        store = SecretsStore()
        store.save_secret("OPENAI_API_KEY", "sk-secret-from-store")

        # resolve_key should find it in secrets.enc
        result = resolve_key("openai_api_key")
        assert result == "sk-secret-from-store"

    def test_resolve_key_env_wins_over_secrets(self, tmp_path, monkeypatch):
        """Process env wins over secrets.enc."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

        store = SecretsStore()
        store.save_secret("OPENAI_API_KEY", "sk-from-store")

        # Env should win
        result = resolve_key("openai_api_key")
        assert result == "sk-from-env"

    def test_resolve_by_env_var_uses_secrets_store(self, tmp_path, monkeypatch):
        """resolve_by_env_var checks secrets.enc."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))

        # Clean env
        monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)

        store = SecretsStore()
        store.save_secret("OPENROUTER_API_KEY", "sk-openrouter-secret")

        result = resolve_by_env_var("OPENROUTER_API_KEY")
        assert result == "sk-openrouter-secret"

    def test_resolve_precedence_order(self, tmp_path, monkeypatch):
        """Verify complete precedence: env → secrets.enc → service.env."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)

        # Set only in service.env
        service_env = tmp_path / "service.env"
        service_env.write_text("OPENAI_API_KEY=sk-from-service-env\n")

        # store.save_secret does not exist yet
        result = resolve_key("openai_api_key")
        assert result == "sk-from-service-env"

        # Now save to secrets.enc using canonical env var name
        store = SecretsStore()
        store.save_secret("OPENAI_API_KEY", "sk-from-secrets-enc")

        # secrets.enc should win over service.env
        result = resolve_key("openai_api_key")
        assert result == "sk-from-secrets-enc"

        # Now set env var
        monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")

        # env should win over both
        result = resolve_key("openai_api_key")
        assert result == "sk-from-env"


class TestSecretsStoreLoad:
    """Load_secret error handling and edge cases."""

    def test_load_secret_on_corrupted_store(self, tmp_path, monkeypatch):
        """load_secret returns default on corrupted store."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        store.secrets_path.write_text("{ invalid }")

        # Should not raise, just return default
        result = store.load_secret("KEY", default="fallback")
        assert result == "fallback"

    def test_list_secrets_on_corrupted_store(self, tmp_path, monkeypatch):
        """list_secrets returns empty on error."""
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))

        store = SecretsStore()
        store.secrets_path.parent.mkdir(parents=True, exist_ok=True)
        store.secrets_path.write_text("{ invalid }")

        result = store.list_secrets()
        assert result == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
