"""
Learned Threshold Store — ADR-0377 Phase 2b

Persistent storage for learned model selection thresholds.

Features:
1. Per-(task_type, subsystem, tenant_id) learned thresholds
2. Immutable version tracking + timestamps
3. Audit-trail integration (all changes logged)
4. Graceful degradation (missing/corrupt file → defaults)
5. Import/Export for operator backups

Data Model:
  ~/corvin/tenants/_default/learning/learned_thresholds.json
  {
    "version": "1",
    "tenant_id": "_default",
    "updated_at": "2026-09-11T12:34:56.789Z",
    "thresholds": {
      "code_gen:code_analyzer:_default": {
        "task_type": "code_gen",
        "subsystem": "code_analyzer",
        "learned_threshold": 0.42,
        "base_threshold": 0.5,
        "timestamp": "2026-09-11T12:30:00Z",
        "sample_count": 45,
        "converged": true
      },
      ...
    }
  }

Constraints (ADR-0377 Phase 2b):
- Audit-first: threshold changes MUST be logged before persisting
- Tenant-isolated: separate store per tenant_id
- Immutable: stored thresholds are frozen dataclass (never edited in-place)
- Fail-safe: missing/corrupt file → graceful fallback to base 0.5
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from threading import RLock

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class StoredThreshold:
    """Immutable stored threshold record."""
    task_type: str
    subsystem: str
    tenant_id: str
    learned_threshold: float  # [0.1, 0.9]
    base_threshold: float = 0.5  # Immutable baseline
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    sample_count: int = 0  # How many samples led to this threshold
    converged: bool = False
    notes: str = ""  # Operator notes (e.g., "manual override 2026-09-11")

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> StoredThreshold:
        """Reconstruct from dict."""
        # Filter unknown keys (for backward compat)
        valid_keys = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_keys}
        return cls(**filtered)


class LearnedThresholdStore:
    """Persistent store for learned model selection thresholds.

    Thread-safe. Persists to JSON file at:
      ~/.corvin/tenants/<tenant_id>/learning/learned_thresholds.json

    Example:
        store = LearnedThresholdStore(tenant_id="_default")

        # Get learned threshold (or base 0.5 if not converged)
        threshold = store.get_threshold("code_gen", "code_analyzer")

        # Store newly learned threshold (call AFTER audit event)
        stored = StoredThreshold(
            task_type="code_gen",
            subsystem="code_analyzer",
            tenant_id="_default",
            learned_threshold=0.42,
            sample_count=45,
            converged=True
        )
        store.set_threshold(stored)

        # Export for backup
        data = store.export_json()

        # Import from backup
        store.import_json(data)

        # Reset (operator action)
        store.reset_all()
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize store for a tenant.

        Args:
            tenant_id: tenant scope (e.g., "_default")
        """
        self.tenant_id = tenant_id
        self._lock = RLock()
        self._cache: Dict[str, StoredThreshold] = {}
        self._path = self._compute_store_path()
        self._load_from_disk()

    def get_threshold(
        self,
        task_type: str,
        subsystem: str,
        base_threshold: float = 0.5,
    ) -> float:
        """Get learned threshold, or base if not converged.

        Args:
            task_type: task classification (e.g., "code_gen")
            subsystem: subsystem name (e.g., "code_analyzer")
            base_threshold: fallback value if no learned threshold

        Returns:
            float [0.1, 0.9]
        """
        with self._lock:
            key = self._make_key(task_type, subsystem)
            stored = self._cache.get(key)

            if stored and stored.converged and stored.sample_count >= 10:
                return stored.learned_threshold

            return base_threshold

    def get_all(self) -> List[StoredThreshold]:
        """Get all stored thresholds for this tenant.

        Returns:
            List of StoredThreshold (sorted by task_type, subsystem)
        """
        with self._lock:
            return sorted(
                self._cache.values(),
                key=lambda x: (x.task_type, x.subsystem)
            )

    def get_convergence_status(
        self,
        task_type: str,
        subsystem: str,
    ) -> Tuple[bool, int]:
        """Get convergence status and sample count.

        Returns:
            (is_converged, sample_count)
        """
        with self._lock:
            key = self._make_key(task_type, subsystem)
            stored = self._cache.get(key)
            if stored:
                return stored.converged, stored.sample_count
            return False, 0

    def set_threshold(
        self,
        stored: StoredThreshold,
        audit_backend: Optional[Any] = None,
    ) -> None:
        """Store a learned threshold (audit-first).

        Args:
            stored: StoredThreshold to persist
            audit_backend: optional audit backend (for logging)

        Raises:
            ValueError: if threshold out of range
        """
        # Validate
        if not 0.1 <= stored.learned_threshold <= 0.9:
            raise ValueError(
                f"Threshold out of range [0.1, 0.9]: {stored.learned_threshold}"
            )
        if stored.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: {stored.tenant_id} != {self.tenant_id}"
            )

        with self._lock:
            key = self._make_key(stored.task_type, stored.subsystem)
            old_stored = self._cache.get(key)
            old_threshold = old_stored.learned_threshold if old_stored else stored.base_threshold

            # Audit log FIRST (fail-closed)
            if audit_backend:
                try:
                    audit_backend.write_event(
                        event_type="learned_threshold_updated",
                        tenant_id=self.tenant_id,
                        task_type=stored.task_type,
                        subsystem=stored.subsystem,
                        old_threshold=old_threshold,
                        new_threshold=stored.learned_threshold,
                        sample_count=stored.sample_count,
                        is_converged=stored.converged,
                        timestamp=stored.timestamp,
                    )
                except Exception as e:
                    logger.error(f"Audit write failed: {e}")
                    raise RuntimeError(f"Audit chain write failed: {e}")

            # Update cache + persist
            self._cache[key] = stored
            self._persist_to_disk()

            logger.info(
                f"Stored threshold: {stored.task_type}/{stored.subsystem} "
                f"{old_threshold:.3f} → {stored.learned_threshold:.3f} "
                f"(samples={stored.sample_count}, converged={stored.converged})"
            )

    def reset_all(
        self,
        audit_backend: Optional[Any] = None,
        operator_note: str = "",
    ) -> None:
        """Reset all learned thresholds for this tenant.

        Args:
            audit_backend: optional audit backend
            operator_note: reason for reset (e.g., "operator manual reset")
        """
        with self._lock:
            old_count = len(self._cache)

            # Audit log FIRST
            if audit_backend:
                try:
                    audit_backend.write_event(
                        event_type="learned_thresholds_reset",
                        tenant_id=self.tenant_id,
                        old_threshold_count=old_count,
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        reason=operator_note,
                    )
                except Exception as e:
                    logger.error(f"Audit write failed: {e}")
                    raise RuntimeError(f"Audit chain write failed: {e}")

            # Clear + persist
            self._cache.clear()
            self._persist_to_disk()

            logger.warning(
                f"Reset {old_count} learned thresholds for tenant {self.tenant_id} "
                f"(reason: {operator_note})"
            )

    def export_json(self) -> Dict[str, Any]:
        """Export thresholds as JSON dict (for backup/analysis).

        Returns:
            {
              "version": "1",
              "tenant_id": "_default",
              "export_at": "2026-09-11T12:00:00Z",
              "thresholds": [
                {task_type, subsystem, learned_threshold, ...},
                ...
              ]
            }
        """
        with self._lock:
            return {
                "version": "1",
                "tenant_id": self.tenant_id,
                "export_at": datetime.now(timezone.utc).isoformat(),
                "thresholds": [t.to_dict() for t in sorted(
                    self._cache.values(),
                    key=lambda x: (x.task_type, x.subsystem)
                )],
            }

    def import_json(
        self,
        data: Dict[str, Any],
        audit_backend: Optional[Any] = None,
        operator_note: str = "import_json",
    ) -> int:
        """Import thresholds from JSON (e.g., restore from backup).

        Args:
            data: export dict
            audit_backend: optional audit backend
            operator_note: reason for import

        Returns:
            count of imported thresholds

        Raises:
            ValueError: on version mismatch or bad data
        """
        if data.get("version") != "1":
            raise ValueError(f"Unsupported export version: {data.get('version')}")
        if data.get("tenant_id") != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: {data.get('tenant_id')} != {self.tenant_id}"
            )

        with self._lock:
            old_count = len(self._cache)

            # Parse thresholds
            thresholds = []
            for item in data.get("thresholds", []):
                try:
                    stored = StoredThreshold.from_dict(item)
                    thresholds.append(stored)
                except Exception as e:
                    logger.warning(f"Failed to parse threshold entry: {e}, skipping")

            if not thresholds:
                raise ValueError("No valid thresholds in import data")

            # Audit log FIRST
            if audit_backend:
                try:
                    audit_backend.write_event(
                        event_type="learned_thresholds_imported",
                        tenant_id=self.tenant_id,
                        old_threshold_count=old_count,
                        new_threshold_count=len(thresholds),
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        reason=operator_note,
                    )
                except Exception as e:
                    logger.error(f"Audit write failed: {e}")
                    raise RuntimeError(f"Audit chain write failed: {e}")

            # Update cache + persist
            self._cache.clear()
            for stored in thresholds:
                key = self._make_key(stored.task_type, stored.subsystem)
                self._cache[key] = stored

            self._persist_to_disk()

            logger.info(
                f"Imported {len(thresholds)} thresholds for tenant {self.tenant_id} "
                f"(old={old_count})"
            )

            return len(thresholds)

    # ── Private helpers ────────────────────────────────────────────────────

    @staticmethod
    def _make_key(task_type: str, subsystem: str) -> str:
        """Make cache key from task_type + subsystem."""
        return f"{task_type}:{subsystem}"

    def _compute_store_path(self) -> Path:
        """Compute path to store file (respects CORVIN_HOME)."""
        from core.paths import tenant_home  # noqa: PLC0415
        return (
            tenant_home(self.tenant_id)
            / "learning"
            / "learned_thresholds.json"
        )

    def _load_from_disk(self) -> None:
        """Load thresholds from disk (best-effort, graceful fallback)."""
        try:
            if not self._path.exists():
                logger.debug(f"Store file not found: {self._path}")
                return

            with open(self._path, "r") as f:
                data = json.load(f)

            if data.get("version") != "1":
                logger.warning(f"Unknown store version: {data.get('version')}")
                return

            for item in data.get("thresholds", []):
                try:
                    stored = StoredThreshold.from_dict(item)
                    if stored.tenant_id == self.tenant_id:
                        key = self._make_key(stored.task_type, stored.subsystem)
                        self._cache[key] = stored
                except Exception as e:
                    logger.warning(f"Failed to parse threshold entry: {e}, skipping")

            logger.debug(f"Loaded {len(self._cache)} thresholds from {self._path}")

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse store file (corrupt?): {e}, starting fresh")
        except Exception as e:
            logger.warning(f"Failed to load store: {e}, starting fresh")

    def _persist_to_disk(self) -> None:
        """Persist thresholds to disk (atomic rename)."""
        try:
            # Create directory if needed
            self._path.parent.mkdir(parents=True, exist_ok=True)

            # Write to temp file first (atomic)
            temp_path = self._path.with_suffix(".tmp")
            with open(temp_path, "w") as f:
                data = {
                    "version": "1",
                    "tenant_id": self.tenant_id,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                    "thresholds": {
                        self._make_key(t.task_type, t.subsystem): t.to_dict()
                        for t in self._cache.values()
                    },
                }
                json.dump(data, f, indent=2)

            # Atomic rename
            temp_path.replace(self._path)
            logger.debug(f"Persisted {len(self._cache)} thresholds to {self._path}")

        except Exception as e:
            logger.error(f"Failed to persist store: {e}")
            raise RuntimeError(f"Store persistence failed: {e}")


# Singleton instance (per-tenant)
_stores: Dict[str, LearnedThresholdStore] = {}
_stores_lock = RLock()


def get_store(tenant_id: str = "_default") -> LearnedThresholdStore:
    """Get or create store for tenant."""
    global _stores
    with _stores_lock:
        if tenant_id not in _stores:
            _stores[tenant_id] = LearnedThresholdStore(tenant_id)
        return _stores[tenant_id]


def reset_store(tenant_id: str = "_default") -> None:
    """Reset singleton for testing."""
    global _stores
    with _stores_lock:
        if tenant_id in _stores:
            del _stores[tenant_id]
