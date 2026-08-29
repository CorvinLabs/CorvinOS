"""JSON ↔ CSV Data Transformer Plugin (Example Plugin for CorvinOS).

This is a complete, working example plugin that demonstrates:
- Full lifecycle (on_load, on_unload, health_check)
- Custom functionality (JSON to CSV, CSV to JSON conversion)
- Configuration handling via PluginContext
- Error handling and validation

Use this as a template for building custom plugins.
"""
from __future__ import annotations

import csv
import json
import logging
from io import StringIO
from pathlib import Path
from typing import Any, Dict, List, Optional

from corvin_plugins.protocol import (
    CorvinPlugin,  # noqa: F401 - for reference
    HealthStatus,
    PluginContext,
)

log = logging.getLogger("corvin.plugins.data_transform")


class DataTransformPlugin:
    """JSON ↔ CSV data transformer plugin.

    Converts between JSON array and CSV format with validation.
    Implements the CorvinPlugin lifecycle protocol (ADR-0030).
    """

    # ── Plugin metadata ────────────────────────────────────────────────────────

    plugin_id = "data-transform-json-csv"
    plugin_type = "custom"
    version = "1.0.0"
    display_name = "JSON ↔ CSV Data Transformer"

    # ── State ──────────────────────────────────────────────────────────────────

    def __init__(self) -> None:
        """Initialize plugin state."""
        self._enabled = False
        self._config: Dict[str, Any] = {}
        self._audit_emit: Optional[Any] = None
        self._conversion_count = 0
        self._last_error: Optional[str] = None

    # ── CorvinPlugin Lifecycle (ADR-0030) ──────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        """Called once after discovery.

        Args:
            ctx: PluginContext containing config, audit emitter, etc.
        """
        self._config = ctx.config or {}
        self._audit_emit = ctx.audit_emit

        log.info(
            f"[{self.display_name}] Loading with config: {self._config}",
            extra={"plugin_id": self.plugin_id},
        )

        # Validate configuration
        self._validate_config()

        self._enabled = True
        self._last_error = None
        self._conversion_count = 0

        # Emit audit event
        if self._audit_emit:
            self._audit_emit(
                "plugin.loaded",
                {
                    "plugin_id": self.plugin_id,
                    "version": self.version,
                    "config_keys": list(self._config.keys()),
                },
            )

    def on_unload(self) -> None:
        """Called on graceful shutdown or tenant hot-reload."""
        log.info(
            f"[{self.display_name}] Unloading",
            extra={
                "plugin_id": self.plugin_id,
                "conversions": self._conversion_count,
            },
        )

        # Emit audit event
        if self._audit_emit:
            self._audit_emit(
                "plugin.unloaded",
                {
                    "plugin_id": self.plugin_id,
                    "conversions_performed": self._conversion_count,
                    "last_error": self._last_error,
                },
            )

        self._enabled = False

    def health_check(self) -> HealthStatus:
        """Called periodically by the health monitor.

        Returns HealthStatus object with status, message, and optional details.
        Must not block for more than 2 seconds.
        """
        if not self._enabled:
            return HealthStatus(
                ok=False,
                message="Plugin disabled",
                details={"enabled": False},
            )

        return HealthStatus(
            ok=True,
            message="OK",
            details={
                "enabled": self._enabled,
                "version": self.version,
                "conversions_performed": self._conversion_count,
                "last_error": self._last_error,
                "config": {
                    "max_rows": self._config.get("max_rows", 10000),
                    "strict_mode": self._config.get("strict_mode", False),
                    "encoding": self._config.get("encoding", "utf-8"),
                },
            },
        )

    # ── Plugin-specific functionality ──────────────────────────────────────────

    def json_to_csv(
        self,
        json_data: str | List[Dict[str, Any]],
        **kwargs: Any,
    ) -> str:
        """Convert JSON to CSV.

        Args:
            json_data: Either a JSON string or a list of dicts.
            **kwargs: Additional options (preserve_order, null_value, etc.)

        Returns:
            CSV-formatted string with headers.

        Raises:
            ValueError: If JSON is invalid or data is empty.
            RuntimeError: If conversion fails and strict_mode is enabled.
        """
        if not self._enabled:
            raise RuntimeError("Plugin is not enabled")

        try:
            # Parse JSON if it's a string
            if isinstance(json_data, str):
                data = json.loads(json_data)
            else:
                data = json_data

            # Validate data structure
            if not isinstance(data, list):
                raise ValueError("JSON must be an array of objects")

            if not data:
                raise ValueError("JSON array is empty")

            # Check row count against config limit
            max_rows = self._config.get("max_rows", 10000)
            if len(data) > max_rows:
                msg = f"Row count {len(data)} exceeds limit {max_rows}"
                if self._config.get("strict_mode"):
                    raise RuntimeError(msg)
                else:
                    log.warning(msg)
                    data = data[:max_rows]

            # Validate all records are dicts
            for i, record in enumerate(data):
                if not isinstance(record, dict):
                    msg = f"Record {i} is not a dict: {type(record).__name__}"
                    if self._config.get("strict_mode"):
                        raise ValueError(msg)
                    else:
                        log.warning(msg)

            # Extract headers from first record
            headers = list(data[0].keys())
            if not headers:
                raise ValueError("First JSON record has no keys")

            # Convert to CSV
            output = StringIO()
            writer = csv.DictWriter(
                output,
                fieldnames=headers,
                restval=kwargs.get("null_value", ""),
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(data)

            result = output.getvalue()

            # Update metrics
            self._conversion_count += 1
            self._last_error = None

            # Emit audit event
            if self._audit_emit:
                self._audit_emit(
                    "plugin.data_transform.json_to_csv",
                    {
                        "plugin_id": self.plugin_id,
                        "record_count": len(data),
                        "field_count": len(headers),
                        "output_bytes": len(result),
                    },
                )

            return result

        except Exception as exc:
            self._last_error = str(exc)
            if self._audit_emit:
                self._audit_emit(
                    "plugin.data_transform.error",
                    {
                        "plugin_id": self.plugin_id,
                        "operation": "json_to_csv",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            raise

    def csv_to_json(
        self,
        csv_data: str,
        **kwargs: Any,
    ) -> List[Dict[str, Any]]:
        """Convert CSV to JSON.

        Args:
            csv_data: CSV-formatted string with headers in first row.
            **kwargs: Additional options (preserve_empty, etc.)

        Returns:
            List of dicts, one per CSV row.

        Raises:
            ValueError: If CSV is invalid or empty.
            RuntimeError: If conversion fails and strict_mode is enabled.
        """
        if not self._enabled:
            raise RuntimeError("Plugin is not enabled")

        try:
            if not csv_data or not csv_data.strip():
                raise ValueError("CSV data is empty")

            # Parse CSV
            reader = csv.DictReader(StringIO(csv_data))
            data = list(reader)

            if not data:
                raise ValueError("CSV has no data rows (only headers)")

            # Check row count against config limit
            max_rows = self._config.get("max_rows", 10000)
            if len(data) > max_rows:
                msg = f"Row count {len(data)} exceeds limit {max_rows}"
                if self._config.get("strict_mode"):
                    raise RuntimeError(msg)
                else:
                    log.warning(msg)
                    data = data[:max_rows]

            # Type conversion for common patterns (optional)
            if kwargs.get("infer_types"):
                data = self._infer_types(data)

            # Update metrics
            self._conversion_count += 1
            self._last_error = None

            # Emit audit event
            if self._audit_emit:
                self._audit_emit(
                    "plugin.data_transform.csv_to_json",
                    {
                        "plugin_id": self.plugin_id,
                        "record_count": len(data),
                        "field_count": len(data[0].keys()) if data else 0,
                        "input_bytes": len(csv_data),
                    },
                )

            return data

        except Exception as exc:
            self._last_error = str(exc)
            if self._audit_emit:
                self._audit_emit(
                    "plugin.data_transform.error",
                    {
                        "plugin_id": self.plugin_id,
                        "operation": "csv_to_json",
                        "error_type": type(exc).__name__,
                        "error_message": str(exc),
                    },
                )
            raise

    def validate_csv(self, csv_data: str) -> Dict[str, Any]:
        """Validate CSV structure and return metadata.

        Args:
            csv_data: CSV-formatted string to validate.

        Returns:
            Dict with validation results (is_valid, row_count, field_count, issues).
        """
        issues: List[str] = []

        try:
            if not csv_data or not csv_data.strip():
                return {
                    "is_valid": False,
                    "row_count": 0,
                    "field_count": 0,
                    "issues": ["CSV data is empty"],
                }

            reader = csv.DictReader(StringIO(csv_data))
            rows = list(reader)

            if not rows:
                return {
                    "is_valid": False,
                    "row_count": 0,
                    "field_count": 0,
                    "issues": ["CSV has no data rows"],
                }

            field_count = len(rows[0].keys()) if rows else 0

            # Check for inconsistent field counts
            for i, row in enumerate(rows[1:], start=2):
                if len(row.keys()) != field_count:
                    issues.append(
                        f"Row {i} has {len(row.keys())} fields, expected {field_count}"
                    )

            return {
                "is_valid": len(issues) == 0,
                "row_count": len(rows),
                "field_count": field_count,
                "issues": issues,
            }

        except Exception as exc:
            return {
                "is_valid": False,
                "row_count": 0,
                "field_count": 0,
                "issues": [f"Validation error: {str(exc)}"],
            }

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _validate_config(self) -> None:
        """Validate plugin configuration."""
        max_rows = self._config.get("max_rows")
        if max_rows is not None and (not isinstance(max_rows, int) or max_rows <= 0):
            log.warning(f"Invalid max_rows: {max_rows}, using default 10000")
            self._config["max_rows"] = 10000

        encoding = self._config.get("encoding", "utf-8")
        if encoding not in ("utf-8", "utf-16", "ascii", "latin-1"):
            log.warning(f"Unsupported encoding: {encoding}, using utf-8")
            self._config["encoding"] = "utf-8"

    def _infer_types(self, data: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Attempt to infer and convert types in CSV data (numbers, booleans).

        This is a best-effort conversion: if a field looks numeric in all rows,
        convert all values to float. Same for booleans.
        """
        if not data:
            return data

        result = []
        for row in data:
            converted_row = {}
            for key, value in row.items():
                if value is None or value == "":
                    converted_row[key] = None
                    continue

                # Try boolean
                if isinstance(value, str):
                    if value.lower() in ("true", "yes", "1"):
                        converted_row[key] = True
                    elif value.lower() in ("false", "no", "0"):
                        converted_row[key] = False
                    # Try number
                    elif value.replace(".", "", 1).replace("-", "", 1).isdigit():
                        try:
                            if "." in value:
                                converted_row[key] = float(value)
                            else:
                                converted_row[key] = int(value)
                        except (ValueError, AttributeError):
                            converted_row[key] = value
                    else:
                        converted_row[key] = value
                else:
                    converted_row[key] = value

            result.append(converted_row)

        return result
