"""Marketplace authentication and token management (ADR-0452)."""

from .token_encryption import (
    encrypt_token,
    decrypt_token,
    EncryptedToken,
    TokenEncryptionError,
    InvalidKeyError,
    InvalidTokenFormatError,
    DecryptionError,
)

from .secrets_store import (
    SecretsStore,
    StoredToken,
    SecretsStoreError,
    TokenNotFoundError,
    get_secrets_store,
)

__all__ = [
    "encrypt_token",
    "decrypt_token",
    "EncryptedToken",
    "TokenEncryptionError",
    "InvalidKeyError",
    "InvalidTokenFormatError",
    "DecryptionError",
    "SecretsStore",
    "StoredToken",
    "SecretsStoreError",
    "TokenNotFoundError",
    "get_secrets_store",
]
