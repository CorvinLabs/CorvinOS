"""DataSource audit event allow-list (ADR-0026 Section D).

Every event emitted by the DataSource sub-system must appear here.
Raw watermark values NEVER enter any event — use hash_watermark(value)[:8].
Credential values NEVER enter any event — only key names.
"""
from __future__ import annotations

DATASOURCE_AUDIT_EVENTS: dict[str, set[str]] = {
    "datasource.registered": {
        "name",
        "adapter",
        "region",
        "auth_secret_key_names",   # list of key NAMES, never values
        "pii_columns_detected",
        "estimated_rows",
    },
    "datasource.schema_refreshed": {
        "name",
        "adapter",
        "columns",
        "pii_tagged_columns",
    },
    "datasource.connection_tested": {
        "name",
        "adapter",
        "latency_ms",
        "ok",
    },
    "datasource.connection_failed": {
        "name",
        "adapter",
        "error_class",             # curated class name only, no detail string
    },
    "datasource.watermark_advanced": {
        "name",
        "previous_watermark_hash", # sha256[:8], never raw value
        "new_watermark_hash",      # sha256[:8], never raw value
        "rows_read",
    },
    "datasource.residency_violation": {
        "datasource_name",
        "declared_region",
        "tenant_zone",
    },
    "datasource.pii_detected": {
        "name",
        "pii_class_counts",        # {class_name: count} — no column values
    },
    "datasource.adapter_enabled": {
        "tenant_id",
        "adapter_name",
        "adapter_version",
    },
    "datasource.adapter_disabled": {
        "tenant_id",
        "adapter_name",
    },
    "datasource.preview_generated": {
        "name",
        "n_rows_requested",
        "n_rows_returned",         # always ≤ 20
        "pii_columns_redacted",
    },
    "datasource.unregistered": {
        "name",
        "adapter",
        "had_checkpoint",
    },
}

__all__ = ["DATASOURCE_AUDIT_EVENTS"]
