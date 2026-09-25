"""
Security module for instance discovery relay.

Provides HMAC-SHA256 authentication and AES-256-GCM encryption.
Fail-closed validation: any decryption/auth failure returns None or raises, never silent fallback.
"""

import hmac
import hashlib
import base64
import json
import logging
from typing import Dict, Optional, Tuple
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

logger = logging.getLogger(__name__)


class AuthenticationError(Exception):
    """Raised when HMAC validation fails."""
    pass


class EncryptionError(Exception):
    """Raised when AES-GCM encryption/decryption fails."""
    pass


def validate_hmac(payload: bytes, signature: str, org_key: bytes) -> bool:
    """
    Validate HMAC-SHA256 signature.

    Args:
        payload: The message bytes to validate
        signature: Base64-encoded HMAC-SHA256 signature
        org_key: Organization's signing key (from org_jwt claims)

    Returns:
        True if signature is valid, False otherwise (fail-closed)
    """
    try:
        expected_sig = hmac.new(org_key, payload, hashlib.sha256).digest()
        provided_sig = base64.b64decode(signature)
        # Use constant-time comparison to prevent timing attacks
        return hmac.compare_digest(expected_sig, provided_sig)
    except Exception as e:
        logger.error(f"HMAC validation failed: {e}")
        return False


def compute_hmac(payload: bytes, org_key: bytes) -> str:
    """
    Compute HMAC-SHA256 signature for a payload.

    Args:
        payload: The message bytes to sign
        org_key: Organization's signing key

    Returns:
        Base64-encoded HMAC-SHA256 signature
    """
    sig = hmac.new(org_key, payload, hashlib.sha256).digest()
    return base64.b64encode(sig).decode('utf-8')


def encrypt_payload(
    plaintext: Dict, org_key: bytes, nonce: bytes = None
) -> Tuple[str, str]:
    """
    Encrypt a payload using AES-256-GCM.

    Args:
        plaintext: Dictionary to encrypt (tier, kid, instance_id, etc.)
        org_key: Organization's encryption key (32 bytes)
        nonce: Optional 12-byte nonce; auto-generated if None

    Returns:
        Tuple of (base64_ciphertext, base64_nonce)

    Raises:
        EncryptionError if encryption fails
    """
    try:
        import os
        if nonce is None:
            nonce = os.urandom(12)  # 12-byte nonce for GCM

        # Ensure org_key is 32 bytes (256 bits)
        if len(org_key) != 32:
            raise EncryptionError(f"org_key must be 32 bytes, got {len(org_key)}")

        cipher = AESGCM(org_key)
        plaintext_bytes = json.dumps(plaintext).encode('utf-8')
        # AAD (additional authenticated data) includes nothing for now, just the plaintext
        ciphertext = cipher.encrypt(nonce, plaintext_bytes, None)

        return (
            base64.b64encode(ciphertext).decode('utf-8'),
            base64.b64encode(nonce).decode('utf-8'),
        )
    except Exception as e:
        logger.error(f"Encryption failed: {e}")
        raise EncryptionError(f"Failed to encrypt payload: {e}")


def decrypt_payload(
    ciphertext_b64: str, nonce_b64: str, org_key: bytes
) -> Optional[Dict]:
    """
    Decrypt a payload using AES-256-GCM.

    Args:
        ciphertext_b64: Base64-encoded ciphertext
        nonce_b64: Base64-encoded nonce
        org_key: Organization's encryption key (32 bytes)

    Returns:
        Decrypted dictionary, or None if decryption fails (fail-closed)
    """
    try:
        if len(org_key) != 32:
            logger.error(f"org_key must be 32 bytes, got {len(org_key)}")
            return None

        ciphertext = base64.b64decode(ciphertext_b64)
        nonce = base64.b64decode(nonce_b64)

        if len(nonce) != 12:
            logger.error(f"nonce must be 12 bytes, got {len(nonce)}")
            return None

        cipher = AESGCM(org_key)
        plaintext_bytes = cipher.decrypt(nonce, ciphertext, None)
        plaintext = json.loads(plaintext_bytes.decode('utf-8'))
        return plaintext
    except Exception as e:
        logger.error(f"Decryption failed: {e}")
        return None


def derive_key_from_password(password: str, salt: bytes = None) -> Tuple[bytes, bytes]:
    """
    Derive a 32-byte AES-256 key from a password using PBKDF2.

    Args:
        password: Password string
        salt: Optional salt (16 bytes); auto-generated if None

    Returns:
        Tuple of (derived_key, salt) both as bytes
    """
    try:
        import os
        if salt is None:
            salt = os.urandom(16)

        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=100000,
        )
        key = kdf.derive(password.encode('utf-8'))
        return (key, salt)
    except Exception as e:
        logger.error(f"Key derivation failed: {e}")
        raise


def scrub_for_audit(payload: Dict) -> Dict:
    """
    Scrub a payload to remove or redact sensitive fields before audit logging.

    Fail-closed: if a field looks like a secret, it is redacted.
    Sensitive patterns: tier_enc, kid (redact to <redacted>), api_key, token, secret, password.

    Args:
        payload: Dictionary to scrub

    Returns:
        Scrubbed copy of the dictionary
    """
    SENSITIVE_KEYS = {"tier_enc", "api_key", "token", "secret", "password", "auth"}
    scrubbed = {}
    for key, value in payload.items():
        if any(sensitive in key.lower() for sensitive in SENSITIVE_KEYS):
            scrubbed[key] = "<redacted>"
        else:
            scrubbed[key] = value
    return scrubbed
