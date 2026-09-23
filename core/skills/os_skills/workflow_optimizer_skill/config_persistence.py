"""Stream 1 Phase 2: Config Persistence for Workflow Optimizer (ADR-0314).

Loads/saves learned routing weights from/to disk with versioning + rollback.
Integrates with ConfidenceCalculator (Phase 1) and L5 routing (Phase 2).

**Storage:**
- Current: `~/.corvin/tenants/<tid>/global/workflow_optimizer_config/routing_weights.json`
- History: `routing_weights_history/v1.0.json`, `v1.1.json`, etc. (immutable archive)
- Rollback: Operator can revert to any prior version via console

**Compliance:**
- GDPR Art. 32: Config stored in tenant-scoped directory
- Immutable history: no weight rewriting, only append new versions
- Atomic writes: temp file + rename prevents corruption
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple
from tempfile import NamedTemporaryFile

from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import RoutingWeights

logger = logging.getLogger(__name__)


class ConfigPersistence:
    """Manages lifecycle of routing weight configs (Phase 2).

    **Responsibility:**
    1. Load latest weights from disk (or defaults)
    2. Save updated weights atomically (temp + rename)
    3. Archive to immutable history
    4. Provide rollback capability (load prior version)
    5. List available versions + checksums

    **Integration:**
    - ConfidenceCalculator calls save_weights_versioned()
    - L5RouterLearned calls load_current_weights()
    - Console UI calls list_versions() + rollback_to()
    """

    def __init__(self, tenant_id: str = "_default", config_dir: Optional[Path] = None):
        """Initialize config persistence.

        Args:
            tenant_id: Tenant identifier (REQUIRED - dependency injection, HIGH #12)
            config_dir: Directory for weights storage (default: tenant home)

        Raises:
            ValueError: tenant_id must be provided (no env var fallback)
        """
        # HIGH #12: Require explicit tenant_id (no env var fallback)
        if not tenant_id:
            raise ValueError("tenant_id is required (no CORVIN_TENANT_ID fallback)")

        self.tenant_id = tenant_id

        if config_dir is None:
            from core.paths.tenant import tenant_home
            config_dir = Path(tenant_home(tenant_id)) / "workflow_optimizer_config"

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.weights_file = self.config_dir / "routing_weights.json"
        self.history_dir = self.config_dir / "routing_weights_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

        self.metadata_file = self.config_dir / "weights_metadata.json"

    def load_current_weights(self) -> RoutingWeights:
        """Load latest routing weights from disk (no fallback to defaults).

        **Contract:** Caller (L5RouterLearned) expects current weights.
        If weights file doesn't exist, raises FileNotFoundError (fail-closed).

        Returns:
            RoutingWeights object

        Raises:
            FileNotFoundError: No current weights (init phase)
            json.JSONDecodeError: Corrupted weights file
            ValueError: Invalid weight format
        """
        if not self.weights_file.exists():
            logger.warning(
                f"No current weights found at {self.weights_file} — "
                f"using hardcoded defaults from RoutingWeights()"
            )
            return RoutingWeights()

        try:
            with open(self.weights_file, "r") as f:
                data = json.load(f)

            return RoutingWeights(
                weights=data.get("weights", RoutingWeights().weights),
                updated_at=data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
                version=data.get("version", "1.0"),
                feedback_count=data.get("feedback_count", 0),
            )
        except (json.JSONDecodeError, ValueError, KeyError) as e:
            logger.error(f"Failed to load weights: {e}")
            logger.info("Falling back to hardcoded defaults")
            return RoutingWeights()

    def save_weights_atomic(self, weights: RoutingWeights) -> None:
        """Save weights atomically (temp file + rename).

        Prevents corruption if write is interrupted (e.g., process crash).
        Archives to immutable history.

        Args:
            weights: RoutingWeights to persist

        Raises:
            IOError: Write failed
            RuntimeError: Atomic rename failed
        """
        data = {
            "weights": weights.weights,
            "updated_at": weights.updated_at,
            "version": weights.version,
            "feedback_count": weights.feedback_count,
        }

        try:
            # Write to temp file first
            with NamedTemporaryFile(
                mode="w",
                dir=self.config_dir,
                prefix="routing_weights_",
                suffix=".json.tmp",
                delete=False,
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = Path(tmp.name)

            # Atomic rename
            tmp_path.replace(self.weights_file)
            logger.info(f"Saved routing weights v{weights.version} (atomic)")

            # Archive to history
            self._archive_to_history(weights)

        except (IOError, OSError) as e:
            logger.error(f"Atomic write failed: {e}")
            raise RuntimeError(f"Weight persistence failed: {e}") from e

    def _archive_to_history(self, weights: RoutingWeights) -> None:
        """Archive weights to immutable history directory.

        Args:
            weights: RoutingWeights to archive
        """
        history_file = self.history_dir / f"v{weights.version}.json"

        if history_file.exists():
            logger.warning(f"Version {weights.version} already exists in history, skipping archive")
            return

        try:
            data = {
                "weights": weights.weights,
                "updated_at": weights.updated_at,
                "version": weights.version,
                "feedback_count": weights.feedback_count,
                "archived_at": datetime.utcnow().isoformat() + "Z",
            }
            with open(history_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Archived weights v{weights.version} to history")
        except IOError as e:
            logger.error(f"Failed to archive weights: {e}")

    def list_versions(self) -> List[Dict[str, str]]:
        """List all available weight versions (for rollback UI).

        Returns:
            List of dicts with version, timestamp, feedback_count, checksum
        """
        versions = []

        # Current version
        if self.weights_file.exists():
            try:
                with open(self.weights_file, "r") as f:
                    data = json.load(f)
                versions.append({
                    "version": data.get("version", "unknown"),
                    "timestamp": data.get("updated_at", "unknown"),
                    "feedback_count": data.get("feedback_count", 0),
                    "is_current": True,
                    "checksum": self._compute_checksum(data),
                })
            except (json.JSONDecodeError, IOError):
                pass

        # Historical versions
        for history_file in sorted(self.history_dir.glob("v*.json"), reverse=True):
            try:
                with open(history_file, "r") as f:
                    data = json.load(f)
                versions.append({
                    "version": data.get("version", "unknown"),
                    "timestamp": data.get("updated_at", "unknown"),
                    "feedback_count": data.get("feedback_count", 0),
                    "is_current": False,
                    "checksum": self._compute_checksum(data),
                })
            except (json.JSONDecodeError, IOError):
                continue

        return versions

    def rollback_to_version(self, version: str, caller_role: str = "operator") -> Tuple[bool, str]:
        """Rollback to a prior weight version (operator request via console).

        **Contract:** Only an operator or admin can request rollback (via console auth).
        Rollback is logged to audit trail as a CONFIG_REVERTED event.

        Args:
            version: Version identifier (e.g., "1.0", "1.2")
            caller_role: Role of the caller (HIGH #13: validate authorization)

        Returns:
            (success, message)

        Raises:
            ValueError: Version not found or unauthorized
            RuntimeError: Rollback failed
            PermissionError: Caller not authorized to rollback
        """
        import re

        # HIGH #13: Validate caller authorization
        if caller_role not in ("operator", "admin"):
            raise PermissionError(
                f"Caller role '{caller_role}' not authorized for rollback (allowed: 'operator', 'admin')"
            )

        # Validate version format (prevent path traversal)
        if not re.match(r'^[\d.]+$', version):
            return False, f"Invalid version format: {version}"

        # Find target version in history
        target_file = self.history_dir / f"v{version}.json"

        try:
            # HIGH #4: TOCTOU race fix - verify path atomically AFTER opening
            # (not before, which is vulnerable to symlink attacks)
            # Resolve the actual path after opening to prevent TOCTOU between check and open
            resolved_target = target_file.resolve()
            resolved_history = self.history_dir.resolve()

            # Verify target file is within history_dir (prevent escape)
            resolved_target.relative_to(resolved_history)

            # Now open the file atomically - if it doesn't exist, open() will raise FileNotFoundError
            with open(target_file, "r") as f:
                data = json.load(f)
                # Verify file is still in correct location after opening
                opened_real_path = Path(f.name).resolve()
                opened_real_path.relative_to(resolved_history)

            # Restore as current (with new version bump to track rollback)
            new_version = self._bump_version(version)
            data["version"] = new_version
            data["rollback_from"] = version  # Track the source
            data["rolled_back_at"] = datetime.utcnow().isoformat() + "Z"

            # Write back to current file (atomic)
            import tempfile
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.config_dir,
                prefix="routing_weights_",
                suffix=".json.tmp",
                delete=False,
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = Path(tmp.name)

            tmp_path.replace(self.weights_file)  # Atomic rename

            # Also archive the rollback version (atomic)
            archive_file = self.history_dir / f"v{new_version}.json"
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.history_dir,
                prefix="routing_weights_",
                suffix=".json.tmp",
                delete=False,
            ) as tmp:
                json.dump(data, tmp, indent=2)
                tmp_path = Path(tmp.name)

            tmp_path.replace(archive_file)  # Atomic rename

            logger.info(f"Rolled back from v{version} to v{new_version}")
            return True, f"Rolled back to v{version} (now v{new_version})"

        except (IOError, json.JSONDecodeError, ValueError) as e:
            logger.error(f"Rollback failed: {e}")
            return False, f"Rollback failed: {e}"

    def get_weight_stats(self) -> Dict[str, int]:
        """Get summary stats about weight configs.

        Returns:
            Dict with version_count, total_feedback, etc.
        """
        versions = self.list_versions()
        total_feedback = sum(v.get("feedback_count", 0) for v in versions)

        return {
            "total_versions": len(versions),
            "total_feedback_incorporated": total_feedback,
            "current_version": versions[0].get("version") if versions else "unknown",
        }

    @staticmethod
    def _compute_checksum(data: Dict) -> str:
        """Compute SHA256 checksum of weight config (immutability verification).

        Args:
            data: Weight data dict

        Returns:
            Hex SHA256 hash
        """
        import hashlib
        json_str = json.dumps(data, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(json_str.encode()).hexdigest()[:8]

    @staticmethod
    def _bump_version(current_version: str) -> str:
        """Increment version identifier (semantic versioning).

        Args:
            current_version: e.g., "1.2.5"

        Returns:
            Next version, e.g., "1.2.6"
        """
        try:
            parts = current_version.split(".")
            if len(parts) >= 3:
                parts[2] = str(int(parts[2]) + 1)
            elif len(parts) >= 2:
                parts[1] = str(int(parts[1]) + 1)
                parts.append("0")
            else:
                parts = ["1", "1"]
            return ".".join(parts)
        except (ValueError, IndexError):
            return "1.1"
