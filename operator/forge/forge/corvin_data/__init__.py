"""ADR-0012 — Large-Data Snapshot Layer.

Public API for the corvin_data package: format sniffing, snapshot
generation, PII detection, redaction, pseudonymisation. The forge
MCP-server's ``data_register`` / ``data_snapshot`` tools call into
here.

Phase 12.1 ships: format_sniffer + snapshot (CSV/TSV/JSON/JSONL).
Phase 12.2 adds: pii_detector.
Phase 12.3 adds: redactor + policy loader.
Phase 12.6 adds: pseudonymize.
Phase 12.7 adds: pii_presidio (optional).

Hard cost contract — this package MUST NOT import:
  * pandas / polars (heavy deps; we work with stdlib csv + json)
  * anthropic / openai / google-cloud-* (no LLM use anywhere)
  * presidio_analyzer (lives behind an opt-in import in pii_presidio)

Parquet support requires ``duckdb`` installed in the operator's
environment; missing duckdb raises ImportError with a clear hint at
the call site, never silently falls through.
"""
from __future__ import annotations

from .format_sniffer import (
    Format,
    UnsupportedFormat,
    sniff_format,
)
from .snapshot import (
    ColumnSchema,
    ColumnStats,
    FileMeta,
    Snapshot,
    SnapshotError,
    SnapshotOptions,
    generate_snapshot,
)
from .pii_detector import (
    PII_CLASSES,
    DetectionResult,
    apply_pii_detection,
    detect_column_pii,
    detection_summary,
)
from .redactor import (
    DEFAULT_POLICY,
    STRATEGIES,
    RedactionError,
    RedactionPolicy,
    apply_redaction,
    hash_value,
    mask_partial,
    pseudonymize,
    redact,
)
from .data_policy import (
    DataPolicy,
    NoiseConfig,
    PolicyError,
    load_policy,
)
from .schema_extension import (
    DATA_KINDS,
    DataFieldSpec,
    SchemaExtensionError,
    extract_data_fields,
    snapshot_options_from_field,
    validate_data_field,
    validate_input_schema,
)
from .data_registry import (
    DataHandle,
    DataRegistry,
    HandleNotFound,
    HandleStoreError,
    compute_file_hash,
    is_handle_shape,
    new_handle,
)
from .mcp_handlers import (
    DATA_REGISTER_SCHEMA,
    DATA_SNAPSHOT_SCHEMA,
    DATA_UNREGISTER_SCHEMA,
    ToolError,
    ToolResult,
    call_data_register,
    call_data_snapshot,
    call_data_unregister,
)
from .pseudonymize import (
    PSEUDO_SEED_VAULT_KEY,
    default_vault_loader,
    derived_seed,
    resolve_seed,
)
from .strict_anonymizer import (
    apply_strict_anonymisation,
    scan_for_pii_leaks,
)
# The line above made ``pseudonymize`` a *module* attribute on this
# package, shadowing the *function* from redactor. Re-bind the
# function explicitly so users importing ``pseudonymize`` from
# ``forge.corvin_data`` get the callable. The function is what the
# public API promises (see __all__); the module remains reachable
# as ``forge.corvin_data.pseudonymize`` via attribute access on
# the package object.
from .redactor import pseudonymize  # noqa: F401  # type: ignore[no-redef]
from .pii_presidio import (
    PresidioNotInstalled,
    PresidioResult,
    detect_with_presidio,
    is_available as presidio_is_available,
)

__all__ = [
    "Format",
    "UnsupportedFormat",
    "sniff_format",
    "ColumnSchema",
    "ColumnStats",
    "FileMeta",
    "Snapshot",
    "SnapshotError",
    "SnapshotOptions",
    "generate_snapshot",
    "PII_CLASSES",
    "DetectionResult",
    "apply_pii_detection",
    "detect_column_pii",
    "detection_summary",
    "DEFAULT_POLICY",
    "STRATEGIES",
    "RedactionError",
    "RedactionPolicy",
    "apply_redaction",
    "hash_value",
    "mask_partial",
    "pseudonymize",
    "redact",
    "DataPolicy",
    "NoiseConfig",
    "PolicyError",
    "load_policy",
    "DATA_KINDS",
    "DataFieldSpec",
    "SchemaExtensionError",
    "extract_data_fields",
    "snapshot_options_from_field",
    "validate_data_field",
    "validate_input_schema",
    "DataHandle",
    "DataRegistry",
    "HandleNotFound",
    "HandleStoreError",
    "compute_file_hash",
    "is_handle_shape",
    "new_handle",
    "DATA_REGISTER_SCHEMA",
    "DATA_SNAPSHOT_SCHEMA",
    "DATA_UNREGISTER_SCHEMA",
    "ToolError",
    "ToolResult",
    "call_data_register",
    "call_data_snapshot",
    "call_data_unregister",
    "PSEUDO_SEED_VAULT_KEY",
    "default_vault_loader",
    "derived_seed",
    "resolve_seed",
    "PresidioNotInstalled",
    "PresidioResult",
    "detect_with_presidio",
    "presidio_is_available",
    "apply_strict_anonymisation",
    "scan_for_pii_leaks",
]
