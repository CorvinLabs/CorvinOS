"""
C2: Concurrency Model for Aggregation ↔ Active Sessions
C3: Atomic Symlink Switching
C4: Closed Feedback Loop (Danger Zone Guard)

Explicit contracts and implementations to prevent race conditions.
"""

import os
import time
import threading
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


class ConcurrencyContract:
    """
    C2: Explicit concurrency model for aggregation ↔ sessions.

    Contract:
    - Aggregator runs nightly 2:00–3:00 UTC
    - Sessions may append during aggregation
    - Aggregator reads queue files PRESENT AT 2:00 (not new appends after)
    - Records appended after 2:00 processed in NEXT nightly run
    - Consequence: max 24h stale profiles, guaranteed

    Implementation:
    - File locking: write-lock on queue appends, read-lock on aggregation
    - Checkpointing: aggregator resumes from last-processed record if crash
    - Timestamp tracking: know which records were processed when
    """

    AGGREGATION_WINDOW_START = 2  # 2:00 UTC
    AGGREGATION_WINDOW_DURATION = 60  # 1 hour

    @staticmethod
    def in_aggregation_window() -> bool:
        """Check if current time is in aggregation window."""
        now = datetime.utcnow()
        window_start = now.replace(hour=ConcurrencyContract.AGGREGATION_WINDOW_START, minute=0, second=0)
        window_end = window_start + timedelta(minutes=ConcurrencyContract.AGGREGATION_WINDOW_DURATION)

        return window_start <= now <= window_end

    @staticmethod
    def acquire_read_lock(filepath: Path, timeout_seconds: int = 30) -> bool:
        """
        Aggregator acquires read-lock on queue file.

        Args:
            filepath: Path to queue file
            timeout_seconds: Max time to wait for lock

        Returns:
            True if lock acquired, False if timeout
        """
        lock_file = filepath.with_suffix(".lock.read")
        start = time.time()

        while time.time() - start < timeout_seconds:
            try:
                # Create lock file (fails if already exists)
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.1)

        logger.error(f"Failed to acquire read-lock on {filepath} after {timeout_seconds}s")
        return False

    @staticmethod
    def release_read_lock(filepath: Path) -> None:
        """Release read-lock."""
        lock_file = filepath.with_suffix(".lock.read")
        if lock_file.exists():
            lock_file.unlink()

    @staticmethod
    def acquire_write_lock(filepath: Path, timeout_seconds: int = 5) -> bool:
        """
        Session acquires write-lock before appending.

        Args:
            filepath: Path to queue file
            timeout_seconds: Max time to wait

        Returns:
            True if lock acquired
        """
        lock_file = filepath.with_suffix(".lock.write")
        start = time.time()

        while time.time() - start < timeout_seconds:
            try:
                fd = os.open(str(lock_file), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
                return True
            except FileExistsError:
                time.sleep(0.01)

        logger.error(f"Failed to acquire write-lock on {filepath} after {timeout_seconds}s")
        return False

    @staticmethod
    def release_write_lock(filepath: Path) -> None:
        """Release write-lock."""
        lock_file = filepath.with_suffix(".lock.write")
        if lock_file.exists():
            lock_file.unlink()


class AggregatorCheckpoint:
    """
    Checkpointing for aggregator: resume from last-processed record if crash.
    """

    def __init__(self, checkpoint_file: Path):
        self.checkpoint_file = checkpoint_file

    def load(self) -> Dict:
        """Load checkpoint."""
        if self.checkpoint_file.exists():
            import json
            with open(self.checkpoint_file, "r") as f:
                return json.load(f)

        return {
            "version": "1.0",
            "last_processed_task_id": None,
            "last_processed_timestamp": None,
            "record_count": 0,
            "updated_at": None,
        }

    def save(self, checkpoint: Dict) -> None:
        """Save checkpoint."""
        import json
        checkpoint["updated_at"] = datetime.utcnow().isoformat() + "Z"
        with open(self.checkpoint_file, "w") as f:
            json.dump(checkpoint, f, indent=2)


class AtomicSymlinkManager:
    """
    C3: Atomic symlink switching to prevent broken symlinks during profile update.

    Strategy:
    1. Write new profile: tenant-baseline.v{ts}.json
    2. Create temp symlink: tenant-baseline.json.tmp → v{ts}
    3. Atomic rename: tenant-baseline.json.tmp → tenant-baseline.json
    """

    @staticmethod
    def atomic_symlink_update(
        profile_dir: Path,
        profile_basename: str,
        new_version: str,
    ) -> bool:
        """
        Atomically update symlink to point to new profile version.

        Args:
            profile_dir: Directory containing profiles
            profile_basename: e.g., "tenant-baseline"
            new_version: e.g., "202608071800"

        Returns:
            True if success, False if failed
        """
        try:
            new_file = profile_dir / f"{profile_basename}.v{new_version}.json"
            symlink_target = f"{profile_basename}.v{new_version}.json"

            # Temp symlink path
            temp_symlink = profile_dir / f"{profile_basename}.json.tmp"

            # Remove stale temp symlink
            if temp_symlink.exists() or temp_symlink.is_symlink():
                temp_symlink.unlink()

            # Create temp symlink
            os.symlink(symlink_target, str(temp_symlink))

            # Atomic rename (POSIX atomic on most filesystems)
            symlink_path = profile_dir / f"{profile_basename}.json"
            os.rename(str(temp_symlink), str(symlink_path))

            logger.info(f"Atomically updated {symlink_path} → {symlink_target}")
            return True

        except Exception as e:
            logger.error(f"Atomic symlink update failed: {e}", exc_info=True)
            return False

    @staticmethod
    def get_current_profile_version(profile_dir: Path, profile_basename: str) -> Optional[str]:
        """
        Get the version that symlink currently points to.

        Returns:
            Version string (e.g., "202608071800") or None if symlink doesn't exist
        """
        symlink_path = profile_dir / f"{profile_basename}.json"

        if not symlink_path.is_symlink():
            return None

        target = os.readlink(str(symlink_path))
        # Extract version from "profile_basename.v{version}.json"
        if f"{profile_basename}.v" in target:
            version = target.replace(f"{profile_basename}.v", "").replace(".json", "")
            return version

        return None


class DangerZoneGuard:
    """
    C4: Closed Feedback Loop - Enforce danger zones to prevent bad contexts.

    Danger zones are patterns learned to be harmful in specific conditions.
    Example: "skipping tests when urgent = 70% fail"

    This guard blocks contexts when dangerous conditions are detected.
    """

    def __init__(self, profiles: Dict):
        """
        Args:
            profiles: Loaded profiles dict from Tier 3
        """
        self.profiles = profiles
        self.blocked_count = 0

    def should_use_context(
        self,
        context_id: str,
        conditions: Dict,
        user_id: Optional[str] = None,
    ) -> Tuple[bool, Optional[str]]:
        """
        Check if context should be used given conditions.

        Args:
            context_id: e.g., "adr-0269", "skill-e2e-wiring"
            conditions: {"task_type": "ml", "urgency": "asap", ...}
            user_id: Optional user ID for per-user profiles

        Returns:
            (allowed, reason_if_blocked)
            allowed=True → use context
            allowed=False → danger zone matched, don't use
        """
        # Check per-user profile first
        user_profile = None
        if user_id:
            user_profile = self.profiles.get(f"user-{user_id}")

        # Check danger zones
        danger_zones = []
        if user_profile:
            danger_zones.extend(user_profile.get("danger_zones", []))

        # Also check tenant baseline danger zones
        baseline = self.profiles.get("tenant-baseline", {})
        danger_zones.extend(baseline.get("danger_zones", []))

        for danger_zone in danger_zones:
            if self._matches_pattern(danger_zone, context_id, conditions):
                self.blocked_count += 1
                reason = f"Danger zone matched: {danger_zone}"
                logger.warning(
                    f"Context {context_id} blocked in conditions {conditions}: {reason}"
                )
                return False, reason

        return True, None

    @staticmethod
    def _matches_pattern(danger_zone_desc: str, context_id: str, conditions: Dict) -> bool:
        """
        Check if context matches a danger zone pattern.

        Simple pattern matching:
        - "skipping tests when urgent" → check if "urgent" in conditions AND context relates to testing
        - Could be extended with more sophisticated patterns

        Args:
            danger_zone_desc: e.g., "skipping tests when urgent (70% fail)"
            context_id: e.g., "skill-e2e-wiring"
            conditions: e.g., {"urgency": "asap"}

        Returns:
            True if pattern matches
        """
        desc_lower = danger_zone_desc.lower()

        # Pattern 1: "skipping tests when urgent"
        if "skip" in desc_lower and "test" in desc_lower and "urgent" in desc_lower:
            if conditions.get("urgency") == "asap" or conditions.get("urgency") == "urgent":
                if "test" in context_id.lower() or "e2e" in context_id.lower():
                    return True

        # Pattern 2: Task-type specific danger zones
        # "dangerous for devops"
        if "danger" in desc_lower and "devops" in desc_lower:
            if conditions.get("task_type") == "devops":
                return True

        # Pattern 3: User-style specific
        # "risky for pragmatic"
        if "risk" in desc_lower and "pragmatic" in desc_lower:
            if conditions.get("user_style") == "pragmatic":
                return True

        return False
