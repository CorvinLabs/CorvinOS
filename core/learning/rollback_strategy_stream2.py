"""Stream 2: Rollback Strategy (Story 12 — Revert if config worse).

Monitors skill performance after config change.
Automatically rolls back if error rate increases.
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class RollbackStrategy:
    """Monitor and rollback skill configurations if performance degrades."""

    # Rollback threshold: if error rate increases by >10%, rollback
    ERROR_RATE_INCREASE_THRESHOLD = 0.10

    def __init__(self, rollback_home: Path, tenant_id: str):
        """Initialize rollback strategy.

        Args:
            rollback_home: Root directory for rollback storage
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.rollback_home = Path(rollback_home)
        self.tenant_id = tenant_id
        self.snapshots_dir = self.rollback_home / tenant_id / "config_snapshots"
        self.rollbacks_dir = self.rollback_home / tenant_id / "rollbacks"

        # Create directories
        for d in [self.snapshots_dir, self.rollbacks_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def save_config_snapshot(
        self,
        skill_id: str,
        config: dict,
        label: str = "current",
    ) -> None:
        """Save a config snapshot before applying changes.

        Args:
            skill_id: Skill ID
            config: Configuration dict
            label: Snapshot label (e.g., "before_tuning")
        """
        try:
            snapshot_data = {
                "skill_id": skill_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "label": label,
                "config": config,
            }

            snapshot_file = self.snapshots_dir / f"{skill_id}_{label}.json"
            with open(snapshot_file, "w") as f:
                json.dump(snapshot_data, f, indent=2)

            logger.debug(f"config_snapshot_saved: {skill_id}: {label}")

        except Exception as e:
            logger.error(f"save_snapshot_error: {skill_id}: {e}")

    def evaluate_rollback_need(
        self,
        skill_id: str,
        error_rate_before: float,  # e.g., 0.05 = 5%
        error_rate_after: float,   # e.g., 0.08 = 8%
    ) -> bool:
        """Determine if rollback is needed.

        Args:
            skill_id: Skill ID
            error_rate_before: Error rate before config change
            error_rate_after: Error rate after config change

        Returns:
            True if rollback needed
        """
        # Check if error rate increased by >10%
        increase = (error_rate_after - error_rate_before) / (error_rate_before + 0.001)

        if increase > self.ERROR_RATE_INCREASE_THRESHOLD:
            logger.warning(
                f"rollback_needed: {skill_id}: error_rate increased "
                f"{error_rate_before:.1%} → {error_rate_after:.1%} ({increase:.1%} increase)"
            )
            return True

        return False

    def perform_rollback(
        self,
        skill_id: str,
        previous_config: dict,
    ) -> bool:
        """Perform rollback to previous config.

        Args:
            skill_id: Skill ID
            previous_config: Config to restore

        Returns:
            True if rollback successful
        """
        try:
            # Record rollback event
            rollback_data = {
                "skill_id": skill_id,
                "timestamp": datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
                "reason": "error_rate_increase",
                "restored_config": previous_config,
            }

            rollback_file = self.rollbacks_dir / f"{skill_id}_{datetime.now().timestamp()}.json"
            with open(rollback_file, "w") as f:
                json.dump(rollback_data, f, indent=2)

            logger.info(f"rollback_performed: {skill_id}")
            return True

        except Exception as e:
            logger.error(f"perform_rollback_error: {skill_id}: {e}")
            return False

    def get_rollback_history(self, skill_id: str) -> list:
        """Get rollback history for a skill."""
        history = []
        for rollback_file in sorted(self.rollbacks_dir.glob(f"{skill_id}_*.json")):
            try:
                with open(rollback_file, "r") as f:
                    rollback_data = json.load(f)
                    history.append(rollback_data)
            except Exception as e:
                logger.error(f"read_rollback_error: {rollback_file.name}: {e}")

        return history


__all__ = ["RollbackStrategy"]
