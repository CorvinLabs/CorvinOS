"""corvin_compute.fabric.datasources — ADR-0026 Section D DataSourceAdapter System.

Public surface for the DataSource sub-system.
"""
from __future__ import annotations

from .protocol import (
    DataSourceAdapter,
    SourceConfig,
    SecretEnv,
    FilterExpr,
    SourceQuery,
    SourceSchema,
    ColumnInfo,
    SourceSession,
    DataCursor,
)
from .manifest import (
    ConnectionManifest,
    InvalidAuthMethod,
    PolicyError,
    validate_manifest,
)
from .registry import DataSourceRegistry
from .residency import DataResidencyViolation, validate_residency
from .vault_env import MissingSecret, check_vault_keys_present, get_vault_env_for_bwrap
from .watermark import (
    CheckpointNotFound,
    read_checkpoint,
    write_checkpoint,
    hash_watermark,
)
from .audit_events import DATASOURCE_AUDIT_EVENTS

__all__ = [
    # Protocol
    "DataSourceAdapter",
    "SourceConfig",
    "SecretEnv",
    "FilterExpr",
    "SourceQuery",
    "SourceSchema",
    "ColumnInfo",
    "SourceSession",
    "DataCursor",
    # Manifest
    "ConnectionManifest",
    "InvalidAuthMethod",
    "PolicyError",
    "validate_manifest",
    # Registry
    "DataSourceRegistry",
    # Residency
    "DataResidencyViolation",
    "validate_residency",
    # Vault
    "MissingSecret",
    "check_vault_keys_present",
    "get_vault_env_for_bwrap",
    # Watermark
    "CheckpointNotFound",
    "read_checkpoint",
    "write_checkpoint",
    "hash_watermark",
    # Audit
    "DATASOURCE_AUDIT_EVENTS",
]
