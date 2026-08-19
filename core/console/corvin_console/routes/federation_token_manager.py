"""Secure federation token management.

Handles generation, storage, validation, and rotation of federation auth tokens.

ADR-0XXX: Federation Authentication (GH-004 remediation)
GDPR Art. 32: Confidentiality and integrity of authentication credentials
"""

import os
import secrets
import json
import logging
from pathlib import Path
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)


class FederationTokenError(Exception):
    """Base exception for federation token operations."""
    pass


class FederationTokenManager:
    """Manage federation authentication tokens securely.

    Responsibilities:
    - Generate cryptographically secure tokens (32 bytes = 256 bits)
    - Store tokens with restrictive permissions (0600)
    - Validate token format and length
    - Track token metadata (created_at, expires_at, rotation_count)
    - Support token rotation on TTL or compromise
    """

    # Token security parameters
    TOKEN_VALIDITY_HOURS = 24
    MIN_TOKEN_LENGTH = 32
    MAX_TOKEN_LENGTH = 256

    def __init__(self, tenant_id: str = "_default"):
        """Initialize token manager.

        Args:
            tenant_id: Tenant identifier

        Raises:
            FederationTokenError: If tenant_id is invalid
        """
        if not tenant_id or not isinstance(tenant_id, str):
            raise FederationTokenError(f"Invalid tenant_id: {tenant_id}")

        self.tenant_id = tenant_id
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self._token_file = self.tenant_path / 'federation-token.secure'
        self._token_metadata_file = self.tenant_path / 'federation-token-meta.json'

        # Ensure secure storage
        self._setup_secure_storage()

    def _setup_secure_storage(self):
        """Ensure token directory and files have restrictive permissions.

        Creates tenant path with 0700 permissions (owner-only).
        """
        try:
            self.tenant_path.mkdir(parents=True, exist_ok=True, mode=0o700)

            # Verify directory permissions
            st = self.tenant_path.stat()
            mode = oct(st.st_mode)[-3:]
            if mode != '700':
                logger.warning(
                    f"Tenant directory has permissive mode {mode}, "
                    f"fixing to 0700"
                )
                os.chmod(self.tenant_path, 0o700)

            # Create token file with 0600 if missing
            if not self._token_file.exists():
                self._token_file.touch(mode=0o600)
                os.chmod(self._token_file, 0o600)

        except Exception as e:
            logger.error(f"Failed to setup secure storage: {e}")
            raise FederationTokenError(f"Storage setup failed: {e}") from e

    def get_or_generate_token(self, force_rotate: bool = False) -> str:
        """Get existing token or generate new one.

        If no valid token exists, generates a new one.
        If force_rotate=True, generates new token even if valid one exists.

        Args:
            force_rotate: If True, generate new token regardless

        Returns:
            Federation auth token (32+ bytes, URL-safe)

        Raises:
            FederationTokenError: If token generation or storage fails
        """
        if not force_rotate and self._token_file.exists():
            try:
                token = self._token_file.read_text().strip()

                # Validate existing token
                if self._validate_token(token):
                    logger.debug(
                        f"Using existing federation token (tenant={self.tenant_id})"
                    )
                    return token
                else:
                    logger.warning(
                        f"Existing token invalid, regenerating (tenant={self.tenant_id})"
                    )
            except Exception as e:
                logger.warning(f"Failed to read existing token: {e}")

        # Generate new token
        return self._generate_new_token()

    def _generate_new_token(self) -> str:
        """Generate and store new federation token.

        Token is generated using os.urandom for cryptographic quality,
        then encoded as URL-safe base64.

        Returns:
            New federation token (32+ bytes)

        Raises:
            FederationTokenError: If generation or storage fails
        """
        try:
            # Generate 32 bytes (256 bits) of random data
            token = secrets.token_urlsafe(32)

            # Validate generated token
            if len(token) < self.MIN_TOKEN_LENGTH:
                raise FederationTokenError(
                    f"Generated token too short (len={len(token)})"
                )

            # Store token securely
            self._token_file.write_text(token)
            os.chmod(self._token_file, 0o600)

            # Record metadata
            rotation_count = self._get_rotation_count() + 1
            metadata = {
                'tenant_id': self.tenant_id,
                'generated_at': datetime.utcnow().isoformat(),
                'expires_at': (
                    datetime.utcnow() + timedelta(hours=self.TOKEN_VALIDITY_HOURS)
                ).isoformat(),
                'rotation_count': rotation_count,
            }

            with open(self._token_metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
            os.chmod(self._token_metadata_file, 0o600)

            logger.info(
                f"Generated federation token (tenant={self.tenant_id}, "
                f"rotation={rotation_count}, validity_hours={self.TOKEN_VALIDITY_HOURS})"
            )

            return token

        except Exception as e:
            logger.error(f"Failed to generate token: {e}")
            raise FederationTokenError(f"Token generation failed: {e}") from e

    def _validate_token(self, token: str) -> bool:
        """Validate token format, length, and character set.

        Tokens must be:
        - String type
        - 32-256 characters
        - Only alphanumeric + '-' and '_' (URL-safe base64)

        Args:
            token: Token to validate

        Returns:
            True if token is valid, False otherwise
        """
        if not isinstance(token, str):
            return False

        if not (self.MIN_TOKEN_LENGTH <= len(token) <= self.MAX_TOKEN_LENGTH):
            return False

        # URL-safe alphabet: A-Z, a-z, 0-9, -, _
        for char in token:
            if not (char.isalnum() or char in '-_'):
                return False

        return True

    def _get_rotation_count(self) -> int:
        """Get current rotation count from metadata.

        Returns:
            Rotation count (0 if metadata doesn't exist)
        """
        if not self._token_metadata_file.exists():
            return 0

        try:
            with open(self._token_metadata_file) as f:
                metadata = json.load(f)
            return metadata.get('rotation_count', 0)
        except Exception as e:
            logger.warning(f"Failed to read metadata: {e}")
            return 0

    def is_token_expired(self) -> bool:
        """Check if current token has expired.

        Token expires after TOKEN_VALIDITY_HOURS.

        Returns:
            True if expired, False otherwise (or on error, assume not expired)
        """
        if not self._token_metadata_file.exists():
            # No metadata = token is old/invalid
            return True

        try:
            with open(self._token_metadata_file) as f:
                metadata = json.load(f)

            expires_at_str = metadata.get('expires_at')
            if not expires_at_str:
                return True

            expires_at = datetime.fromisoformat(expires_at_str)
            is_expired = datetime.utcnow() > expires_at

            if is_expired:
                logger.warning(
                    f"Federation token expired (tenant={self.tenant_id})"
                )

            return is_expired

        except Exception as e:
            logger.warning(f"Failed to check token expiry: {e}")
            return False  # Assume not expired on error (fail-safe)

    def rotate_token(self) -> str:
        """Force token rotation.

        Used when:
        - Token expires (24h TTL)
        - Token is suspected compromised
        - Operator requests rotation

        Returns:
            New federation token

        Raises:
            FederationTokenError: If rotation fails
        """
        logger.warning(f"Rotating federation token (tenant={self.tenant_id})")
        return self._generate_new_token()

    def get_token_metadata(self) -> Optional[dict]:
        """Get token metadata (creation time, expiry, rotation count).

        Returns:
            Metadata dict, or None if not found
        """
        if not self._token_metadata_file.exists():
            return None

        try:
            with open(self._token_metadata_file) as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Failed to read token metadata: {e}")
            return None

    def revoke_token(self):
        """Revoke token immediately (on suspected compromise).

        Deletes token and metadata files. Next get_or_generate_token() will
        create new one.

        Raises:
            FederationTokenError: If revocation fails
        """
        try:
            if self._token_file.exists():
                self._token_file.unlink()
                logger.warning(
                    f"Revoked federation token (tenant={self.tenant_id})"
                )

            if self._token_metadata_file.exists():
                self._token_metadata_file.unlink()

        except Exception as e:
            logger.error(f"Failed to revoke token: {e}")
            raise FederationTokenError(f"Token revocation failed: {e}") from e
