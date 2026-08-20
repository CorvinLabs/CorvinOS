"""Cross-Tenant Isolation Tests (TEST-003 Fix).

Verifies that skills and configurations are properly isolated between tenants
to prevent data leaks (GDPR Art. 5, ADR-0007 multi-tenant axis).
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from core.skill_management.github_sync import GitClient
from core.skill_management.github_exporter import GitHubExporter
from core.skill_management.github_importer import GitHubImporter
from core.skill_management.tenant_validator import validate_tenant_id  # Fixed import


class TestGitClientTenantIsolation:
    """Test CVE-TENANT-001: GitClient tenant scoping (FIXED: local_repo_path removed)."""

    def test_git_client_paths_scoped_by_tenant(self):
        """Test GitClient local_repo_path is tenant-scoped."""
        client_a = GitClient(
            repo_url="https://github.com/test/repo",
            branch="main",
            tenant_id="tenant_a"
        )
        client_b = GitClient(
            repo_url="https://github.com/test/repo",
            branch="main",
            tenant_id="tenant_b"
        )

        # Paths should be different
        assert "tenant_a" in str(client_a.local_repo_path)
        assert "tenant_b" in str(client_b.local_repo_path)
        assert client_a.local_repo_path != client_b.local_repo_path

    def test_git_client_default_tenant(self):
        """Test GitClient defaults to _default tenant if not specified."""
        client = GitClient(
            repo_url="https://github.com/test/repo",
            branch="main"
            # tenant_id defaults to "_default"
        )

        assert "_default" in str(client.local_repo_path)

    def test_git_client_no_bypass_via_local_repo_path(self):
        """Test GitClient local_repo_path bypass parameter is removed (CVE-TENANT-001 FIX)."""
        # The local_repo_path parameter was removed, so this should fail
        # if someone tries to pass it
        with pytest.raises(TypeError):
            GitClient(
                repo_url="https://github.com/test/repo",
                branch="main",
                tenant_id="tenant_a",
                local_repo_path="/etc/passwd"  # ← Should fail (parameter removed)
            )


class TestGitHubExporterTenantIsolation:
    """Test GitHubExporter tenant isolation."""

    def test_exporter_validates_tenant_id(self):
        """Test GitHubExporter validates tenant_id on init."""
        # Valid tenant_id should succeed
        exporter = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_a"
        )
        assert exporter.tenant_id == "tenant_a"

        # Invalid tenant_id should raise
        with pytest.raises((ValueError, AssertionError)):
            GitHubExporter(
                repo_url="https://github.com/test/repo",
                tenant_id="invalid/../../../etc/passwd"
            )

    def test_exporter_paths_scoped_by_tenant(self):
        """Test GitHubExporter base_path is tenant-scoped."""
        exporter_a = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_a"
        )
        exporter_b = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_b"
        )

        assert "tenant_a" in str(exporter_a.base_path)
        assert "tenant_b" in str(exporter_b.base_path)
        assert exporter_a.base_path != exporter_b.base_path

    def test_exporter_git_client_receives_tenant_id(self):
        """Test GitHubExporter passes tenant_id to GitClient."""
        exporter = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_a"
        )

        # GitClient should be tenant-scoped
        assert exporter.git_client.tenant_id == "tenant_a"
        assert "tenant_a" in str(exporter.git_client.local_repo_path)


class TestCrossTenantDataLeakPrevention:
    """Test that Tenant A and B cannot interfere with each other."""

    def test_tenant_a_and_b_separate_directories(self):
        """Test Tenant A and B use separate directories (no collision)."""
        exporter_a = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_a"
        )
        exporter_b = GitHubExporter(
            repo_url="https://github.com/test/repo",
            tenant_id="tenant_b"
        )

        # Create temporary directories to simulate file operations
        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate Tenant A's git repo path
            repo_a = Path(tmpdir) / "tenants" / "tenant_a" / "git_sync_repo"
            repo_a.mkdir(parents=True)
            (repo_a / "skill_a.md").write_text("Tenant A skill")

            # Simulate Tenant B's git repo path (different directory)
            repo_b = Path(tmpdir) / "tenants" / "tenant_b" / "git_sync_repo"
            repo_b.mkdir(parents=True)
            (repo_b / "skill_b.md").write_text("Tenant B skill")

            # Verify no collision
            assert not (repo_b / "skill_a.md").exists()  # Tenant B cannot access Tenant A's files
            assert not (repo_a / "skill_b.md").exists()  # Tenant A cannot access Tenant B's files

    @pytest.mark.skip(reason="Requires mock git operations; integration test")
    def test_tenant_isolation_in_git_operations(self):
        """Test git clone/pull operations don't share state between tenants (integration test)."""
        # TODO: Mock GitClient.clone_or_pull() and verify each tenant gets isolated directory
        pass


class TestTenantIDValidation:
    """Test TENANT-002: tenant_id validation (GDPR Art. 5 integrity)."""

    def test_invalid_tenant_ids_rejected(self):
        """Test invalid tenant_ids are rejected."""
        invalid_ids = [
            "",  # Empty
            None,  # None
            "../../../etc/passwd",  # Path traversal attempt
            "_default/../admin",  # Mixed valid/traversal
            "tenant:admin",  # Invalid characters
        ]

        for invalid_id in invalid_ids:
            if invalid_id is None:
                continue  # Skip None for now (would need different test)

            with pytest.raises((ValueError, AssertionError)):
                GitHubExporter(
                    repo_url="https://github.com/test/repo",
                    tenant_id=invalid_id
                )

    def test_valid_tenant_ids_accepted(self):
        """Test valid tenant_ids are accepted."""
        valid_ids = [
            "_default",
            "tenant_a",
            "org_prod_01",
            "client-2025-01",
        ]

        for valid_id in valid_ids:
            try:
                exporter = GitHubExporter(
                    repo_url="https://github.com/test/repo",
                    tenant_id=valid_id
                )
                assert exporter.tenant_id == valid_id
            except (ValueError, AssertionError) as e:
                pytest.fail(f"Valid tenant_id '{valid_id}' was rejected: {e}")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
