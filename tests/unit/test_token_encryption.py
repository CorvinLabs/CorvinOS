"""Unit tests for Token Encryption (ADR-0452).

Tests cover:
1. Encrypt/decrypt round-trip
2. Invalid key detection
3. Token format validation
4. Corrupt ciphertext handling
5. Key from env var only
6. Key rotation
"""

import pytest
import os
import base64
from unittest.mock import patch, MagicMock

from core.marketplace.auth.token_encryption import (
    EncryptedToken,
    TokenEncryptionError,
    InvalidKeyError,
    InvalidTokenFormatError,
    DecryptionError,
    get_encryption_key,
    validate_token_format,
    encrypt_token,
    decrypt_token,
    reencrypt_token,
)


class TestTokenFormatValidation:
    """Test GitHub PAT format validation."""

    def test_valid_token_format(self):
        """Valid token starting with 'ghp_' should not raise."""
        token = "ghp_1234567890abcdefghijklmnopqrstuvwxyz"
        validate_token_format(token)  # Should not raise

    def test_invalid_token_format_no_ghp_prefix(self):
        """Token not starting with 'ghp_' should raise InvalidTokenFormatError."""
        token = "gho_1234567890abcdefghijklmnopqrstuvwxyz"  # Wrong prefix
        with pytest.raises(InvalidTokenFormatError, match="must start with 'ghp_'"):
            validate_token_format(token)

    def test_invalid_token_format_empty(self):
        """Empty token should raise InvalidTokenFormatError."""
        with pytest.raises(InvalidTokenFormatError):
            validate_token_format("")

    def test_invalid_token_format_short(self):
        """Token too short should raise InvalidTokenFormatError."""
        with pytest.raises(InvalidTokenFormatError):
            validate_token_format("ghp_")


class TestEncryptionKeyManagement:
    """Test encryption key retrieval and validation."""

    def test_get_encryption_key_from_env(self, monkeypatch):
        """get_encryption_key should retrieve 32-byte key from env var."""
        # Generate valid 32-byte key and encode as base64
        valid_key = os.urandom(32)
        key_b64 = base64.b64encode(valid_key).decode("utf-8")

        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", key_b64)

        retrieved_key = get_encryption_key()
        assert retrieved_key == valid_key
        assert len(retrieved_key) == 32

    def test_get_encryption_key_missing_env(self, monkeypatch):
        """get_encryption_key should raise InvalidKeyError if env var missing."""
        monkeypatch.delenv("CORVIN_GITHUB_TOKEN_KEY", raising=False)

        with pytest.raises(InvalidKeyError, match="environment variable is not set"):
            get_encryption_key()

    def test_get_encryption_key_invalid_base64(self, monkeypatch):
        """get_encryption_key should raise InvalidKeyError if not valid base64."""
        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", "not-valid-base64!@#")

        with pytest.raises(InvalidKeyError, match="not valid base64"):
            get_encryption_key()

    def test_get_encryption_key_wrong_size(self, monkeypatch):
        """get_encryption_key should raise InvalidKeyError if key is not 32 bytes."""
        # 16-byte key (AES-128, not AES-256)
        wrong_size_key = os.urandom(16)
        key_b64 = base64.b64encode(wrong_size_key).decode("utf-8")

        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", key_b64)

        with pytest.raises(InvalidKeyError, match="must be 32 bytes"):
            get_encryption_key()


class TestEncryptDecryptRoundTrip:
    """Test encrypt/decrypt round-trip."""

    @pytest.fixture
    def valid_key(self, monkeypatch):
        """Fixture: valid 32-byte encryption key."""
        key = os.urandom(32)
        key_b64 = base64.b64encode(key).decode("utf-8")
        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", key_b64)
        return key

    def test_encrypt_decrypt_round_trip(self, valid_key):
        """Encrypt then decrypt should recover original token."""
        original_token = "ghp_abcdefghijklmnopqrstuvwxyz123456"

        encrypted = encrypt_token(original_token, key=valid_key)
        assert isinstance(encrypted, EncryptedToken)
        assert encrypted.algorithm == "aes-256-gcm"

        decrypted = decrypt_token(encrypted, key=valid_key)
        assert decrypted == original_token

    def test_encrypt_with_env_key(self, valid_key):
        """Encrypt should use key from env if not provided."""
        token = "ghp_xyz789"

        encrypted = encrypt_token(token)  # No key parameter
        decrypted = decrypt_token(encrypted)  # No key parameter
        assert decrypted == token

    def test_different_encryptions_produce_different_ciphertexts(self, valid_key):
        """Same token encrypted twice should produce different ciphertexts (due to random IV)."""
        token = "ghp_sametoken"

        encrypted1 = encrypt_token(token, key=valid_key)
        encrypted2 = encrypt_token(token, key=valid_key)

        # Ciphertexts should differ due to random IV
        assert encrypted1.ciphertext_b64 != encrypted2.ciphertext_b64

        # But both should decrypt to same token
        assert decrypt_token(encrypted1, key=valid_key) == token
        assert decrypt_token(encrypted2, key=valid_key) == token


class TestCorruptedCiphertext:
    """Test graceful handling of corrupted ciphertext."""

    @pytest.fixture
    def valid_key(self, monkeypatch):
        """Fixture: valid encryption key."""
        key = os.urandom(32)
        key_b64 = base64.b64encode(key).decode("utf-8")
        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", key_b64)
        return key

    def test_decrypt_corrupted_ciphertext_blob_too_short(self, valid_key):
        """Decrypt should raise DecryptionError if ciphertext blob is too short."""
        # Create corrupted EncryptedToken with too-short blob
        corrupted = EncryptedToken(ciphertext_b64=base64.b64encode(b"short").decode("utf-8"))

        with pytest.raises(DecryptionError, match="too short"):
            decrypt_token(corrupted, key=valid_key)

    def test_decrypt_corrupted_ciphertext_auth_fails(self, valid_key):
        """Decrypt should raise DecryptionError if authentication fails (wrong key or corrupted data)."""
        token = "ghp_correcttoken"
        encrypted = encrypt_token(token, key=valid_key)

        # Use wrong key to decrypt
        wrong_key = os.urandom(32)

        with pytest.raises(DecryptionError):
            decrypt_token(encrypted, key=wrong_key)

    def test_decrypt_invalid_base64(self, valid_key):
        """Decrypt should raise DecryptionError if ciphertext is not valid base64."""
        corrupted = EncryptedToken(ciphertext_b64="not-valid-base64!@#$%")

        with pytest.raises(DecryptionError):
            decrypt_token(corrupted, key=valid_key)


class TestKeyRotation:
    """Test key rotation functionality."""

    @pytest.fixture
    def keys(self):
        """Fixture: old and new encryption keys."""
        old_key = os.urandom(32)
        new_key = os.urandom(32)
        return old_key, new_key

    def test_reencrypt_token_with_new_key(self, keys):
        """Re-encrypt token with new key should allow decryption with new key."""
        old_key, new_key = keys
        original_token = "ghp_keyrotationtest"

        # Encrypt with old key
        encrypted_old = encrypt_token(original_token, key=old_key)

        # Re-encrypt with new key
        encrypted_new = reencrypt_token(encrypted_old, old_key=old_key, new_key=new_key)

        # Should decrypt with new key
        decrypted = decrypt_token(encrypted_new, key=new_key)
        assert decrypted == original_token

        # Should NOT decrypt with old key (different ciphertext)
        with pytest.raises(DecryptionError):
            decrypt_token(encrypted_new, key=old_key)


class TestEncryptedTokenDataclass:
    """Test EncryptedToken immutability."""

    def test_encrypted_token_is_frozen(self):
        """EncryptedToken should be immutable."""
        encrypted = EncryptedToken(ciphertext_b64="test_ciphertext")

        with pytest.raises(AttributeError):  # frozen dataclass
            encrypted.ciphertext_b64 = "modified"

    def test_encrypted_token_algorithm_default(self):
        """EncryptedToken should have default algorithm 'aes-256-gcm'."""
        encrypted = EncryptedToken(ciphertext_b64="test")
        assert encrypted.algorithm == "aes-256-gcm"


class TestErrorMessages:
    """Test that error messages are user-friendly."""

    @pytest.fixture
    def valid_key(self, monkeypatch):
        key = os.urandom(32)
        key_b64 = base64.b64encode(key).decode("utf-8")
        monkeypatch.setenv("CORVIN_GITHUB_TOKEN_KEY", key_b64)
        return key

    def test_invalid_token_format_error_message(self):
        """InvalidTokenFormatError should include helpful message."""
        with pytest.raises(InvalidTokenFormatError) as exc_info:
            validate_token_format("bad_token")

        assert "ghp_" in str(exc_info.value)

    def test_decryption_error_message(self, valid_key):
        """DecryptionError should provide diagnostic message."""
        wrong_key = os.urandom(32)
        encrypted = encrypt_token("ghp_test", key=valid_key)

        with pytest.raises(DecryptionError) as exc_info:
            decrypt_token(encrypted, key=wrong_key)

        assert "decrypt" in str(exc_info.value).lower()
