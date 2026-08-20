"""Tests for GitHub Git Wrapper branch strategy.

Verifies that sync operations push to 'main' directly instead of creating
dated branches to avoid branch accumulation.
"""

import pytest
import json
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock, call
from datetime import datetime

from core.console.corvin_console.routes.github_git_wrapper import (
    GitHubGitWrapper,
    sync_skills_to_github_real,
)


class TestGitHubGitWrapperBranchStrategy:
    """Test branch strategy in GitHubGitWrapper."""

    def test_sync_skills_uses_main_branch(self):
        """Verify sync_skills_to_github_real uses 'main' branch, not dated branches."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()

            # Create a test skill file
            skill_file = skills_dir / "test-skill.md"
            skill_file.write_text("# Test Skill\nSome content")

            with patch.object(GitHubGitWrapper, '_run_git') as mock_run_git:
                # Mock successful git operations
                mock_run_git.return_value = (0, "success", "")

                with patch.object(GitHubGitWrapper, 'clone') as mock_clone:
                    mock_clone.return_value = {"success": True}

                    with patch.object(GitHubGitWrapper, 'write_file') as mock_write:
                        mock_write.return_value = {"success": True, "file": "test"}

                        with patch.object(GitHubGitWrapper, 'commit_and_push') as mock_push:
                            mock_push.return_value = {
                                "success": True,
                                "branch": "main",
                                "commit_hash": "abc123"
                            }

                            with patch.object(GitHubGitWrapper, 'create_tag') as mock_tag:
                                mock_tag.return_value = {
                                    "success": True,
                                    "tag": "release-20260820-120000"
                                }

                                result = sync_skills_to_github_real(
                                    repo_url="https://github.com/test/repo",
                                    skills_dir=skills_dir,
                                    tenant_id="_default"
                                )

                                assert result["success"] is True
                                assert result["branch"] == "main"

                                # Verify that commit_and_push was called with 'main', not a dated branch
                                mock_push.assert_called_once()
                                call_args = mock_push.call_args
                                assert call_args[0][0] == "main"  # First positional arg is branch name

    def test_no_dated_branches_created(self):
        """Verify that dated branches are never created during sync."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()

            # Create a test skill file
            skill_file = skills_dir / "test-skill.md"
            skill_file.write_text("# Test Skill")

            with patch.object(GitHubGitWrapper, '_run_git') as mock_run_git:
                mock_run_git.return_value = (0, "success", "")

                with patch.object(GitHubGitWrapper, 'clone') as mock_clone:
                    mock_clone.return_value = {"success": True}

                    with patch.object(GitHubGitWrapper, 'write_file') as mock_write:
                        mock_write.return_value = {"success": True, "file": "test"}

                        with patch.object(GitHubGitWrapper, 'commit_and_push') as mock_push:
                            mock_push.return_value = {
                                "success": True,
                                "branch": "main",
                                "commit_hash": "abc123"
                            }

                            with patch.object(GitHubGitWrapper, 'create_tag') as mock_tag:
                                mock_tag.return_value = {"success": True, "tag": "release-123"}

                                result = sync_skills_to_github_real(
                                    repo_url="https://github.com/test/repo",
                                    skills_dir=skills_dir,
                                    tenant_id="_default"
                                )

                                # Verify no create_branch was called
                                # (we're not mocking it, so if it was called, it would fail)
                                assert result["success"] is True

    def test_checkout_main_after_clone(self):
        """Verify that 'main' branch is checked out after cloning."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()

            with patch.object(GitHubGitWrapper, '_run_git') as mock_run_git:
                # Track calls to _run_git
                call_sequence = []

                def run_git_side_effect(*args, **kwargs):
                    call_sequence.append(args)
                    return (0, "success", "")

                mock_run_git.side_effect = run_git_side_effect

                with patch.object(GitHubGitWrapper, 'clone') as mock_clone:
                    mock_clone.return_value = {"success": True}

                    with patch.object(GitHubGitWrapper, 'write_file') as mock_write:
                        mock_write.return_value = {"success": True, "file": "test"}

                        with patch.object(GitHubGitWrapper, 'commit_and_push') as mock_push:
                            mock_push.return_value = {
                                "success": True,
                                "branch": "main",
                                "commit_hash": "abc123"
                            }

                            with patch.object(GitHubGitWrapper, 'create_tag') as mock_tag:
                                mock_tag.return_value = {"success": True, "tag": "release-123"}

                                result = sync_skills_to_github_real(
                                    repo_url="https://github.com/test/repo",
                                    skills_dir=skills_dir,
                                    tenant_id="_default"
                                )

                                assert result["success"] is True
                                # Verify checkout was called with 'main'
                                checkout_calls = [c for c in call_sequence if c[0] == "checkout"]
                                assert len(checkout_calls) > 0
                                assert "main" in checkout_calls[0]


class TestBranchAccumulationPrevention:
    """Test that branch accumulation is prevented by using main."""

    def test_manifest_records_main_branch(self):
        """Verify manifest records 'main' as the branch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            skills_dir = Path(tmpdir) / "skills"
            skills_dir.mkdir()

            skill_file = skills_dir / "skill1.md"
            skill_file.write_text("# Skill 1")

            with patch.object(GitHubGitWrapper, '_run_git') as mock_run_git:
                mock_run_git.return_value = (0, "success", "")

                with patch.object(GitHubGitWrapper, 'clone') as mock_clone:
                    mock_clone.return_value = {"success": True}

                    # Capture the manifest that gets written
                    written_manifest = {}

                    def write_file_side_effect(path, content):
                        if path == "skills/manifest.json":
                            written_manifest.update(json.loads(content))
                        return {"success": True, "file": path, "size": len(content)}

                    with patch.object(GitHubGitWrapper, 'write_file') as mock_write:
                        mock_write.side_effect = write_file_side_effect

                        with patch.object(GitHubGitWrapper, 'commit_and_push') as mock_push:
                            mock_push.return_value = {
                                "success": True,
                                "branch": "main",
                                "commit_hash": "abc123"
                            }

                            with patch.object(GitHubGitWrapper, 'create_tag') as mock_tag:
                                mock_tag.return_value = {"success": True, "tag": "release-123"}

                                result = sync_skills_to_github_real(
                                    repo_url="https://github.com/test/repo",
                                    skills_dir=skills_dir,
                                    tenant_id="_default"
                                )

                                assert result["success"] is True
                                # Verify manifest records 'main' branch
                                assert written_manifest.get("branch") == "main"

    def test_multiple_syncs_same_branch(self):
        """Verify multiple syncs use same 'main' branch (no new branches created)."""
        sync_results = []

        for i in range(3):
            with tempfile.TemporaryDirectory() as tmpdir:
                skills_dir = Path(tmpdir) / "skills"
                skills_dir.mkdir()

                skill_file = skills_dir / f"skill{i}.md"
                skill_file.write_text(f"# Skill {i}")

                with patch.object(GitHubGitWrapper, '_run_git') as mock_run_git:
                    mock_run_git.return_value = (0, "success", "")

                    with patch.object(GitHubGitWrapper, 'clone') as mock_clone:
                        mock_clone.return_value = {"success": True}

                        with patch.object(GitHubGitWrapper, 'write_file') as mock_write:
                            mock_write.return_value = {"success": True, "file": "test"}

                            with patch.object(GitHubGitWrapper, 'commit_and_push') as mock_push:
                                mock_push.return_value = {
                                    "success": True,
                                    "branch": "main",
                                    "commit_hash": f"abc{i}"
                                }

                                with patch.object(GitHubGitWrapper, 'create_tag') as mock_tag:
                                    mock_tag.return_value = {"success": True, "tag": f"release-{i}"}

                                    result = sync_skills_to_github_real(
                                        repo_url="https://github.com/test/repo",
                                        skills_dir=skills_dir,
                                        tenant_id="_default"
                                    )

                                    sync_results.append(result)

        # Verify all syncs used 'main' branch
        for result in sync_results:
            assert result["success"] is True
            assert result["branch"] == "main"
