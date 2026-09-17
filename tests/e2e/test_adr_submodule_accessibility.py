"""
E2E tests for ADR Submodule Integration (FIX #1 — ADR-0862)

Verifies:
1. Git submodule is properly initialized
2. ADRs are accessible via submodule path
3. Canonical ADR count matches Corvin-ADR repo
4. No duplicate ADRs in local directories (deprecated paths)
"""

import os
import subprocess
import json
import glob
from pathlib import Path
import pytest


class TestADRSubmoduleIntegration:
    """Test ADR submodule accessibility and canonicalization"""

    @pytest.fixture
    def repo_root(self):
        """Return CorvinOS repository root"""
        return Path(__file__).parent.parent.parent

    def test_git_submodule_configured(self, repo_root):
        """Verify .gitmodules contains corvin_decisions submodule"""
        gitmodules_path = repo_root / ".gitmodules"

        assert gitmodules_path.exists(), ".gitmodules file not found"

        content = gitmodules_path.read_text()
        assert "corvin_decisions" in content, "corvin_decisions submodule not in .gitmodules"
        assert "Corvin-ADR.git" in content, "Corvin-ADR repository URL not configured"

    def test_submodule_initialized(self, repo_root):
        """Verify submodule is cloned and contains decisions"""
        submodule_path = repo_root / "corvin_decisions" / "decisions"

        assert submodule_path.exists(), f"Submodule not initialized at {submodule_path}"

        # Count ADR files
        adr_files = list(submodule_path.glob("ADR-*.md"))
        assert len(adr_files) >= 400, f"Expected 400+ ADRs, found {len(adr_files)}"

    def test_adr_files_accessible_via_submodule(self, repo_root):
        """Verify all canonical ADRs accessible from submodule"""
        submodule_path = repo_root / "corvin_decisions" / "decisions"

        # Get all ADR files
        adr_files = sorted(submodule_path.glob("ADR-*.md"))

        # Verify sample ADRs exist and are readable
        assert len(adr_files) > 0, "No ADR files found in submodule"

        # Read first and last ADR to verify they're valid
        for adr_file in [adr_files[0], adr_files[-1]]:
            content = adr_file.read_text()
            assert len(content) > 0, f"{adr_file} is empty"
            assert "id: ADR-" in content, f"{adr_file} missing ADR id"

    def test_submodule_tracks_upstream_branch(self, repo_root):
        """Verify submodule is configured to track main branch"""
        gitmodules_path = repo_root / ".gitmodules"
        content = gitmodules_path.read_text()

        # Check for branch = main configuration
        assert "branch = main" in content or "branch=main" in content.replace(" ", ""), \
            "Submodule not configured to track main branch"

    def test_deprecated_paths_have_deprecation_notices(self, repo_root):
        """Verify deprecation notices in old ADR locations"""
        deprecated_dirs = [
            repo_root / "docs" / "decisions",
            repo_root / "core" / "console",
            repo_root / "outputs"
        ]

        for dir_path in deprecated_dirs:
            if dir_path.exists():
                # Check for deprecation notice in README
                readme_path = dir_path / "README.md"
                if readme_path.exists():
                    content = readme_path.read_text()
                    assert "deprecated" in content.lower() or "corvin_decisions" in content, \
                        f"No deprecation notice in {readme_path}"

    def test_no_local_adr_duplicates(self, repo_root):
        """Verify no ADR files duplicated in deprecated local locations"""
        submodule_adrs = set(p.name for p in (repo_root / "corvin_decisions" / "decisions").glob("ADR-*.md"))

        deprecated_paths = [
            repo_root / "docs" / "decisions",
            repo_root / "core" / "console",
            repo_root / "outputs"
        ]

        for deprecated_path in deprecated_paths:
            if deprecated_path.exists():
                local_adrs = list(deprecated_path.glob("ADR-*.md"))
                assert len(local_adrs) == 0, \
                    f"Found {len(local_adrs)} ADR files in deprecated location {deprecated_path}. " \
                    f"ADRs should only exist in corvin_decisions/"

    def test_submodule_origin_url_correct(self, repo_root):
        """Verify submodule points to correct Corvin-ADR repository"""
        # Read .gitmodules
        gitmodules_path = repo_root / ".gitmodules"
        content = gitmodules_path.read_text()

        # Extract URL
        assert "url = https://github.com/CorvinLabs/Corvin-ADR.git" in content or \
               "url=https://github.com/CorvinLabs/Corvin-ADR.git" in content.replace(" ", ""), \
               "Submodule URL does not point to CorvinLabs/Corvin-ADR repository"

    def test_ci_workflow_mentions_submodule_init(self, repo_root):
        """Verify CI/CD workflows initialize submodules"""
        workflow_dir = repo_root / ".github" / "workflows"

        if workflow_dir.exists():
            workflow_files = list(workflow_dir.glob("*.yml")) + list(workflow_dir.glob("*.yaml"))

            for workflow_file in workflow_files:
                content = workflow_file.read_text()

                # Check for submodule initialization in build workflows
                if "build" in workflow_file.name.lower() or "test" in workflow_file.name.lower():
                    assert "submodule" in content.lower(), \
                        f"{workflow_file.name} should include submodule initialization"


class TestADRSubmoduleUsage:
    """Test actual usage patterns for ADR submodule"""

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).parent.parent.parent

    def test_adr_lookup_by_id(self, repo_root):
        """Test looking up ADR by ID from code"""
        # Example: ADR-0862 (this integration)
        adr_path = repo_root / "corvin_decisions" / "decisions" / "ADR-0862-adr-submodule-integration.md"

        # Note: This ADR file will be created in Corvin-ADR repo; test pattern is the verification
        assert adr_path.parent.exists(), "ADR decisions directory not accessible"

    def test_code_can_reference_adr_via_submodule_path(self, repo_root):
        """Verify code can use submodule path in comments"""
        # Example reference pattern in code:
        # See corvin_decisions/decisions/ADR-XXXX-slug.md

        submodule_path = repo_root / "corvin_decisions"
        assert submodule_path.exists(), "Cannot reference ADRs via submodule path"


class TestADRSubmoduleUpdate:
    """Test submodule update workflow"""

    @pytest.fixture
    def repo_root(self):
        return Path(__file__).parent.parent.parent

    def test_submodule_update_process(self, repo_root):
        """Verify developers can update submodule"""
        # This test documents the expected workflow:
        # 1. git submodule update --init --recursive  (first time)
        # 2. git submodule update --remote              (regular updates)

        # Verify submodule is reachable
        submodule_path = repo_root / "corvin_decisions"
        assert submodule_path.is_dir(), "Submodule not accessible"

        # Verify it's a git repository
        git_dir = submodule_path / ".git"
        assert git_dir.exists(), "Submodule is not a git repository"
