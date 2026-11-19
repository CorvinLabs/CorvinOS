"""Encrypted secrets storage for GitHub tokens (ADR-0452).

Manages persisted, encrypted GitHub PATs in secrets.yaml.
- Tokens are encrypted at rest using token_encryption module
- Keys indexed by repository URL for lookup
- Multi-tenant isolated (every query filtered by tenant_id)
"""

from __future__ import annotations

import contextlib
import os
import tempfile
import threading
import yaml
from pathlib import Path
from typing import Optional, Dict, Any, Iterator
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

    # One lock per process too: flock is per open file description, and two threads of one
    # process each opening the lock file would be serialised by it - but the in-process lock
    # is the cheap, portable half and covers a platform without a file lock.
    # A plain Lock on purpose, NOT an RLock: _locked() opens a NEW descriptor on the lock file
    # at every entry, and flock is per open file description - a re-entered RLock would pass
    # the in-process half and then block forever on its own outer flock. Nothing here enters
    # _locked() twice (the helpers below say "caller holds _locked()"); if a future change
    # does, it must fail at the first, visible lock, not at the second, silent one.
    _process_lock = threading.Lock()

    @contextlib.contextmanager
    def _locked(self) -> Iterator[None]:
        """Exclusive lock over a read-modify-write of secrets.yaml (review PLS-7).

        Held on a sibling ``<name>.lock`` file, never on secrets.yaml itself: the data file is
        replaced by ``os.replace`` on every save, and a lock on the old inode would not exclude
        a writer that opened the new one. Without the lock two writers each loaded the file,
        added their own token and wrote back - the later write dropped the earlier token.
        """
        lock_path = self.secrets_path.with_name(self.secrets_path.name + ".lock")
        with self._process_lock:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
            try:
                try:
                    import fcntl  # noqa: PLC0415 - POSIX
                    fcntl.flock(fd, fcntl.LOCK_EX)
                    unlock = lambda: fcntl.flock(fd, fcntl.LOCK_UN)  # noqa: E731
                except ImportError:  # Windows
                    import msvcrt  # noqa: PLC0415
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    unlock = lambda: (os.lseek(fd, 0, os.SEEK_SET),  # noqa: E731
                                      msvcrt.locking(fd, msvcrt.LK_UNLCK, 1))
                try:
                    yield
                finally:
                    with contextlib.suppress(OSError):
                        unlock()
            finally:
                os.close(fd)

    def _ensure_file_exists(self) -> None:
        """Create secrets.yaml (mode 0o600) if it doesn't exist; tighten a looser one."""
        try:
            with self._locked():
                if not self.secrets_path.exists():
                    self._write_atomic({"repositories": {}})
                else:
                    self._tighten_mode()
        except SecretsStoreError:
            raise
        except Exception as e:  # noqa: BLE001
            raise SecretsStoreError(f"Failed to initialise secrets.yaml: {e}")

    def _tighten_mode(self) -> None:
        """An existing file written before PLS-7 is at the umask (typically 0o644): the
        ciphertexts were world-readable. Best effort - a file this process may not chmod is
        left as it is and the next save replaces it at 0o600."""
        with contextlib.suppress(OSError):
            if os.name == "posix" and (self.secrets_path.stat().st_mode & 0o077):
                os.chmod(self.secrets_path, 0o600)

    def _load_secrets(self) -> Dict[str, Any]:
        """Load and parse secrets.yaml."""
        try:
            with open(self.secrets_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
                return data.get("repositories", {}) or {}
        except Exception as e:
            raise SecretsStoreError(f"Failed to load secrets.yaml: {e}")

    def _write_atomic(self, document: Dict[str, Any]) -> None:
        """Write the whole document: fresh ``mkstemp`` temp in the same directory (created
        0o600, never a predictable name, never following a planted symlink), fsync, chmod
        BEFORE ``os.replace`` (replace carries the temp's mode onto the target). A crash at
        any point leaves either the previous file or the new one - never a truncated
        secrets.yaml, which the earlier ``open(path, "w")`` produced the moment it ran."""
        directory = self.secrets_path.parent
        directory.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=self.secrets_path.name + ".", suffix=".tmp",
                                   dir=str(directory))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                yaml.dump(document, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.secrets_path)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def _save_secrets(self, secrets: Dict[str, Any]) -> None:
        """Save secrets to secrets.yaml (atomic, mode 0o600). Caller holds ``_locked()``."""
        try:
            self._write_atomic({"repositories": secrets})
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

        # Read-modify-write under the lock: two concurrent stores must both survive.
        with self._locked():
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
        with self._locked():
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
