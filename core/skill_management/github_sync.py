"""GitHub API wrapper for skill synchronization."""

import subprocess
import json
from pathlib import Path
from typing import Optional, Dict, List
from dataclasses import dataclass
from datetime import datetime

from core.skill_management.tenant_validator import validate_tenant_id  # TENANT-002


@dataclass
class GitPushResult:
    """Result of pushing to GitHub."""
    success: bool
    commit_sha: Optional[str] = None
    repo_url: Optional[str] = None
    error: Optional[str] = None
    files_pushed: int = 0


@dataclass
class GitPullResult:
    """Result of pulling from GitHub."""
    success: bool
    commit_sha: Optional[str] = None
    files_pulled: int = 0
    conflicts: List[str] = None
    error: Optional[str] = None

    def __post_init__(self):
        if self.conflicts is None:
            self.conflicts = []


class GitClient:
    """Wrapper around git CLI for GitHub operations."""

    def __init__(self, repo_url: str, branch: str = "main", tenant_id: str = "_default"):
        """Initialize GitClient with tenant isolation (CVE-TENANT-001 fix).

        Args:
            repo_url: GitHub repository URL
            branch: Git branch (default: "main")
            tenant_id: Tenant identifier for path scoping (REQUIRED, no bypass allowed)

        Note: local_repo_path parameter REMOVED (CVE-TENANT-001 fix).
              Tenant-scoped path is now MANDATORY, no override allowed for security.
        """
        # TENANT-002 FIX: Validate tenant_id before using in path construction
        validate_tenant_id(tenant_id)

        self.repo_url = repo_url
        self.branch = branch
        self.tenant_id = tenant_id

        # CVE-TENANT-001 FIX: Tenant-scope is MANDATORY, no bypass via local_repo_path parameter
        self.local_repo_path = (
            Path.home() / ".corvin" / "tenants" / tenant_id / "git_sync_repo"
        )

    def clone_or_pull(self) -> bool:
        """Clone repo if needed, or pull latest."""
        if not self.local_repo_path.exists():
            # Clone
            try:
                subprocess.run(
                    ["git", "clone", "--branch", self.branch, self.repo_url, str(self.local_repo_path)],
                    check=True,
                    capture_output=True,
                    timeout=30
                )
                return True
            except subprocess.CalledProcessError as e:
                print(f"Clone failed: {e.stderr.decode()}")
                return False
        else:
            # Pull
            try:
                subprocess.run(
                    ["git", "-C", str(self.local_repo_path), "pull", "origin", self.branch],
                    check=True,
                    capture_output=True,
                    timeout=30
                )
                return True
            except subprocess.CalledProcessError as e:
                print(f"Pull failed: {e.stderr.decode()}")
                return False

    def push(self, message: str, files: Optional[List[Path]] = None) -> GitPushResult:
        """Stage files, commit, and push to GitHub."""
        try:
            # Stage files
            if files:
                for file_path in files:
                    subprocess.run(
                        ["git", "-C", str(self.local_repo_path), "add", str(file_path)],
                        check=True,
                        capture_output=True
                    )
            else:
                # Stage all
                subprocess.run(
                    ["git", "-C", str(self.local_repo_path), "add", "-A"],
                    check=True,
                    capture_output=True
                )

            # Check if anything to commit
            status = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "status", "--porcelain"],
                capture_output=True,
                text=True
            )
            if not status.stdout.strip():
                return GitPushResult(success=True, error="Nothing to commit")

            # Commit
            result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "commit", "-m", message],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return GitPushResult(success=False, error=f"Commit failed: {result.stderr}")

            # Get commit SHA
            sha_result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True
            )
            commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

            # Push
            push_result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "push", "origin", self.branch],
                capture_output=True,
                text=True,
                timeout=30
            )

            if push_result.returncode != 0:
                return GitPushResult(
                    success=False,
                    commit_sha=commit_sha,
                    error=f"Push failed: {push_result.stderr}"
                )

            return GitPushResult(
                success=True,
                commit_sha=commit_sha,
                repo_url=self.repo_url,
                files_pushed=len(files) if files else 0
            )

        except subprocess.TimeoutExpired:
            return GitPushResult(success=False, error="Git operation timed out")
        except Exception as e:
            return GitPushResult(success=False, error=str(e))

    def pull(self) -> GitPullResult:
        """Pull latest from GitHub."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "pull", "origin", self.branch],
                capture_output=True,
                text=True,
                timeout=30
            )

            if result.returncode != 0:
                # Check for conflicts
                status = subprocess.run(
                    ["git", "-C", str(self.local_repo_path), "status", "--porcelain"],
                    capture_output=True,
                    text=True
                )
                conflicts = [line[3:] for line in status.stdout.split("\n") if line.startswith("UU")]

                return GitPullResult(
                    success=False,
                    conflicts=conflicts,
                    error=f"Pull failed: {result.stderr}"
                )

            # Get latest commit
            sha_result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True
            )
            commit_sha = sha_result.stdout.strip() if sha_result.returncode == 0 else None

            return GitPullResult(success=True, commit_sha=commit_sha)

        except subprocess.TimeoutExpired:
            return GitPullResult(success=False, error="Git operation timed out")
        except Exception as e:
            return GitPullResult(success=False, error=str(e))

    def get_current_sha(self) -> Optional[str]:
        """Get current commit SHA."""
        try:
            result = subprocess.run(
                ["git", "-C", str(self.local_repo_path), "rev-parse", "HEAD"],
                capture_output=True,
                text=True
            )
            return result.stdout.strip() if result.returncode == 0 else None
        except:
            return None

    def cleanup(self):
        """Remove local clone."""
        import shutil
        if self.local_repo_path.exists():
            shutil.rmtree(self.local_repo_path)
