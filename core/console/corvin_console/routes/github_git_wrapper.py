"""Git CLI wrapper for GitHub repository operations.

Handles:
- Repository cloning
- Branch creation/switching
- File commits and pushes
- Tag creation (releases)
- Error handling with audit trail
"""

import subprocess
import json
import tempfile
import shutil
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional, Tuple
import logging
import re

logger = logging.getLogger(__name__)


class GitHubGitWrapper:
    """Wrap git CLI for safe GitHub repository operations."""

    def __init__(self, repo_url: str, tenant_id: str = "_default", max_retries: int = 3):
        self.repo_url = repo_url
        self.tenant_id = tenant_id
        self.work_dir: Optional[Path] = None
        self.tenant_path = Path.home() / '.corvin' / 'tenants' / tenant_id
        self.max_retries = max_retries
        self.last_error: Optional[str] = None

    def __enter__(self):
        """Context manager: create temporary work directory."""
        self.work_dir = Path(tempfile.mkdtemp(prefix=f"github-sync-{self.tenant_id}-"))
        logger.info(f"Created work directory: {self.work_dir}")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager: cleanup work directory."""
        if self.work_dir and self.work_dir.exists():
            shutil.rmtree(self.work_dir)
            logger.info(f"Cleaned up work directory: {self.work_dir}")

    def _run_git(self, *args: str, cwd: Optional[Path] = None, retry: int = 0) -> Tuple[int, str, str]:
        """Run git command safely, return (returncode, stdout, stderr).

        With automatic retry on transient failures (network, timeout).
        """
        if cwd is None:
            cwd = self.work_dir

        cmd = ["git"] + list(args)
        logger.debug(f"Running: {' '.join(cmd)} in {cwd} (attempt {retry+1}/{self.max_retries})")

        try:
            result = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                timeout=30
            )

            # Success
            if result.returncode == 0:
                return result.returncode, result.stdout, result.stderr

            # Transient error - retry
            if retry < self.max_retries - 1 and self._is_transient_error(result.stderr):
                logger.warning(f"Transient error, retrying ({retry+1}/{self.max_retries}): {result.stderr[:100]}")
                import time
                time.sleep(1 + retry)  # Backoff: 1s, 2s, 3s
                return self._run_git(*args, cwd=cwd, retry=retry+1)

            return result.returncode, result.stdout, result.stderr

        except subprocess.TimeoutExpired as e:
            if retry < self.max_retries - 1:
                logger.warning(f"Timeout, retrying ({retry+1}/{self.max_retries})")
                import time
                time.sleep(1 + retry)
                return self._run_git(*args, cwd=cwd, retry=retry+1)
            logger.error(f"Git command timed out after {self.max_retries} attempts: {' '.join(cmd)}")
            self.last_error = f"Timeout: {str(e)}"
            return 1, "", str(e)
        except Exception as e:
            logger.error(f"Git command failed: {e}")
            self.last_error = str(e)
            return 1, "", str(e)

    @staticmethod
    def _is_transient_error(stderr: str) -> bool:
        """Check if error is transient (network, temporary lock) vs permanent."""
        transient_patterns = [
            "Connection refused",
            "Connection timed out",
            "Temporary failure",
            "unable to access",
            "Could not resolve host",
            "Operation timed out",
            "resource temporarily unavailable",
        ]
        stderr_lower = stderr.lower()
        return any(pattern.lower() in stderr_lower for pattern in transient_patterns)

    def clone(self) -> Dict[str, Any]:
        """Clone the GitHub repository."""
        if not self.work_dir:
            return {"success": False, "error": "Work directory not initialized"}

        returncode, stdout, stderr = self._run_git(
            "clone", self.repo_url, str(self.work_dir),
            cwd=Path("/tmp")
        )

        if returncode != 0:
            return {
                "success": False,
                "error": f"Failed to clone repository: {stderr}"
            }

        logger.info(f"Repository cloned successfully: {self.repo_url}")
        return {
            "success": True,
            "repo_url": self.repo_url,
            "work_dir": str(self.work_dir),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def create_branch(self, branch_name: str) -> Dict[str, Any]:
        """Create and switch to a new branch."""
        if not self.work_dir:
            return {"success": False, "error": "Work directory not initialized"}

        # Create branch
        returncode, _, stderr = self._run_git("checkout", "-b", branch_name)
        if returncode != 0:
            return {
                "success": False,
                "error": f"Failed to create branch: {stderr}"
            }

        logger.info(f"Branch created: {branch_name}")
        return {
            "success": True,
            "branch": branch_name,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def write_file(self, relative_path: str, content: str) -> Dict[str, Any]:
        """Write content to a file in the repository."""
        if not self.work_dir:
            return {"success": False, "error": "Work directory not initialized"}

        file_path = self.work_dir / relative_path
        file_path.parent.mkdir(parents=True, exist_ok=True)

        try:
            file_path.write_text(content)
            logger.info(f"File written: {relative_path}")
            return {
                "success": True,
                "file": relative_path,
                "size": len(content),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }
        except Exception as e:
            return {"success": False, "error": f"Failed to write file: {e}"}

    def commit_and_push(
        self,
        branch: str,
        message: str,
        user_email: str = "corvin@local.dev",
        user_name: str = "Corvin Sync"
    ) -> Dict[str, Any]:
        """Stage all changes, commit, and push to remote."""
        if not self.work_dir:
            return {"success": False, "error": "Work directory not initialized"}

        # Configure git user
        self._run_git("config", "user.email", user_email)
        self._run_git("config", "user.name", user_name)

        # Stage all changes
        returncode, _, stderr = self._run_git("add", "-A")
        if returncode != 0:
            return {"success": False, "error": f"Failed to stage changes: {stderr}"}

        # Check if there are changes to commit
        returncode, status, _ = self._run_git("status", "--porcelain")
        if returncode != 0 or not status.strip():
            logger.info("No changes to commit")
            return {
                "success": True,
                "message": "No changes to commit",
                "commit_hash": None,
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

        # Commit
        returncode, stdout, stderr = self._run_git("commit", "-m", message)
        if returncode != 0:
            return {"success": False, "error": f"Failed to commit: {stderr}"}

        # Extract commit hash
        commit_hash = stdout.split()[2] if len(stdout.split()) > 2 else "unknown"

        # Push to remote
        returncode, _, stderr = self._run_git("push", "-u", "origin", branch)
        if returncode != 0:
            return {
                "success": False,
                "error": f"Failed to push to remote: {stderr}"
            }

        logger.info(f"Committed and pushed: {commit_hash}")
        return {
            "success": True,
            "branch": branch,
            "commit_hash": commit_hash,
            "message": message,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def create_tag(self, tag_name: str, message: str = "") -> Dict[str, Any]:
        """Create an annotated tag (release tag)."""
        if not self.work_dir:
            return {"success": False, "error": "Work directory not initialized"}

        cmd = ["tag", "-a", tag_name]
        if message:
            cmd.extend(["-m", message])

        returncode, _, stderr = self._run_git(*cmd)
        if returncode != 0:
            return {"success": False, "error": f"Failed to create tag: {stderr}"}

        # Push tag to remote
        returncode, _, stderr = self._run_git("push", "origin", tag_name)
        if returncode != 0:
            return {
                "success": False,
                "error": f"Failed to push tag: {stderr}"
            }

        logger.info(f"Tag created and pushed: {tag_name}")
        return {
            "success": True,
            "tag": tag_name,
            "message": message,
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }

    def get_current_branch(self) -> str:
        """Get current branch name."""
        if not self.work_dir:
            return ""

        returncode, stdout, _ = self._run_git("rev-parse", "--abbrev-ref", "HEAD")
        if returncode == 0:
            return stdout.strip()
        return ""

    def get_commit_hash(self) -> str:
        """Get current commit hash."""
        if not self.work_dir:
            return ""

        returncode, stdout, _ = self._run_git("rev-parse", "HEAD")
        if returncode == 0:
            return stdout.strip()
        return ""

    def list_files(self, pattern: str = "*") -> List[str]:
        """List files matching pattern in repository."""
        if not self.work_dir:
            return []

        try:
            from pathlib import Path
            matching = []
            for path in self.work_dir.rglob(pattern):
                if path.is_file() and ".git" not in path.parts:
                    matching.append(str(path.relative_to(self.work_dir)))
            return matching
        except Exception as e:
            logger.error(f"Failed to list files: {e}")
            return []


def sync_skills_to_github_real(
    repo_url: str,
    skills_dir: Path,
    tenant_id: str = "_default"
) -> Dict[str, Any]:
    """Execute real GitHub sync: clone → commit → push → tag."""

    branch_name = f"tenant-sync-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}"

    try:
        with GitHubGitWrapper(repo_url, tenant_id) as git:
            # Step 1: Clone
            clone_result = git.clone()
            if not clone_result["success"]:
                return clone_result

            # Step 2: Create branch
            branch_result = git.create_branch(branch_name)
            if not branch_result["success"]:
                return branch_result

            # Step 3: Prepare skills
            if not skills_dir.exists():
                logger.warning(f"Skills directory not found: {skills_dir}")
                skills_list = []
            else:
                skills_list = []
                for skill_file in skills_dir.glob("*.md"):
                    skills_list.append({
                        "name": skill_file.stem,
                        "size": skill_file.stat().st_size
                    })

            # Step 4: Write manifest
            manifest = {
                "tenant_id": tenant_id,
                "synced_at": datetime.utcnow().isoformat() + "Z",
                "skills_count": len(skills_list),
                "skills": skills_list,
                "branch": branch_name
            }

            manifest_result = git.write_file(
                "skills/manifest.json",
                json.dumps(manifest, indent=2)
            )
            if not manifest_result["success"]:
                return manifest_result

            # Step 5: Copy skill files
            files_written = [manifest_result["file"]]
            if skills_dir.exists():
                for skill_file in skills_dir.glob("*.md"):
                    content = skill_file.read_text()
                    result = git.write_file(
                        f"skills/{skill_file.name}",
                        content
                    )
                    if result["success"]:
                        files_written.append(result["file"])

            # Step 6: Commit and push
            commit_result = git.commit_and_push(
                branch_name,
                f"[Corvin Sync] Tenant {tenant_id}: {len(files_written)} files updated"
            )
            if not commit_result["success"]:
                return commit_result

            # Step 7: Create release tag
            tag_result = git.create_tag(
                f"release-{datetime.utcnow().strftime('%Y%m%d-%H%M%S')}",
                f"Tenant {tenant_id} synchronization\n\n"
                f"Skills: {len(skills_list)}\n"
                f"Files: {len(files_written)}\n"
                f"Branch: {branch_name}"
            )

            return {
                "success": True,
                "repo_url": repo_url,
                "branch": branch_name,
                "commit": commit_result.get("commit_hash"),
                "tag": tag_result.get("tag") if tag_result.get("success") else None,
                "files_written": files_written,
                "skills_synced": len(skills_list),
                "timestamp": datetime.utcnow().isoformat() + "Z"
            }

    except Exception as e:
        logger.error(f"Sync to GitHub failed: {e}")
        return {
            "success": False,
            "error": str(e),
            "timestamp": datetime.utcnow().isoformat() + "Z"
        }
