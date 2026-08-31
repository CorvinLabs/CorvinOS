"""Custom GitHub Repository Management (ADR-0450/0451).

Handles:
- Adding custom GitHub repository URLs
- Validating repository URLs
- Caching repository metadata
- Merging custom registries with Corvin marketplace
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Any
import re
import json
from pathlib import Path

from core.marketplace.auth import (
    get_secrets_store,
    TokenNotFoundError,
    SecretsStoreError,
)


@dataclass
class CustomRepository:
    """A custom GitHub repository entry."""
    repo_url: str
    tenant_id: str
    status: str  # "healthy", "auth_error", "rate_limited", "timeout"
    extension_count: int = 0
    error_message: Optional[str] = None
    last_checked: Optional[str] = None  # ISO timestamp
    cached_extensions: Optional[List[Dict[str, Any]]] = None
    # A disabled repository stays registered (with its token) but contributes no
    # extensions to discovery — the console's per-repo toggle. Absent from an
    # older cache file it reads back as True, so existing entries keep working.
    enabled: bool = True


class RepositoryValidationError(Exception):
    """Raised when repository URL is invalid."""
    pass


class RepositoryManager:
    """Manages custom GitHub repositories for a tenant."""

    # Valid GitHub repo URL pattern
    GITHUB_REPO_PATTERN = re.compile(
        r"^https://github\.com/[a-zA-Z0-9_-]+/[a-zA-Z0-9_.-]+(?:\.git)?/?$"
    )

    # Cache TTL: 30 seconds for testing, 1 hour in production
    CACHE_TTL = timedelta(seconds=30)

    def __init__(self, tenant_id: str, cache_dir: Optional[Path] = None):
        """Initialize repository manager for a tenant.

        Args:
            tenant_id: Tenant identifier for isolation
            cache_dir: Directory for caching metadata (default: ~/.corvin/cache/marketplace)
        """
        self.tenant_id = tenant_id
        self.secrets_store = get_secrets_store()

        if cache_dir is None:
            from core.orchestration.quota_gate import corvin_home
            cache_dir = corvin_home() / "cache" / "marketplace" / tenant_id

        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    def validate_repository_url(self, repo_url: str) -> None:
        """Validate GitHub repository URL format.

        Args:
            repo_url: URL to validate

        Raises:
            RepositoryValidationError: if URL is invalid
        """
        repo_url = repo_url.strip()

        if not self.GITHUB_REPO_PATTERN.match(repo_url):
            raise RepositoryValidationError(
                f"Invalid GitHub repository URL: {repo_url}. "
                f"Expected format: https://github.com/owner/repo or https://github.com/owner/repo.git"
            )

    def add_repository(
        self,
        repo_url: str,
        token: Optional[str] = None,
    ) -> CustomRepository:
        """Add a custom repository.

        Args:
            repo_url: GitHub repository URL
            token: Optional GitHub PAT for private repos

        Returns:
            CustomRepository entry with initial status

        Raises:
            RepositoryValidationError: if URL is invalid
            SecretsStoreError: if token storage fails
        """
        self.validate_repository_url(repo_url)

        # Normalize URL (remove .git suffix if present)
        repo_url = repo_url.rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]

        # Check for duplicates
        if self._repository_exists(repo_url):
            raise RepositoryValidationError(
                f"Repository {repo_url} is already registered for this tenant"
            )

        # Store token if provided
        if token:
            self.secrets_store.store_token(repo_url, token, self.tenant_id)

        # Create entry with initial status
        repo = CustomRepository(
            repo_url=repo_url,
            tenant_id=self.tenant_id,
            status="pending",  # Will be validated on next refresh
            extension_count=0,
            error_message=None,
            last_checked=None,
        )

        # Store metadata
        self._save_repository_metadata(repo)

        return repo

    def remove_repository(self, repo_url: str) -> None:
        """Remove a repository and its stored token.

        Args:
            repo_url: Repository URL to remove

        Raises:
            RepositoryValidationError: if the repository is not registered for
                this tenant — deleting by path alone would let one tenant drop
                another's record whenever they share a cache directory.
        """
        repo_url = self._normalize_url(repo_url)
        self.get_repository(repo_url)  # tenant-checked existence probe

        # Remove token if stored
        try:
            self.secrets_store.delete_token(repo_url, self.tenant_id)
        except TokenNotFoundError:
            pass  # Token not stored is OK

        # Remove metadata
        cache_file = self._get_cache_file(repo_url)
        if cache_file.exists():
            cache_file.unlink()

    def get_repository(self, repo_url: str) -> CustomRepository:
        """Get repository metadata.

        Args:
            repo_url: Repository URL

        Returns:
            CustomRepository with current metadata

        Raises:
            RepositoryNotFoundError: if not registered
        """
        repo_url = self._normalize_url(repo_url)
        cache_file = self._get_cache_file(repo_url)

        if not cache_file.exists():
            raise RepositoryValidationError(
                f"Repository {repo_url} not registered for tenant {self.tenant_id}"
            )

        # Load from cache
        with open(cache_file, "r") as f:
            data = json.load(f)

        # A record belonging to another tenant reads as "not registered" — the
        # same answer an unknown URL gets, so nothing leaks through the error.
        if data.get("tenant_id") != self.tenant_id:
            raise RepositoryValidationError(
                f"Repository {repo_url} not registered for tenant {self.tenant_id}"
            )

        return CustomRepository(
            repo_url=data["repo_url"],
            tenant_id=data["tenant_id"],
            status=data["status"],
            extension_count=data.get("extension_count", 0),
            error_message=data.get("error_message"),
            last_checked=data.get("last_checked"),
            cached_extensions=data.get("cached_extensions"),
            enabled=data.get("enabled", True),
        )

    def list_repositories(self) -> List[CustomRepository]:
        """List all repositories for this tenant.

        Returns:
            List of CustomRepository entries
        """
        repos = []

        for cache_file in self.cache_dir.glob("*.json"):
            try:
                with open(cache_file, "r") as f:
                    data = json.load(f)

                # Isolation is enforced on the RECORD, not only on the path.
                # The default cache_dir already ends in the tenant id, but an
                # explicit shared directory (and a future flat layout) would
                # otherwise hand one tenant another's repositories.
                if data.get("tenant_id") != self.tenant_id:
                    continue

                repos.append(CustomRepository(
                    repo_url=data["repo_url"],
                    tenant_id=data["tenant_id"],
                    status=data["status"],
                    extension_count=data.get("extension_count", 0),
                    error_message=data.get("error_message"),
                    last_checked=data.get("last_checked"),
                    cached_extensions=data.get("cached_extensions"),
                    enabled=data.get("enabled", True),
                ))
            except Exception:
                pass  # Skip corrupted cache files

        return repos

    def _normalize_url(self, repo_url: str) -> str:
        """Normalize repository URL (remove .git, trailing slashes)."""
        repo_url = repo_url.strip().rstrip("/")
        if repo_url.endswith(".git"):
            repo_url = repo_url[:-4]
        return repo_url

    def _repository_exists(self, repo_url: str) -> bool:
        """Check if repository is already registered."""
        repo_url = self._normalize_url(repo_url)
        cache_file = self._get_cache_file(repo_url)
        return cache_file.exists()

    def _get_cache_file(self, repo_url: str) -> Path:
        """Get cache file path for a repository."""
        # Use repo owner/name as cache filename
        parts = repo_url.rstrip("/").split("/")
        if len(parts) >= 2:
            owner = parts[-2]
            repo_name = parts[-1]
            filename = f"{owner}_{repo_name}.json"
        else:
            filename = repo_url.replace("/", "_").replace(":", "_") + ".json"

        return self.cache_dir / filename

    def _save_repository_metadata(self, repo: CustomRepository) -> None:
        """Save repository metadata to cache."""
        cache_file = self._get_cache_file(repo.repo_url)

        data = {
            "repo_url": repo.repo_url,
            "tenant_id": repo.tenant_id,
            "status": repo.status,
            "extension_count": repo.extension_count,
            "error_message": repo.error_message,
            "last_checked": repo.last_checked,
            "cached_extensions": repo.cached_extensions or [],
            "enabled": repo.enabled,
        }

        with open(cache_file, "w") as f:
            json.dump(data, f)

    def update_repository_status(
        self,
        repo_url: str,
        status: str,
        extension_count: int = 0,
        error_message: Optional[str] = None,
        extensions: Optional[List[Dict[str, Any]]] = None,
    ) -> CustomRepository:
        """Update repository status after validation/refresh.

        Args:
            repo_url: Repository URL
            status: New status (healthy, auth_error, rate_limited, timeout)
            extension_count: Number of extensions found
            error_message: Error message if status is not healthy
            extensions: Cached extension list

        Returns:
            Updated CustomRepository
        """
        repo = self.get_repository(repo_url)
        repo.status = status
        repo.extension_count = extension_count
        repo.error_message = error_message
        repo.last_checked = datetime.now(timezone.utc).isoformat()
        repo.cached_extensions = extensions or []

        self._save_repository_metadata(repo)
        return repo

    def set_enabled(self, repo_url: str, enabled: bool) -> CustomRepository:
        """Enable or disable a repository without unregistering it.

        Args:
            repo_url: Repository URL
            enabled: New state

        Returns:
            Updated CustomRepository

        Raises:
            RepositoryValidationError: if the repository is not registered
        """
        repo = self.get_repository(repo_url)
        repo.enabled = enabled
        self._save_repository_metadata(repo)
        return repo
