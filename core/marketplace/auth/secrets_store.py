"""Encrypted secrets storage for GitHub tokens (ADR-0452).

Manages persisted, encrypted GitHub PATs in secrets.yaml.
- Tokens are encrypted at rest using token_encryption module
- Keys indexed by repository URL for lookup
- Multi-tenant isolated (every query filtered by tenant_id)
"""

from __future__ import annotations

import yaml
from pathlib import Path
from typing import Optional, Dict, Any
from dataclasses import dataclass

from core.marketplace.auth.token_encryption import (
    EncryptedToken,
    encrypt_token,
    decrypt_token,
    TokenEncryptionError,
)


@dataclass
class StoredToken:
    """A stored, encrypted token entry."""
    repo_url: str
    encrypted: EncryptedToken
    tenant_id: str
    created_at: str  # ISO format timestamp


class SecretsStoreError(Exception):
    """Base exception for secrets store failures."""
    pass


class TokenNotFoundError(SecretsStoreError):
    """Raised when token lookup fails."""
    pass


class SecretsStore:
    """Encrypted token storage backed by secrets.yaml."""

    def __init__(self, secrets_path: Optional[Path] = None):
        """Initialize secrets store.

        Args:
            secrets_path: Path to secrets.yaml (default: ~/.corvin/secrets.yaml)
        """
        if secrets_path is None:
            from core.orchestration.quota_gate import corvin_home
            secrets_path = corvin_home() / "secrets.yaml"

        self.secrets_path = Path(secrets_path)
        self._ensure_file_exists()

    def _ensure_file_exists(self) -> None:
        """Create secrets.yaml if it doesn't exist."""
        if not self.secrets_path.exists():
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.secrets_path, "w") as f:
                yaml.dump({"repositories": {}}, f)

    def _load_secrets(self) -> Dict[str, Any]:
        """Load and parse secrets.yaml."""
        try:
            with open(self.secrets_path, "r") as f:
                data = yaml.safe_load(f) or {}
                return data.get("repositories", {})
        except Exception as e:
            raise SecretsStoreError(f"Failed to load secrets.yaml: {e}")

    def _save_secrets(self, secrets: Dict[str, Any]) -> None:
        """Save secrets to secrets.yaml."""
        try:
            with open(self.secrets_path, "w") as f:
                yaml.dump({"repositories": secrets}, f)
        except Exception as e:
            raise SecretsStoreError(f"Failed to save secrets.yaml: {e}")

    def store_token(
        self,
        repo_url: str,
        token: str,
        tenant_id: str,
    ) -> None:
        """Store an encrypted token for a repository.

        Args:
            repo_url: GitHub repository URL
            token: Raw GitHub PAT (must start with 'ghp_')
            tenant_id: Tenant ID for isolation

        Raises:
            TokenEncryptionError: if token format is invalid
            SecretsStoreError: if write fails
        """
        from datetime import datetime, timezone

        # Encrypt token
        encrypted = encrypt_token(token)

        # Load current secrets
        secrets = self._load_secrets()

        # Store with metadata (tenant_id for isolation, created_at for audit)
        tenant_key = f"{tenant_id}:{repo_url}"
        secrets[tenant_key] = {
            "repo_url": repo_url,
            "tenant_id": tenant_id,
            "ciphertext_b64": encrypted.ciphertext_b64,
            "algorithm": encrypted.algorithm,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }

        # Save back to disk
        self._save_secrets(secrets)

    def retrieve_token(self, repo_url: str, tenant_id: str) -> str:
        """Retrieve and decrypt token for a repository.

        Args:
            repo_url: GitHub repository URL
            tenant_id: Tenant ID for isolation

        Returns:
            Raw GitHub PAT

        Raises:
            TokenNotFoundError: if token not found for this tenant/repo
            TokenEncryptionError: if decryption fails (wrong key or corrupted)
            SecretsStoreError: if read fails
        """
        secrets = self._load_secrets()
        tenant_key = f"{tenant_id}:{repo_url}"

        if tenant_key not in secrets:
            raise TokenNotFoundError(
                f"No token stored for {repo_url} in tenant {tenant_id}"
            )

        entry = secrets[tenant_key]

        try:
            encrypted = EncryptedToken(ciphertext_b64=entry["ciphertext_b64"])
            return decrypt_token(encrypted)
        except Exception as e:
            raise SecretsStoreError(
                f"Failed to decrypt token for {repo_url}: {e}"
            )

    def delete_token(self, repo_url: str, tenant_id: str) -> None:
        """Delete a stored token.

        Args:
            repo_url: GitHub repository URL
            tenant_id: Tenant ID

        Raises:
            TokenNotFoundError: if token not found
            SecretsStoreError: if write fails
        """
        secrets = self._load_secrets()
        tenant_key = f"{tenant_id}:{repo_url}"

        if tenant_key not in secrets:
            raise TokenNotFoundError(
                f"No token to delete for {repo_url} in tenant {tenant_id}"
            )

        del secrets[tenant_key]
        self._save_secrets(secrets)

    def list_tokens_for_tenant(self, tenant_id: str) -> list[str]:
        """List all repository URLs with stored tokens for a tenant.

        Returns:
            List of repo URLs (only this tenant's tokens)
        """
        secrets = self._load_secrets()
        repo_urls = []

        for tenant_key in secrets.keys():
            if tenant_key.startswith(f"{tenant_id}:"):
                repo_url = tenant_key.split(":", 1)[1]
                repo_urls.append(repo_url)

        return repo_urls

    def token_exists(self, repo_url: str, tenant_id: str) -> bool:
        """Check if a token is stored without decrypting it.

        Args:
            repo_url: GitHub repository URL
            tenant_id: Tenant ID

        Returns:
            True if token exists for this tenant/repo
        """
        secrets = self._load_secrets()
        tenant_key = f"{tenant_id}:{repo_url}"
        return tenant_key in secrets


# Global instance (lazy-loaded)
_store_instance: Optional[SecretsStore] = None


def get_secrets_store(secrets_path: Optional[Path] = None) -> SecretsStore:
    """Get or create the global secrets store instance.

    Args:
        secrets_path: Optional override for secrets.yaml path

    Returns:
        SecretsStore instance
    """
    global _store_instance

    if _store_instance is None or secrets_path is not None:
        _store_instance = SecretsStore(secrets_path)

    return _store_instance
