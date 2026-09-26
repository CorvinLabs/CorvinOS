"""KeyManagementConfig - Reject hardcoded snapshot keys in production.

Implements FIX #4 of Phase 9 production deployment.
Ensures cryptographic keys are sourced from external management (env var or HSM).
"""

import os
from pathlib import Path


class KeyManagementConfig:
    """Manage cryptographic keys with fail-closed hardcoded rejection."""

    @staticmethod
    def get_snapshot_key() -> str:
        """Get cryptographic key (reject hardcoded default).

        Priority:
        1. CORVIN_SNAPSHOT_KEY env var (if set and != "default-key")
        2. ~/.corvin/keys/snapshot.key file (if exists and != "default-key")
        3. Raise ValueError (fail-closed: no hardcoded fallback)

        Returns:
            str: Cryptographic key for snapshot verification

        Raises:
            ValueError: If no valid key found or only hardcoded default available
        """
        # Check environment variable first
        key = os.getenv("CORVIN_SNAPSHOT_KEY")
        if key and key != "default-key":
            return key

        # Check keyfile second
        keyfile = Path.home() / ".corvin" / "keys" / "snapshot.key"
        if keyfile.exists():
            try:
                key = keyfile.read_text().strip()
                if key and key != "default-key":
                    return key
            except Exception as e:
                raise ValueError(
                    f"Failed to read snapshot key from {keyfile}: {e}"
                )

        # Fail-closed: no hardcoded fallback
        raise ValueError(
            "CORVIN_SNAPSHOT_KEY not set and no key file found — "
            "cannot verify snapshots. Set CORVIN_SNAPSHOT_KEY env var or "
            "create ~/.corvin/keys/snapshot.key with a production key."
        )
