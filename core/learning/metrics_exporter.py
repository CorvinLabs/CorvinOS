"""Metrics Exporter with HMAC-SHA256 Tamper Protection (Fix #10).

Provides high-level metrics export functionality with built-in HMAC-SHA256
integrity protection. Prevents tampering in transit and ensures fail-closed
design on any verification failure.

Attack vectors mitigated:
  - Metric value modification
  - Export ID forgery
  - Tenant ID substitution
  - Cross-tenant injection
  - HMAC corruption

All exports include a tenant-scoped HMAC-SHA256 signature over the canonical
JSON payload. Import verification is mandatory (fail-closed) before any
metric state is updated.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Callable, List, Tuple

from core.learning.export_import import (
    MetricsExporter as BaseMetricsExporter,
    MetricsImporter as BaseMetricsImporter,
    ExportedMetrics,
    canonical_json,
)
from core.tenants import validate_tenant_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MetricsSnapshot:
    """Immutable snapshot of exported metrics with metadata."""
    tenant_id: str
    export_id: str
    timestamp: str
    metrics: Dict[str, Any]
    payload_hmac: str
    signature_algorithm: str = "hmac-sha256"

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            "tenant_id": self.tenant_id,
            "export_id": self.export_id,
            "timestamp": self.timestamp,
            "metrics": self.metrics,
            "payload_hmac": self.payload_hmac,
            "signature_algorithm": self.signature_algorithm,
        }

    @classmethod
    def from_exported_metrics(cls, exported: ExportedMetrics) -> MetricsSnapshot:
        """Create from ExportedMetrics object."""
        return cls(
            tenant_id=exported.tenant_id,
            export_id=exported.export_id,
            timestamp=exported.timestamp,
            metrics=exported.payload.get("metrics", {}),
            payload_hmac=exported.payload_hmac,
        )


class MetricsExporter:
    """High-level metrics exporter with HMAC-SHA256 protection.

    Provides a clean API for exporting metrics with automatic HMAC computation
    and signature inclusion. All exports are tenant-scoped and fail-closed.

    Example:
        exporter = MetricsExporter(corvin_home="/path/to/.corvin")
        snapshot = exporter.export(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="export-123"
        )
        if snapshot:
            json_bytes = json.dumps(snapshot.to_dict())
            # Send/store json_bytes safely
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize exporter with crypto binding.

        Args:
            corvin_home: Path to .corvin directory (defaults to ~/.corvin)
        """
        self.base_exporter = BaseMetricsExporter(corvin_home=corvin_home)
        self.corvin_home = corvin_home

    def export(
        self,
        tenant_id: str,
        metrics: Dict[str, Any],
        export_id: str,
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Optional[MetricsSnapshot]:
        """Export metrics with HMAC-SHA256 signature.

        Args:
            tenant_id: Tenant ID (validated)
            metrics: Metrics dict to export
            export_id: Unique export ID (for audit trail)
            audit_callback: Optional callback for audit events

        Returns:
            MetricsSnapshot with payload_hmac, or None on failure
        """
        try:
            validate_tenant_id(tenant_id)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid tenant_id in export: {e}")
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_export_failed",
                    "reason": f"Invalid tenant_id: {e}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return None

        if not isinstance(metrics, dict):
            logger.error(f"Metrics must be dict, got {type(metrics).__name__}")
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_export_failed",
                    "tenant_id": tenant_id,
                    "reason": "Metrics must be a dict",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return None

        # Use base exporter
        exported = self.base_exporter.export_metrics(
            tenant_id=tenant_id,
            metrics=metrics,
            export_id=export_id,
            audit_callback=audit_callback,
        )

        if exported is None:
            return None

        return MetricsSnapshot.from_exported_metrics(exported)

    def export_batch(
        self,
        tenant_id: str,
        metrics_list: List[Dict[str, Any]],
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[List[MetricsSnapshot], List[str]]:
        """Export multiple metrics with HMAC protection.

        Args:
            tenant_id: Tenant ID
            metrics_list: List of metrics dicts to export
            audit_callback: Optional audit callback

        Returns:
            (list of MetricsSnapshot, list of error messages)
        """
        snapshots = []
        errors = []

        for i, metrics in enumerate(metrics_list):
            export_id = f"batch-{i}-{datetime.utcnow().timestamp()}"
            snapshot = self.export(
                tenant_id=tenant_id,
                metrics=metrics,
                export_id=export_id,
                audit_callback=audit_callback,
            )
            if snapshot:
                snapshots.append(snapshot)
            else:
                errors.append(f"Failed to export metrics at index {i}")

        return snapshots, errors


class MetricsImporter:
    """High-level metrics importer with HMAC-SHA256 verification.

    Ensures fail-closed design: any HMAC mismatch results in immediate
    rejection and audit logging. State is never updated on verification failure.

    Example:
        importer = MetricsImporter(corvin_home="/path/to/.corvin")
        snapshot_dict = json.loads(received_json)
        ok, error = importer.verify_and_import(
            tenant_id="_default",
            snapshot_dict=snapshot_dict,
            on_valid=lambda metrics: db.update(metrics)
        )
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize importer with crypto binding.

        Args:
            corvin_home: Path to .corvin directory (defaults to ~/.corvin)
        """
        self.base_importer = BaseMetricsImporter(corvin_home=corvin_home)
        self.corvin_home = corvin_home

    def verify_and_import(
        self,
        tenant_id: str,
        snapshot_dict: Dict[str, Any],
        on_valid: Optional[Callable[[Dict[str, Any]], None]] = None,
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[bool, str]:
        """Verify HMAC and optionally import metrics from a snapshot dict.

        Fail-closed: any verification failure results in rejection.

        Args:
            tenant_id: Tenant ID (validated)
            snapshot_dict: Snapshot dict from JSON (must have payload_hmac)
            on_valid: Optional callback to invoke on valid import
            audit_callback: Optional callback for audit events

        Returns:
            (True, "") on success, (False, reason) on failure
        """
        try:
            validate_tenant_id(tenant_id)
        except (ValueError, TypeError) as e:
            reason = f"Invalid tenant_id: {e}"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return False, reason

        if not isinstance(snapshot_dict, dict):
            reason = "Snapshot must be a dict"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": tenant_id,
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return False, reason

        # Extract payload and HMAC from snapshot
        payload_hmac = snapshot_dict.get("payload_hmac")
        if not payload_hmac or not isinstance(payload_hmac, str):
            reason = "payload_hmac is required and must be a non-empty string"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": tenant_id,
                    "reason": reason,
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return False, reason

        # Reconstruct payload from snapshot
        payload = {
            "timestamp": snapshot_dict.get("timestamp"),
            "tenant_id": snapshot_dict.get("tenant_id"),
            "metrics": snapshot_dict.get("metrics", {}),
            "export_id": snapshot_dict.get("export_id"),
        }

        # Delegate to base importer for verification
        def wrapped_on_valid(base_payload):
            """Wrapper that extracts metrics before calling user callback."""
            if on_valid:
                on_valid(base_payload.get("metrics", {}))

        return self.base_importer.verify_and_import(
            tenant_id=tenant_id,
            payload=payload,
            payload_hmac=payload_hmac,
            on_valid=wrapped_on_valid if on_valid else None,
            audit_callback=audit_callback,
        )

    def verify_and_import_batch(
        self,
        tenant_id: str,
        snapshot_dicts: List[Dict[str, Any]],
        on_valid: Optional[Callable[[Dict[str, Any]], None]] = None,
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[int, List[str]]:
        """Verify and import multiple metrics snapshots.

        Args:
            tenant_id: Tenant ID
            snapshot_dicts: List of snapshot dicts
            on_valid: Optional callback for each valid import
            audit_callback: Optional audit callback

        Returns:
            (number successful, list of error messages)
        """
        success_count = 0
        errors = []

        for snapshot_dict in snapshot_dicts:
            ok, error = self.verify_and_import(
                tenant_id=tenant_id,
                snapshot_dict=snapshot_dict,
                on_valid=on_valid,
                audit_callback=audit_callback,
            )
            if ok:
                success_count += 1
            else:
                errors.append(error)

        return success_count, errors


class MetricsIntegrityValidator:
    """Standalone HMAC-SHA256 validation without state updates.

    Useful for audit verification, compliance checking, and tamper detection.
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize validator with crypto binding.

        Args:
            corvin_home: Path to .corvin directory
        """
        self.importer = MetricsImporter(corvin_home=corvin_home)

    def validate_snapshot(
        self,
        tenant_id: str,
        snapshot_dict: Dict[str, Any],
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[bool, str]:
        """Validate a metrics snapshot without updating state.

        Args:
            tenant_id: Tenant ID
            snapshot_dict: Snapshot dict to validate
            audit_callback: Optional audit callback

        Returns:
            (True, "") on valid, (False, reason) on invalid
        """
        # No state update, just verification
        return self.importer.verify_and_import(
            tenant_id=tenant_id,
            snapshot_dict=snapshot_dict,
            on_valid=None,  # Don't update state
            audit_callback=audit_callback,
        )

    def validate_batch(
        self,
        tenant_id: str,
        snapshot_dicts: List[Dict[str, Any]],
        audit_callback: Optional[Callable[[Dict[str, Any]], None]] = None,
    ) -> Tuple[int, List[str]]:
        """Validate multiple snapshots without updating state.

        Args:
            tenant_id: Tenant ID
            snapshot_dicts: List of snapshot dicts to validate
            audit_callback: Optional audit callback

        Returns:
            (number valid, list of invalid reasons)
        """
        valid_count = 0
        invalid = []

        for snapshot_dict in snapshot_dicts:
            ok, error = self.validate_snapshot(
                tenant_id=tenant_id,
                snapshot_dict=snapshot_dict,
                audit_callback=audit_callback,
            )
            if ok:
                valid_count += 1
            else:
                invalid.append(error)

        return valid_count, invalid
