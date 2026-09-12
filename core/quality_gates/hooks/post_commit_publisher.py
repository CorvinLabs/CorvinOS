"""Post-Commit Event Publisher for Quality Gates System (ADR-0688 Phase 2.3).

After a commit succeeds:
1. Extract artifact ID from commit message/changed files
2. Run quality gate validators on the artifact
3. POST results to /api/quality/gates/events (async, non-blocking)
4. Log results to .corvin/quality-gates-post-commit.log

This hook is non-blocking — commit succeeds even if validation fails.
"""

import os
import sys
import json
import logging
import subprocess
from datetime import datetime
from typing import Optional, Dict, Any
import hashlib

# Configure logging
log_dir = os.path.expanduser("~/.corvin")
os.makedirs(log_dir, exist_ok=True)

log_file = os.path.join(log_dir, "quality-gates-post-commit.log")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(log_file),
        logging.StreamHandler(sys.stderr),
    ],
)
logger = logging.getLogger(__name__)


def get_commit_sha() -> str:
    """Get the current commit SHA.

    Returns:
        Commit SHA (shortened)

    Raises:
        RuntimeError: If git command fails
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()[:12]  # Shortened SHA
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get commit SHA: {e}")
        raise RuntimeError(f"Failed to get commit SHA: {e}")


def get_commit_message() -> str:
    """Get the commit message of HEAD.

    Returns:
        Commit message

    Raises:
        RuntimeError: If git command fails
    """
    try:
        result = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get commit message: {e}")
        raise RuntimeError(f"Failed to get commit message: {e}")


def get_changed_files() -> list:
    """Get files changed in this commit.

    Returns:
        List of file paths changed in HEAD
    """
    try:
        result = subprocess.run(
            ["git", "diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD"],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip().split("\n") if result.stdout.strip() else []
    except subprocess.CalledProcessError as e:
        logger.error(f"Failed to get changed files: {e}")
        return []


def extract_artifact_id(commit_message: str, changed_files: list) -> Optional[str]:
    """Extract artifact ID from commit message or changed files.

    Strategy:
    1. Look for ADR-NNNN in commit message
    2. Look for CONCEPT-NNNN in commit message
    3. Look for Corvin-ADR/decisions/ADR-NNNN in changed files
    4. Look for concepts/CONCEPT-NNNN in changed files

    Args:
        commit_message: Commit message
        changed_files: List of changed files

    Returns:
        Artifact ID (e.g., "ADR-0688") or None if not found
    """
    # Check commit message for ADR references
    import re

    # Pattern: ADR-NNNN or CONCEPT-NNNN
    adr_pattern = r"(ADR-\d{4})"
    concept_pattern = r"(CONCEPT-\d{4})"

    # Search commit message
    adr_match = re.search(adr_pattern, commit_message)
    if adr_match:
        return adr_match.group(1)

    concept_match = re.search(concept_pattern, commit_message)
    if concept_match:
        return concept_match.group(1)

    # Search changed files
    for file in changed_files:
        # ADR files: Corvin-ADR/decisions/ADR-NNNN-*.md
        adr_match = re.search(r"(ADR-\d{4})", file)
        if adr_match:
            return adr_match.group(1)

        # Concept files: */concepts/CONCEPT-NNNN-*.md
        concept_match = re.search(r"(CONCEPT-\d{4})", file)
        if concept_match:
            return concept_match.group(1)

    return None


def validate_artifact(artifact_id: str, tenant_id: str = "_default") -> Dict[str, Any]:
    """Validate an artifact using corvin CLI.

    Args:
        artifact_id: Artifact ID (e.g., "ADR-0688")
        tenant_id: Tenant ID

    Returns:
        Validation result dict
    """
    try:
        result = subprocess.run(
            [
                "python", "-m", "core.quality_gates.cli",
                "validate",
                f"--artifact={artifact_id}",
                f"--tenant-id={tenant_id}",
                "--format=json",
            ],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=5.0,
        )

        if result.returncode == 0:
            # Parse JSON output
            try:
                return json.loads(result.stdout)
            except json.JSONDecodeError:
                logger.warning(f"Failed to parse validator output JSON: {result.stdout}")
                return {"error": "Failed to parse validator output"}
        else:
            logger.warning(f"Validator failed with code {result.returncode}: {result.stderr}")
            return {"error": result.stderr}

    except subprocess.TimeoutExpired:
        logger.error(f"Validator timeout for {artifact_id}")
        return {"error": "Validator timeout"}
    except Exception as e:
        logger.error(f"Validator error for {artifact_id}: {e}")
        return {"error": str(e)}


def publish_event_to_api(
    commit_sha: str,
    artifact_id: str,
    validation_result: Dict[str, Any],
    tenant_id: str = "_default",
) -> bool:
    """Publish validation event to /api/quality/gates/events (async, non-blocking).

    Args:
        commit_sha: Commit SHA
        artifact_id: Artifact ID
        validation_result: Validation result dict
        tenant_id: Tenant ID

    Returns:
        True if publish succeeded (or timed out gracefully), False otherwise
    """
    event = {
        "commit_sha": commit_sha,
        "artifact_id": artifact_id,
        "tenant_id": tenant_id,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "validation_result": validation_result,
    }

    # Try to POST to localhost API (non-blocking, timeout 2s)
    try:
        result = subprocess.run(
            [
                "curl",
                "-X", "POST",
                "-H", "Content-Type: application/json",
                "-d", json.dumps(event),
                "--max-time", "2",
                "http://localhost:8765/api/quality/gates/events",
            ],
            cwd=os.getcwd(),
            capture_output=True,
            text=True,
            timeout=3.0,
        )

        if result.returncode == 0:
            logger.info(f"Event published for {artifact_id} (commit {commit_sha})")
            return True
        else:
            # API not reachable or failed; log but don't block
            logger.debug(f"Event publish failed (API unreachable): {result.stderr[:100]}")
            return False

    except subprocess.TimeoutExpired:
        # Timeout is expected and OK (post-commit is non-blocking)
        logger.debug(f"Event publish timed out (expected for async post-commit)")
        return True
    except Exception as e:
        logger.debug(f"Event publish error (non-blocking): {e}")
        return True  # Return True to not block commit


def main() -> int:
    """Main entry point for post-commit hook.

    Returns:
        Exit code (always 0 — never block commit)
    """
    try:
        logger.info("Quality Gates post-commit hook started")

        # Extract commit info
        commit_sha = get_commit_sha()
        commit_message = get_commit_message()
        changed_files = get_changed_files()

        logger.info(f"Commit SHA: {commit_sha}")
        logger.info(f"Changed files: {len(changed_files)}")

        # Extract artifact ID
        artifact_id = extract_artifact_id(commit_message, changed_files)
        if not artifact_id:
            logger.debug("No artifact ID found in commit message or files")
            return 0  # Don't block commit

        logger.info(f"Artifact ID: {artifact_id}")

        # Get tenant ID (from env or default)
        tenant_id = os.environ.get("CORVIN_TENANT_ID", "_default")

        # Run validators
        validation_result = validate_artifact(artifact_id, tenant_id)
        logger.info(f"Validation result: {validation_result}")

        # Publish event (async, non-blocking)
        publish_event_to_api(commit_sha, artifact_id, validation_result, tenant_id)

        logger.info("Quality Gates post-commit hook completed successfully")
        return 0

    except Exception as e:
        logger.error(f"Post-commit hook error: {e}", exc_info=True)
        # Don't block commit on error
        return 0


if __name__ == "__main__":
    sys.exit(main())
