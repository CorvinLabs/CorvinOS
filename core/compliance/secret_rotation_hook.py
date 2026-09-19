"""Boot-time Secret Rotation Hook (GDPR Art. 32) — Phase 2 Blocker 3."""

import logging
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)


def assert_secret_rotation_compliant() -> bool:
    """Enforce secret rotation at boot (fail-closed).
    
    GDPR Art. 32 requires periodic security updates.
    This hook ensures rotation policy is enforced before the system starts.
    """
    try:
        policy_file = Path(__file__).parent.parent.parent / "corvin-secrets-rotation-policy.yaml"
        if not policy_file.exists():
            logger.critical(f"[FAIL-CLOSED] Rotation policy not found: {policy_file}")
            return False

        # Run rotation check at boot
        result = subprocess.run(
            [sys.executable, "scripts/rotate_secrets_periodic.py"],
            capture_output=True,
            text=True,
            timeout=30
        )

        if result.returncode != 0:
            logger.critical(f"[FAIL-CLOSED] Secret rotation check failed:\n{result.stderr}")
            return False

        logger.info("[BOOT] Secret rotation policy verified")
        return True

    except Exception as e:
        logger.exception(f"[FAIL-CLOSED] Boot hook exception: {e}")
        return False


# Call at boot
if __name__ == "__main__":
    if not assert_secret_rotation_compliant():
        logger.critical("[FAIL-CLOSED] Refusing to start without rotation compliance")
        sys.exit(1)
