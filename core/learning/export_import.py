"""Export/Import Metrics with HMAC-SHA256 Integrity Protection (Finding #10 Mitigation).

Protects exported metrics from tampering in transit by computing HMAC-SHA256 over
the full JSON payload using tenant-specific keys. Import verification ensures
integrity before accepting any metric data.

Fail-closed design: any HMAC mismatch → rejection + audit event, no state update.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

from core.infinite_session.crypto_binding import CryptoBinding, canonical_json
from core.tenants import validate_tenant_id

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExportPayload:
    """Immutable export payload with metadata."""
    timestamp: str
    tenant_id: str
    metrics: Dict[str, Any]
    export_id: str


@dataclass(frozen=True)
class ExportedMetrics:
    """Exported metrics with HMAC signature and metadata."""
    payload: Dict[str, Any]
    payload_hmac: str
    tenant_id: str
    timestamp: str
    export_id: str


class MetricsExporter:
    """Export metrics with HMAC-SHA256 protection against tampering.

    Usage:
        exporter = MetricsExporter(corvin_home="/path/to/.corvin")
        export_data = exporter.export_metrics(
            tenant_id="_default",
            metrics={"accuracy": 0.95, "latency_ms": 42},
            export_id="export-123",
            audit_callback=audit_backend.write_audit_event
        )
        if export_data:
            json_str = json.dumps(asdict(export_data))
            # Send/store json_str
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize exporter with crypto binding.

        Args:
            corvin_home: Path to .corvin directory (defaults to ~/.corvin)
        """
        self.crypto = CryptoBinding(corvin_home=corvin_home)

    def export_metrics(
        self,
        tenant_id: str,
        metrics: Dict[str, Any],
        export_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Optional[ExportedMetrics]:
        """Export metrics with HMAC protection.

        Args:
            tenant_id: Tenant ID (validated)
            metrics: Metrics dict to export
            export_id: Unique export ID (for audit trail)
            audit_callback: Optional callback for audit events

        Returns:
            ExportedMetrics with payload_hmac, or None on failure
        """
        try:
            validate_tenant_id(tenant_id)
        except (ValueError, TypeError) as e:
            logger.error(f"Invalid tenant_id: {e}")
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_export_failed",
                    "tenant_id": "<invalid>",
                    "export_id": export_id,
                    "reason": f"Invalid tenant_id: {e}",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return None

        if not isinstance(metrics, dict):
            logger.error(f"Metrics must be a dict, got {type(metrics)}")
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_export_failed",
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "reason": "Metrics must be a dict",
                    "timestamp": datetime.utcnow().isoformat() + "Z",
                })
            return None

        timestamp = datetime.utcnow().isoformat() + "Z"

        # Build payload dict
        payload_dict = {
            "timestamp": timestamp,
            "tenant_id": tenant_id,
            "metrics": metrics,
            "export_id": export_id,
        }

        # Compute HMAC over canonical JSON
        hmac_value, error = self.crypto.hmac_bytes(
            tenant_id,
            canonical_json(payload_dict),
        )

        if error or hmac_value is None:
            logger.error(f"HMAC computation failed: {error}")
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_export_failed",
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "reason": f"HMAC computation failed: {error}",
                    "timestamp": timestamp,
                })
            return None

        exported = ExportedMetrics(
            payload=payload_dict,
            payload_hmac=hmac_value,
            tenant_id=tenant_id,
            timestamp=timestamp,
            export_id=export_id,
        )

        if audit_callback:
            audit_callback({
                "event_type": "metrics_exported",
                "tenant_id": tenant_id,
                "export_id": export_id,
                "payload_size": len(json.dumps(payload_dict)),
                "timestamp": timestamp,
            })

        return exported


class MetricsImporter:
    """Import metrics and verify HMAC-SHA256 integrity.

    Fail-closed design: any HMAC mismatch → rejection + audit event,
    state NOT updated.

    Usage:
        importer = MetricsImporter(corvin_home="/path/to/.corvin")
        import_data = json.loads(received_json_str)
        ok = importer.verify_and_import(
            tenant_id="_default",
            payload=import_data["payload"],
            payload_hmac=import_data["payload_hmac"],
            on_valid=lambda metrics: db.update_metrics(metrics),
            audit_callback=audit_backend.write_audit_event
        )
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize importer with crypto binding.

        Args:
            corvin_home: Path to .corvin directory (defaults to ~/.corvin)
        """
        self.crypto = CryptoBinding(corvin_home=corvin_home)

    def verify_and_import(
        self,
        tenant_id: str,
        payload: Dict[str, Any],
        payload_hmac: str,
        on_valid: Optional[callable] = None,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Verify HMAC and optionally import metrics.

        Fail-closed: any verification failure → rejection, no state update.

        Args:
            tenant_id: Tenant ID (validated)
            payload: Payload dict from import
            payload_hmac: HMAC-SHA256 hex string from import
            on_valid: Optional callback to invoke on valid import (receives payload)
            audit_callback: Optional callback for audit events

        Returns:
            (True, "") on success, (False, reason) on failure
        """
        timestamp = datetime.utcnow().isoformat() + "Z"
        export_id = payload.get("export_id", "<unknown>") if isinstance(payload, dict) else "<unknown>"

        # Validate tenant_id
        try:
            validate_tenant_id(tenant_id)
        except (ValueError, TypeError) as e:
            reason = f"Invalid tenant_id: {e}"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": "<invalid>",
                    "export_id": export_id,
                    "reason": reason,
                    "timestamp": timestamp,
                })
            return False, reason

        # Validate payload structure
        if not isinstance(payload, dict):
            reason = "Payload must be a dict"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "reason": reason,
                    "timestamp": timestamp,
                })
            return False, reason

        if not isinstance(payload_hmac, str) or not payload_hmac.strip():
            reason = "payload_hmac is required and must be a non-empty string"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "reason": reason,
                    "timestamp": timestamp,
                })
            return False, reason

        # Verify HMAC using constant-time comparison
        verified, error = self.crypto.verify_bytes(
            tenant_id,
            canonical_json(payload),
            payload_hmac,
        )

        if not verified or error:
            reason = f"HMAC verification failed: {error}"
            logger.error(reason)
            if audit_callback:
                audit_callback({
                    "event_type": "metrics_import_invalid",
                    "tenant_id": tenant_id,
                    "export_id": export_id,
                    "reason": reason,
                    "timestamp": timestamp,
                })
            return False, reason

        # HMAC verified; optionally invoke callback to update state
        if on_valid:
            try:
                on_valid(payload)
            except Exception as e:
                reason = f"Callback failed after valid HMAC: {e}"
                logger.error(reason)
                if audit_callback:
                    audit_callback({
                        "event_type": "metrics_import_callback_failed",
                        "tenant_id": tenant_id,
                        "export_id": export_id,
                        "reason": reason,
                        "timestamp": timestamp,
                    })
                return False, reason

        # Audit successful import
        if audit_callback:
            audit_callback({
                "event_type": "metrics_imported",
                "tenant_id": tenant_id,
                "export_id": export_id,
                "payload_size": len(json.dumps(payload)),
                "timestamp": timestamp,
            })

        return True, ""


class BulkMetricsProcessor:
    """Process multiple metrics exports/imports with integrity verification.

    Useful for batch operations, dashboard syncs, report generation.
    """

    def __init__(self, corvin_home: Optional[str | Path] = None):
        """Initialize processor.

        Args:
            corvin_home: Path to .corvin directory
        """
        self.exporter = MetricsExporter(corvin_home=corvin_home)
        self.importer = MetricsImporter(corvin_home=corvin_home)

    def export_batch(
        self,
        tenant_id: str,
        metrics_list: list[Dict[str, Any]],
        audit_callback: Optional[callable] = None,
    ) -> Tuple[list[ExportedMetrics], list[str]]:
        """Export multiple metrics with HMAC.

        Args:
            tenant_id: Tenant ID
            metrics_list: List of metrics dicts
            audit_callback: Optional audit callback

        Returns:
            (list of ExportedMetrics, list of errors)
        """
        exported = []
        errors = []

        for i, metrics in enumerate(metrics_list):
            export_id = f"batch-{i}-{datetime.utcnow().timestamp()}"
            result = self.exporter.export_metrics(
                tenant_id=tenant_id,
                metrics=metrics,
                export_id=export_id,
                audit_callback=audit_callback,
            )
            if result:
                exported.append(result)
            else:
                errors.append(f"Failed to export metrics at index {i}")

        return exported, errors

    def import_batch(
        self,
        tenant_id: str,
        exported_list: list[ExportedMetrics],
        on_valid: Optional[callable] = None,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[int, list[str]]:
        """Import multiple metrics with HMAC verification.

        Args:
            tenant_id: Tenant ID
            exported_list: List of ExportedMetrics
            on_valid: Optional callback for each valid payload
            audit_callback: Optional audit callback

        Returns:
            (number successful, list of errors)
        """
        success_count = 0
        errors = []

        for exported in exported_list:
            ok, error = self.importer.verify_and_import(
                tenant_id=tenant_id,
                payload=exported.payload,
                payload_hmac=exported.payload_hmac,
                on_valid=on_valid,
                audit_callback=audit_callback,
            )
            if ok:
                success_count += 1
            else:
                errors.append(error)

        return success_count, errors
