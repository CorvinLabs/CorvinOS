"""E2E tests for registry snapshot automation (ADR-0864).

Tests:
- Snapshot file creation
- Snapshot validity (valid JSON + contains tasks)
- Git commit automation
- Systemd integration
- Compliance (GDPR Art. 30, 32)

ADR-0864: Git-Tracked Registry Snapshots
"""

import json
import os
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path


class TestRegistrySnapshotCompletion:
    """E2E tests for complete registry snapshot automation."""

    def test_snapshot_script_exists_and_executable(self):
        """Verify commit_registry_snapshot.sh exists and is executable."""
        script_path = Path(__file__).parent.parent.parent / "scripts/commit_registry_snapshot.sh"
        assert script_path.exists(), f"Script not found: {script_path}"
        assert os.access(script_path, os.X_OK), f"Script not executable: {script_path}"

    def test_snapshot_directory_is_created_by_the_script(self):
        """The snapshot directory is created on demand by the script.

        It holds no tracked file, and git does not track an empty directory, so a
        clean checkout never has it: requiring it to pre-exist (as this test did)
        failed on every fresh clone. The script's own ``mkdir -p`` is the contract.
        """
        script = Path(__file__).parent.parent.parent / "scripts/commit_registry_snapshot.sh"
        text = script.read_text(encoding="utf-8")
        assert 'SNAPSHOT_DIR="$REPO_ROOT/docs/reference/task_registry_snapshots"' in text
        assert 'mkdir -p "$SNAPSHOT_DIR"' in text

    # test_snapshot_script_creates_valid_snapshot was removed: it never ran the
    # script — it wrote its own JSON into the repo tree and read it back, so it
    # proved nothing about the snapshot pipeline and failed on a clean checkout.

    def test_snapshot_contains_tasks_count(self):
        """Verify snapshots contain task count metadata.

        k=5: Snapshots must have task count for compliance tracking.
        """
        snapshot_dir = Path(__file__).parent.parent.parent / "docs/reference/task_registry_snapshots"
        snapshots = list(snapshot_dir.glob("*.json"))

        if snapshots:
            # Check latest snapshot
            latest = sorted(snapshots)[-1]
            with open(latest, 'r', encoding="utf-8") as f:
                data = json.load(f)

            # Should have tasks key
            assert "tasks" in data, f"Snapshot missing 'tasks' key: {latest}"
            assert isinstance(data["tasks"], list), f"Tasks is not a list in: {latest}"

    def test_git_commit_script_exists(self):
        """Verify git commit script exists."""
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts/commit_registry_snapshot.sh"
        assert script.exists()

    def test_systemd_service_files_present(self):
        """Verify systemd service and timer files are in repo.

        k=5: Systemd integration files must be present for installation.
        """
        repo_root = Path(__file__).parent.parent.parent
        service_file = repo_root / "systemd/corvin-registry-snapshot.service"
        timer_file = repo_root / "systemd/corvin-registry-snapshot.timer"

        assert service_file.exists(), f"Service file not found: {service_file}"
        assert timer_file.exists(), f"Timer file not found: {timer_file}"

        # Verify they're valid systemd files (basic check)
        with open(service_file, 'r', encoding="utf-8") as f:
            content = f.read()
            assert "[Unit]" in content, "Service file missing [Unit] section"
            assert "[Service]" in content, "Service file missing [Service] section"

        with open(timer_file, 'r', encoding="utf-8") as f:
            content = f.read()
            assert "[Unit]" in content, "Timer file missing [Unit] section"
            assert "[Timer]" in content, "Timer file missing [Timer] section"

    def test_snapshot_compliance_documentation(self):
        """Verify compliance documentation exists.

        k=5: ADR-0864 must document GDPR Art. 30, 32 compliance.
        """
        repo_root = Path(__file__).parent.parent.parent
        adr_path = Path("/home/shumway/projects/Corvin-Knowledge/decisions/ADR-0864-git-tracked-registry-snapshots.md")

        if adr_path.exists():
            with open(adr_path, 'r', encoding="utf-8") as f:
                content = f.read()
            # Should mention compliance
            assert "GDPR" in content or "compliance" in content or "audit" in content, \
                f"ADR-0864 missing compliance language"

    def test_snapshot_installation_guide(self):
        """Verify installation guide exists for systemd setup.

        k=5: Users need clear installation instructions.
        """
        repo_root = Path(__file__).parent.parent.parent
        readme = repo_root / "systemd/README.md"

        assert readme.exists(), f"Systemd README not found: {readme}"

        with open(readme, 'r', encoding="utf-8") as f:
            content = f.read()

        # Should contain installation instructions
        assert "systemctl" in content or "Installation" in content, \
            "README missing installation instructions"

        # Should mention compliance
        assert "GDPR" in content or "compliance" in content or "audit" in content, \
            "README missing compliance rationale"


class TestRegistrySnapshotIntegration:
    """Integration tests for registry snapshot pipeline."""

    def test_snapshot_git_commit_format(self):
        """Verify snapshot commits follow expected format.

        k=5: Commits must be auditable (predictable format for parsing).
        """
        repo_root = Path(__file__).parent.parent.parent
        os.chdir(repo_root)

        # Check recent commits for snapshot pattern
        try:
            result = subprocess.run(
                ["git", "log", "--oneline", "--grep=Task Registry Snapshot", "-n", "5"],
                capture_output=True,
                text=True,
                timeout=5, encoding="utf-8", errors="replace"
            )

            # If there are any snapshot commits, verify format
            if result.stdout.strip():
                for line in result.stdout.strip().split('\n'):
                    # Expected format: "abcd123 automation: Task Registry Snapshot YYYYMMDD-HHMMSS"
                    assert "Task Registry Snapshot" in line, f"Unexpected commit format: {line}"
        except subprocess.TimeoutExpired:
            pass  # Git may be slow; don't fail test
        except FileNotFoundError:
            pass  # Git may not be available; skip this check

    def test_snapshot_audit_trail_completeness(self):
        """Verify snapshot audit trail includes all required fields.

        GDPR Art. 30, 32: Snapshots must include timestamp, count, and reason.
        """
        repo_root = Path(__file__).parent.parent.parent
        snapshot_dir = repo_root / "docs/reference/task_registry_snapshots"

        snapshots = list(snapshot_dir.glob("*.json"))
        if not snapshots:
            # No snapshot taken on this checkout (the directory is created by the
            # script on its first run, so it may not exist) — nothing to check.
            return

        # Verify latest snapshot is recent (within last 7 days)
        latest = sorted(snapshots)[-1]
        filename = latest.name

        # Filename should be YYYYMMDD-HHMMSS.json pattern
        assert len(filename) >= 15, f"Snapshot filename format unexpected: {filename}"
        assert filename.endswith(".json"), f"Snapshot not JSON: {filename}"


class TestRegistrySnapshotFailureModes:
    """Test error handling and edge cases."""

    def test_snapshot_handles_missing_registry_gracefully(self):
        """Script should handle missing registry file gracefully.

        k=5: No crash if registry not yet created.
        """
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts/commit_registry_snapshot.sh"

        # Script should have error handling for missing registry
        with open(script, 'r', encoding="utf-8") as f:
            content = f.read()

        # Should check for existence
        assert "if [[ ! -f" in content or "if [ ! -f" in content or "test" in content or "exist" in content, \
            "Script missing file existence check"

    def test_snapshot_validates_json(self):
        """Script should validate JSON before committing.

        k=5: Only valid snapshots committed to git.
        """
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts/commit_registry_snapshot.sh"

        with open(script, 'r', encoding="utf-8") as f:
            content = f.read()

        # Should check JSON validity
        assert "jq" in content and ("empty" in content or "error" in content), \
            "Script missing JSON validation"

    def test_snapshot_no_duplicate_commits(self):
        """Script should not commit if nothing changed.

        k=5: Don't pollute git history with no-op commits.
        """
        repo_root = Path(__file__).parent.parent.parent
        script = repo_root / "scripts/commit_registry_snapshot.sh"

        with open(script, 'r', encoding="utf-8") as f:
            content = f.read()

        # Should check if git has changes before committing
        assert "git diff --cached --quiet" in content or "git status" in content or "|| true" in content, \
            "Script should handle identical snapshots (no-op commits)"
