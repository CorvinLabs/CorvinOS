"""Unit tests for Custom Repository Management (ADR-0450/0451).

Tests cover:
1. Repository URL validation
2. Add/remove/list operations
3. Multi-tenant isolation
4. Cache loading/saving
5. Token integration
"""

import pytest
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.marketplace.custom_repositories import (
    RepositoryManager,
    CustomRepository,
    RepositoryValidationError,
)
from core.marketplace.auth import SecretsStoreError


class TestRepositoryURLValidation:
    """Test GitHub repository URL validation."""

    @pytest.fixture
    def manager(self, tmp_path):
        """Create RepositoryManager with temp cache."""
        return RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")

    def test_valid_https_url(self, manager):
        """Valid HTTPS URL should pass validation."""
        manager.validate_repository_url("https://github.com/owner/repo")

    def test_valid_https_url_with_git_suffix(self, manager):
        """Valid HTTPS URL with .git suffix should pass."""
        manager.validate_repository_url("https://github.com/owner/repo.git")

    def test_valid_https_url_with_trailing_slash(self, manager):
        """Valid HTTPS URL with trailing slash should pass."""
        manager.validate_repository_url("https://github.com/owner/repo/")

    def test_invalid_url_wrong_host(self, manager):
        """URL with wrong host should fail validation."""
        with pytest.raises(RepositoryValidationError):
            manager.validate_repository_url("https://gitlab.com/owner/repo")

    def test_invalid_url_http_not_https(self, manager):
        """HTTP URL (not HTTPS) should fail validation."""
        with pytest.raises(RepositoryValidationError):
            manager.validate_repository_url("http://github.com/owner/repo")

    def test_invalid_url_missing_owner_or_repo(self, manager):
        """URL missing owner or repo name should fail."""
        with pytest.raises(RepositoryValidationError):
            manager.validate_repository_url("https://github.com/owner")

    def test_invalid_url_empty_string(self, manager):
        """Empty URL should fail validation."""
        with pytest.raises(RepositoryValidationError):
            manager.validate_repository_url("")


class TestRepositoryOperations:
    """Test add/remove/list repository operations."""

    @pytest.fixture
    def manager(self, tmp_path, monkeypatch):
        """Create RepositoryManager with temp cache and mocked secrets."""
        # Mock secrets store
        mock_store = MagicMock()
        mock_store.token_exists.return_value = False

        with patch("core.marketplace.custom_repositories.get_secrets_store", return_value=mock_store):
            return RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")

    def test_add_repository_creates_cache_entry(self, manager):
        """Adding repository should create cache file."""
        repo_url = "https://github.com/owner/repo"

        repo = manager.add_repository(repo_url)

        assert repo.repo_url == repo_url
        assert repo.tenant_id == "test-tenant"
        assert repo.status == "pending"
        assert repo.extension_count == 0

        # Verify cache file exists
        cache_file = manager._get_cache_file(repo_url)
        assert cache_file.exists()

    def test_add_repository_duplicate_fails(self, manager):
        """Adding same repository twice should fail."""
        repo_url = "https://github.com/owner/repo"

        manager.add_repository(repo_url)

        with pytest.raises(RepositoryValidationError, match="already registered"):
            manager.add_repository(repo_url)

    def test_add_repository_normalizes_url(self, manager):
        """Add repository should normalize URL (remove .git, trailing /)."""
        repo_url_with_git = "https://github.com/owner/repo.git"
        repo_url_normalized = "https://github.com/owner/repo"

        repo = manager.add_repository(repo_url_with_git)

        assert repo.repo_url == repo_url_normalized

    def test_remove_repository_deletes_cache(self, manager):
        """Removing repository should delete cache file."""
        repo_url = "https://github.com/owner/repo"

        manager.add_repository(repo_url)
        cache_file = manager._get_cache_file(repo_url)
        assert cache_file.exists()

        manager.remove_repository(repo_url)
        assert not cache_file.exists()

    def test_list_repositories_empty(self, manager):
        """List repositories on empty cache should return empty list."""
        repos = manager.list_repositories()
        assert repos == []

    def test_list_repositories_multiple(self, manager):
        """List repositories should return all repositories for tenant."""
        urls = [
            "https://github.com/owner1/repo1",
            "https://github.com/owner2/repo2",
            "https://github.com/owner3/repo3",
        ]

        for url in urls:
            manager.add_repository(url)

        repos = manager.list_repositories()
        assert len(repos) == 3
        assert {r.repo_url for r in repos} == set(urls)

    def test_get_repository_not_found(self, manager):
        """Get nonexistent repository should raise RepositoryValidationError."""
        with pytest.raises(RepositoryValidationError, match="not registered"):
            manager.get_repository("https://github.com/owner/unknown")

    def test_get_repository_success(self, manager):
        """Get existing repository should return metadata."""
        repo_url = "https://github.com/owner/repo"
        manager.add_repository(repo_url)

        repo = manager.get_repository(repo_url)
        assert repo.repo_url == repo_url
        assert repo.status == "pending"


class TestMultiTenantIsolation:
    """Test that repositories are isolated per tenant."""

    @pytest.fixture
    def tmp_cache_dir(self, tmp_path):
        """Shared temp cache directory."""
        return tmp_path / "cache"

    def test_repositories_isolated_by_tenant(self, tmp_cache_dir):
        """Repositories from tenant A should not appear in tenant B's list."""
        manager_a = RepositoryManager("tenant-a", cache_dir=tmp_cache_dir)
        manager_b = RepositoryManager("tenant-b", cache_dir=tmp_cache_dir)

        url_a = "https://github.com/owner/repo-a"
        url_b = "https://github.com/owner/repo-b"

        # Mock secrets store
        with patch("core.marketplace.custom_repositories.get_secrets_store"):
            manager_a.add_repository(url_a)
            manager_b.add_repository(url_b)

        # Each tenant sees only their repositories
        repos_a = manager_a.list_repositories()
        repos_b = manager_b.list_repositories()

        assert len(repos_a) == 1
        assert repos_a[0].repo_url == url_a
        assert len(repos_b) == 1
        assert repos_b[0].repo_url == url_b


class TestCacheOperations:
    """Test cache loading and saving."""

    @pytest.fixture
    def manager(self, tmp_path):
        return RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")

    def test_cache_persistence_across_instances(self, tmp_path):
        """Repository data should persist across RepositoryManager instances."""
        repo_url = "https://github.com/owner/repo"

        # Create repo in first instance
        with patch("core.marketplace.custom_repositories.get_secrets_store"):
            manager1 = RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")
            manager1.add_repository(repo_url)

        # Load in second instance
        manager2 = RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")
        repos = manager2.list_repositories()

        assert len(repos) == 1
        assert repos[0].repo_url == repo_url

    def test_cache_file_json_format(self, manager):
        """Cache files should be valid JSON."""
        repo_url = "https://github.com/owner/repo"
        manager.add_repository(repo_url)

        cache_file = manager._get_cache_file(repo_url)
        with open(cache_file, "r") as f:
            data = json.load(f)

        assert data["repo_url"] == repo_url
        assert data["tenant_id"] == "test-tenant"
        assert "status" in data


class TestStatusUpdates:
    """Test repository status update."""

    @pytest.fixture
    def manager(self, tmp_path):
        return RepositoryManager("test-tenant", cache_dir=tmp_path / "cache")

    def test_update_repository_status(self, manager):
        """Update status should persist changes."""
        repo_url = "https://github.com/owner/repo"
        manager.add_repository(repo_url)

        updated = manager.update_repository_status(
            repo_url,
            status="healthy",
            extension_count=5,
            extensions=[{"name": "ext1"}, {"name": "ext2"}],
        )

        assert updated.status == "healthy"
        assert updated.extension_count == 5
        assert len(updated.cached_extensions) == 2
        assert updated.last_checked is not None

        # Verify persistence
        repo = manager.get_repository(repo_url)
        assert repo.status == "healthy"
        assert repo.extension_count == 5
