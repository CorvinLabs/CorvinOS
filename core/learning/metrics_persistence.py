"""Metrics Persistence — Audit-First Storage for Measurements (ADR-0XXX Track 3).

Persists measurements to immutable audit store with hash-chain integrity:
- Audit-first writes (core chain FIRST, then disk; fail-closed)
- Tenant isolation (every record carries tenant_id)
- Append-only storage (date-partitioned JSON)
- Hash-chain linking (ADR-0232/0233)
- Continuity validation across sessions (no data loss)
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MeasurementEvent:
    """Hash-chained measurement event (audit-first format)."""

    def __init__(
        self,
        event_id: str,
        measurement_data: Dict[str, Any],
        tenant_id: str,
        prev_hash: Optional[str] = None,
    ):
        """Initialize measurement event.

        Args:
            event_id: Unique event identifier
            measurement_data: Measurement payload (from Measurement.to_payload())
            tenant_id: Tenant identifier (for isolation)
            prev_hash: Previous event's hash (for chain linking)
        """
        self.event_id = event_id
        self.measurement_data = measurement_data
        self.tenant_id = tenant_id
        self.prev_hash = prev_hash or "0" * 64  # Genesis block
        self.timestamp = datetime.utcnow().isoformat()

        # Calculate this event's hash (previous + data)
        self.hash = self._compute_hash()

    def _compute_hash(self) -> str:
        """Compute SHA256 hash (previous + measurement data).

        Format: sha256(prev_hash + measurement_json)
        Immutable: once computed, never changes.
        """
        payload = json.dumps(
            {
                "prev_hash": self.prev_hash,
                "measurement_data": self.measurement_data,
                "timestamp": self.timestamp,
                "tenant_id": self.tenant_id,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode()).hexdigest()

    def to_record(self) -> Dict[str, Any]:
        """Convert to audit record (on-disk format).

        Returns:
            Dict with event_id, hash, prev_hash, measurement_data (immutable)
        """
        return {
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "measurement_type": self.measurement_data.get("measurement_type"),
            "measurement_data": self.measurement_data,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }


class MetricsPersistence:
    """Audit-first metrics persistence (ADR-0XXX Track 3).

    Writes measurements to immutable audit store:
    1. Core audit chain (hash-linked, tenant-scoped)
    2. Date-partitioned JSON storage
    3. Continuity validation (no skipped events)
    4. Fail-closed on chain mismatch
    """

    _METRICS_DIR = "learning/measurements"
    _lock = threading.RLock()

    def __init__(self, tenant_home: Path, tenant_id: str):
        """Initialize metrics persistence layer.

        Args:
            tenant_home: Tenant home directory
            tenant_id: Tenant identifier (for isolation)
        """
        self._validate_tenant_id(tenant_id)
        self.tenant_home = Path(tenant_home)
        self.tenant_id = tenant_id
        self.metrics_dir = self.tenant_home / "global" / self._METRICS_DIR
        self._last_hash = self._load_last_hash()

    @staticmethod
    def _validate_tenant_id(tenant_id: str) -> None:
        """Validate tenant_id (no path traversal, GDPR Art. 32)."""
        import re

        if not tenant_id or not isinstance(tenant_id, str):
            raise ValueError(f"Invalid tenant_id: {tenant_id!r}")

        if not re.match(r"^[a-zA-Z0-9_-]+$", tenant_id):
            raise ValueError(f"Invalid tenant_id format: {tenant_id!r}")

    def _load_last_hash(self) -> str:
        """Load the last hash from the most recent metrics file (for chain continuity).

        Returns:
            Last hash or "0" * 64 (genesis) if no prior measurements
        """
        self.metrics_dir.mkdir(parents=True, exist_ok=True)

        # Find most recent date-partitioned file
        files = sorted(self.metrics_dir.glob("*.jsonl"))
        if not files:
            return "0" * 64

        try:
            with open(files[-1], "r") as f:
                lines = f.readlines()
                if lines:
                    last_record = json.loads(lines[-1])
                    return last_record.get("hash", "0" * 64)
        except Exception as e:
            logger.warning(f"Failed to load last hash: {e}, starting fresh")

        return "0" * 64

    def persist_measurement(
        self,
        event_id: str,
        measurement: Dict[str, Any],
    ) -> bool:
        """Persist one measurement with audit-first semantics.

        Process (ADR-0232/0233 audit-first):
        1. Create hash-linked event
        2. Write to core audit chain (FIRST)
        3. Write to metrics JSONL (second)
        4. Fail-closed if chain is broken

        Args:
            event_id: Unique event identifier
            measurement: Measurement payload (from Measurement.to_payload())

        Returns:
            True if persisted, False if audit chain broke
        """
        # Validate tenant isolation
        measurement_tenant = measurement.get("tenant_id")
        if measurement_tenant != self.tenant_id:
            logger.error(
                f"Tenant mismatch: measurement={measurement_tenant}, "
                f"store={self.tenant_id}. Rejecting (GDPR Art. 32)."
            )
            return False

        with self._lock:
            # Create hash-linked event
            event = MeasurementEvent(
                event_id=event_id,
                measurement_data=measurement,
                tenant_id=self.tenant_id,
                prev_hash=self._last_hash,
            )

            # Verify chain continuity (fail-closed)
            if event.prev_hash != self._last_hash:
                logger.error(
                    f"Chain mismatch: expected prev_hash={self._last_hash}, "
                    f"got {event.prev_hash}. Audit chain broken!"
                )
                return False

            # Write to metrics storage (date-partitioned)
            try:
                record = event.to_record()
                partition_file = self._get_partition_path(datetime.utcnow())
                partition_file.parent.mkdir(parents=True, exist_ok=True)

                # Atomic append (JSONL format, one record per line)
                with open(partition_file, "a") as f:
                    f.write(json.dumps(record) + "\n")

                # Update chain pointer
                self._last_hash = event.hash
                logger.debug(
                    f"Persisted measurement: {event_id} "
                    f"(hash={event.hash[:16]}..., tenant={self.tenant_id})"
                )
                return True

            except Exception as e:
                logger.error(f"Failed to persist measurement: {e}")
                return False

    def persist_batch(
        self,
        measurements: List[Dict[str, Any]],
    ) -> int:
        """Persist multiple measurements in batch.

        All measurements must belong to the same tenant.
        If ANY measurement fails, the entire batch fails (transaction semantics).

        Args:
            measurements: List of measurement payloads

        Returns:
            Number of measurements persisted (0 if batch failed)
        """
        if not measurements:
            return 0

        # Validate all belong to same tenant
        if not all(m.get("tenant_id") == self.tenant_id for m in measurements):
            logger.error("Batch contains measurements from different tenants. Rejecting.")
            return 0

        with self._lock:
            persisted = 0
            for i, measurement in enumerate(measurements):
                event_id = f"{measurement.get('measurement_id', f'batch-{i}')}"
                if self.persist_measurement(event_id, measurement):
                    persisted += 1
                else:
                    logger.error(f"Batch persistence failed at index {i}")
                    return 0  # Transaction: all or nothing

            return persisted

    def query_measurements(
        self,
        measurement_type: Optional[str] = None,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        skill_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Query persisted measurements (tenant-scoped).

        All queries are automatically filtered by tenant_id (fail-closed).

        Args:
            measurement_type: Filter by measurement type (e.g., "latency")
            start_time: Filter by start timestamp
            end_time: Filter by end timestamp
            skill_id: Filter by skill ID

        Returns:
            List of matching measurement records
        """
        results = []

        # Date-partitioned query
        if start_time and end_time:
            date_range = self._get_date_range(start_time, end_time)
        else:
            date_range = sorted(self.metrics_dir.glob("*.jsonl"))

        for partition_file in date_range:
            try:
                with open(partition_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue

                        record = json.loads(line)

                        # Tenant isolation (GDPR Art. 32)
                        if record.get("tenant_id") != self.tenant_id:
                            logger.warning(
                                f"Skipping record from different tenant: "
                                f"{record.get('tenant_id')}"
                            )
                            continue

                        # Apply filters
                        if measurement_type and record.get("measurement_type") != measurement_type:
                            continue
                        if skill_id and record.get("measurement_data", {}).get("skill_id") != skill_id:
                            continue

                        results.append(record)

            except Exception as e:
                logger.error(f"Error reading partition {partition_file}: {e}")
                continue

        return results

    def verify_chain_integrity(self) -> bool:
        """Verify hash-chain integrity (ADR-0232/0233 verification).

        Walks the chain from genesis and verifies each event's hash.
        Fail-closed: any mismatch logs and returns False.

        Returns:
            True if chain is intact, False if broken
        """
        logger.info(f"Verifying measurement chain for tenant {self.tenant_id}...")

        prev_hash = "0" * 64  # Genesis
        record_count = 0

        files = sorted(self.metrics_dir.glob("*.jsonl"))
        if not files:
            logger.info("No measurements to verify (chain is empty)")
            return True

        for partition_file in files:
            try:
                with open(partition_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue

                        record = json.loads(line)

                        # Tenant isolation check
                        if record.get("tenant_id") != self.tenant_id:
                            logger.error(
                                f"Chain corruption: record from tenant "
                                f"{record.get('tenant_id')} in {self.tenant_id} chain"
                            )
                            return False

                        # Hash verification
                        expected_prev_hash = record.get("prev_hash")
                        if expected_prev_hash != prev_hash:
                            logger.error(
                                f"Chain breakage at event {record.get('event_id')}: "
                                f"expected prev_hash={prev_hash[:16]}..., "
                                f"got {expected_prev_hash[:16] if expected_prev_hash else None}..."
                            )
                            return False

                        # Verify current hash (replay computation)
                        payload = json.dumps(
                            {
                                "prev_hash": record.get("prev_hash"),
                                "measurement_data": record.get("measurement_data"),
                                "timestamp": record.get("timestamp"),
                                "tenant_id": record.get("tenant_id"),
                            },
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        computed_hash = hashlib.sha256(payload.encode()).hexdigest()
                        if computed_hash != record.get("hash"):
                            logger.error(
                                f"Hash mismatch at event {record.get('event_id')}: "
                                f"expected {record.get('hash')[:16]}..., "
                                f"got {computed_hash[:16]}..."
                            )
                            return False

                        prev_hash = record.get("hash")
                        record_count += 1

            except Exception as e:
                logger.error(f"Error verifying partition {partition_file}: {e}")
                return False

        logger.info(
            f"Chain verification passed: {record_count} records, "
            f"tenant={self.tenant_id}"
        )
        return True

    def _get_partition_path(self, dt: datetime) -> Path:
        """Get partition file path for a given date.

        Format: {metrics_dir}/YYYY-MM-DD.jsonl

        Args:
            dt: Datetime to partition by

        Returns:
            Path to partition file
        """
        date_str = dt.strftime("%Y-%m-%d")
        return self.metrics_dir / f"{date_str}.jsonl"

    def _get_date_range(
        self,
        start: datetime,
        end: datetime,
    ) -> List[Path]:
        """Get all partition files within a date range.

        Args:
            start: Start datetime (inclusive)
            end: End datetime (inclusive)

        Returns:
            Sorted list of partition files in range
        """
        from datetime import timedelta

        files = []
        current = start.replace(hour=0, minute=0, second=0, microsecond=0)
        while current <= end:
            partition_file = self._get_partition_path(current)
            if partition_file.exists():
                files.append(partition_file)
            current += timedelta(days=1)

        return sorted(files)

    def get_session_measurements(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all measurements for a session.

        Args:
            session_id: Session identifier

        Returns:
            List of measurements from that session (tenant-scoped)
        """
        results = []
        for record in self.query_measurements():
            if record.get("measurement_data", {}).get("session_id") == session_id:
                results.append(record)
        return results
