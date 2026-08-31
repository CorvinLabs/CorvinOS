"""Token Encryption for GitHub Personal Access Tokens (ADR-0452).

Provides AES-256-GCM encryption/decryption for GitHub PATs stored in secrets.yaml.
Keys are sourced from environment variables only (never config files).

Load-bearing invariants:
- Encryption key must be 32 bytes (AES-256)
- Key sourced from CORVIN_GITHUB_TOKEN_KEY env var (fail-closed if missing)
- Tokens must start with 'ghp_' (GitHub PAT format)
- IV (nonce) is randomly generated per encryption
- Ciphertext format: base64(iv + ciphertext + tag) for transport
"""

from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from typing import Optional

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes


@dataclass(frozen=True)
class EncryptedToken:
    """Immutable result of token encryption."""

    ciphertext_b64: str  # base64(iv + ciphertext + tag)
    algorithm: str = "aes-256-gcm"  # Fixed; kept for audit trail


class TokenEncryptionError(Exception):
    """Base exception for token encryption failures."""
    pass


class InvalidKeyError(TokenEncryptionError):
    """Raised when encryption key is missing, wrong size, or invalid."""
    pass


class InvalidTokenFormatError(TokenEncryptionError):
    """Raised when token format is invalid (must start with 'ghp_')."""
    pass


class DecryptionError(TokenEncryptionError):
    """Raised when decryption fails (corrupted ciphertext or wrong key)."""
    pass


def get_encryption_key() -> bytes:
    """Retrieve encryption key from environment variable.

    Fail-closed: raises InvalidKeyError if key is missing or invalid.

    Returns:
        32-byte encryption key (AES-256)

    Raises:
        InvalidKeyError: if key missing, not 32 bytes, or not valid base64
    """
    key_b64 = os.environ.get("CORVIN_GITHUB_TOKEN_KEY")
    if not key_b64:
        raise InvalidKeyError(
            "CORVIN_GITHUB_TOKEN_KEY environment variable is not set. "
            "Token encryption requires a 32-byte AES-256 key."
        )

    try:
        key = base64.b64decode(key_b64)
    except Exception as e:
        raise InvalidKeyError(
            f"CORVIN_GITHUB_TOKEN_KEY is not valid base64: {e}"
        )

    if len(key) != 32:
        raise InvalidKeyError(
            f"CORVIN_GITHUB_TOKEN_KEY must be 32 bytes (256 bits), got {len(key)}"
        )

    return key


def validate_token_format(token: str) -> None:
    """Validate GitHub PAT format (must start with 'ghp_').

    Raises:
        InvalidTokenFormatError: if token doesn't start with 'ghp_'
    """
    if not token.startswith("ghp_"):
        raise InvalidTokenFormatError(
            f"GitHub token must start with 'ghp_', got: {token[:20]}..."
        )


def encrypt_token(token: str, key: Optional[bytes] = None) -> EncryptedToken:
    """Encrypt a GitHub PAT using AES-256-GCM.

    Args:
        token: Raw GitHub PAT (must start with 'ghp_')
        key: Optional encryption key (default: from env)

    Returns:
        EncryptedToken with base64-encoded ciphertext

    Raises:
        InvalidTokenFormatError: if token format is invalid
        InvalidKeyError: if key is missing or invalid
    """
    validate_token_format(token)

    if key is None:
        key = get_encryption_key()

    # Generate random 12-byte IV (nonce) for AES-GCM
    import os as os_module
    iv = os_module.urandom(12)

    # Encrypt token with associated data (empty for now, but could add tenant_id)
    cipher = AESGCM(key)
    plaintext = token.encode("utf-8")
    ciphertext = cipher.encrypt(iv, plaintext, associated_data=None)

    # Combine iv + ciphertext + tag into single blob for storage
    # Format: iv (12 bytes) + ciphertext (variable) + tag (16 bytes)
    # Note: AESGCM.encrypt() returns ciphertext with tag appended
    blob = iv + ciphertext

    return EncryptedToken(ciphertext_b64=base64.b64encode(blob).decode("utf-8"))


def decrypt_token(encrypted: EncryptedToken, key: Optional[bytes] = None) -> str:
    """Decrypt a GitHub PAT from EncryptedToken.

    Args:
        encrypted: EncryptedToken with ciphertext
        key: Optional encryption key (default: from env)

    Returns:
        Raw GitHub PAT string

    Raises:
        InvalidKeyError: if key is missing or invalid
        DecryptionError: if ciphertext is corrupted or key is wrong
    """
    if key is None:
        key = get_encryption_key()

    try:
        # Decode base64 ciphertext blob
        blob = base64.b64decode(encrypted.ciphertext_b64)

        # Split: first 12 bytes are IV, rest is ciphertext + tag
        if len(blob) < 12 + 16:  # IV (12) + minimum ciphertext (16 tag)
            raise DecryptionError(
                f"Ciphertext blob too short: {len(blob)} bytes"
            )

        iv = blob[:12]
        ciphertext_with_tag = blob[12:]

        # Decrypt
        cipher = AESGCM(key)
        plaintext = cipher.decrypt(iv, ciphertext_with_tag, associated_data=None)

        token = plaintext.decode("utf-8")

        # Verify token format
        validate_token_format(token)

        return token

    except InvalidTokenFormatError:
        raise
    except Exception as e:
        raise DecryptionError(
            f"Failed to decrypt token: {e}. "
            "This usually means the ciphertext is corrupted or the key is wrong."
        )


def reencrypt_token(encrypted: EncryptedToken, old_key: bytes, new_key: bytes) -> EncryptedToken:
    """Re-encrypt a token with a new key (for key rotation).

    Args:
        encrypted: EncryptedToken with current ciphertext
        old_key: Current encryption key
        new_key: New encryption key

    Returns:
        EncryptedToken encrypted with new_key

    Raises:
        DecryptionError: if decryption with old_key fails
    """
    plaintext = decrypt_token(encrypted, key=old_key)
    return encrypt_token(plaintext, key=new_key)
