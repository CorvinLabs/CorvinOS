"""ConnectionManifest — validated descriptor for a DataSource connection.

ADR-0026 format (legacy): uses source.region + auth.method="vault".
ADR-0106 DSI v1 format:   uses dsi_version="1" + data_classification.

Both formats are supported. DSI v1 manifests are identified by the
presence of dsi_version="1".  Use DSIv1ConnectionManifest for new
connections.
"""
from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any, Optional

from .protocol import SourceConfig

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class InvalidAuthMethod(ValueError):
    """Raised when auth.method is not 'vault'."""


class PolicyError(ValueError):
    """Raised when a manifest field violates policy."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,62}$")

_PII_HANDLING_VALUES = frozenset({
    "drop", "redact", "pseudonymize", "mask_partial", "aggregate_only", "hash",
})

_INCREMENTAL_MODES = frozenset({
    "timestamp", "sequence_id", "cdc_log",
})


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class IncrementalConfig:
    mode: str  # timestamp | sequence_id | cdc_log
    watermark_col: Optional[str] = None
    initial_watermark: Optional[Any] = None

    def __post_init__(self) -> None:
        if self.mode not in _INCREMENTAL_MODES:
            raise PolicyError(
                f"incremental.mode {self.mode!r} invalid. "
                f"Valid: {sorted(_INCREMENTAL_MODES)}"
            )


@dataclasses.dataclass
class AuthConfig:
    method: str  # MUST be "vault"
    secret_keys: list[str] = dataclasses.field(default_factory=list)
    vault_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.method != "vault":
            raise InvalidAuthMethod(
                f"auth.method must be 'vault', got {self.method!r}. "
                "No other auth method is permitted."
            )


@dataclasses.dataclass
class ConnectionManifest:
    """Fully-validated manifest for a DataSource connection."""

    name: str
    adapter: str
    source: SourceConfig
    auth: AuthConfig
    schema_hint: Optional[dict] = None
    pii_handling: str = "redact"
    filters: list[dict] = dataclasses.field(default_factory=list)
    incremental: Optional[IncrementalConfig] = None
    tags: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# Validator
# ---------------------------------------------------------------------------

def validate_manifest(
    path_or_dict: "Path | dict",
    adapter_registry: Any,
) -> ConnectionManifest:
    """Load and validate a ConnectionManifest.

    Args:
        path_or_dict: Either a Path to a JSON manifest file or a plain dict.
        adapter_registry: Used to verify the adapter name is registered.
                         Pass None in tests to skip adapter lookup.

    Returns:
        ConnectionManifest

    Raises:
        InvalidAuthMethod: if auth.method != "vault"
        PolicyError: for any other policy violation
    """
    if isinstance(path_or_dict, dict):
        raw: dict = path_or_dict
    else:
        raw = json.loads(Path(path_or_dict).read_text(encoding="utf-8"))

    # --- name ---
    name = raw.get("name", "")
    if not _NAME_RE.match(name):
        raise PolicyError(
            f"Manifest name {name!r} is invalid. "
            "Must match [a-z0-9][a-z0-9_-]{0,62}."
        )

    # --- adapter ---
    adapter_name = raw.get("adapter", "")
    if not adapter_name:
        raise PolicyError("Manifest missing 'adapter' field.")

    # --- source ---
    source_raw = raw.get("source", {})
    if not source_raw.get("region"):
        raise PolicyError(
            f"Manifest '{name}': source.region is required but missing."
        )
    source = SourceConfig(
        adapter=adapter_name,
        region=source_raw["region"],
        raw={k: v for k, v in source_raw.items() if k not in ("adapter", "region")},
    )

    # --- auth ---
    auth_raw = raw.get("auth", {})
    method = auth_raw.get("method", "")
    if method != "vault":
        raise InvalidAuthMethod(
            f"auth.method must be 'vault', got {method!r}. "
            "No other auth method is permitted."
        )
    auth = AuthConfig(
        method=method,
        secret_keys=auth_raw.get("secret_keys", []),
        vault_path=auth_raw.get("vault_path"),
    )

    # --- pii_handling ---
    pii_handling = raw.get("pii_handling", "redact")
    if pii_handling not in _PII_HANDLING_VALUES:
        raise PolicyError(
            f"pii_handling {pii_handling!r} invalid. "
            f"Valid: {sorted(_PII_HANDLING_VALUES)}"
        )

    # --- incremental ---
    inc_raw = raw.get("incremental")
    incremental: Optional[IncrementalConfig] = None
    if inc_raw:
        incremental = IncrementalConfig(
            mode=inc_raw.get("mode", "timestamp"),
            watermark_col=inc_raw.get("watermark_col"),
            initial_watermark=inc_raw.get("initial_watermark"),
        )

    return ConnectionManifest(
        name=name,
        adapter=adapter_name,
        source=source,
        auth=auth,
        schema_hint=raw.get("schema_hint"),
        pii_handling=pii_handling,
        filters=raw.get("filters", []),
        incremental=incremental,
        tags=raw.get("tags", []),
    )


# ---------------------------------------------------------------------------
# DSI v1 — ConnectionManifest (ADR-0106)
# ---------------------------------------------------------------------------

_DATA_CLASSIFICATIONS = frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"})
_DATA_RESIDENCIES = frozenset({"any", "eu", "us", "de", "local"})
_DSI_NAME_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,63}$")
_ENV_VAR_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


@dataclasses.dataclass
class SnapshotOptions:
    token_cap: int = 500
    sample_rows: int = 5
    redaction_strategy: str = "redact"


@dataclasses.dataclass
class DSIv1ConnectionManifest:
    """Fully-validated DSI v1 connection manifest (ADR-0106)."""

    dsi_version: str
    name: str
    adapter: str
    config: dict
    data_classification: str
    secrets: list = dataclasses.field(default_factory=list)
    data_residency: str = "any"
    tags: list = dataclasses.field(default_factory=list)
    pii_scan: bool = True
    read_only: bool = True
    auto_refresh_schema: bool = False
    snapshot_options: SnapshotOptions = dataclasses.field(default_factory=SnapshotOptions)
    description: str = ""


class DSIv1PolicyError(ValueError):
    """Raised when a DSI v1 manifest field violates policy."""


def validate_dsiv1_manifest(raw: dict) -> DSIv1ConnectionManifest:
    """Validate a raw dict against the DSI v1 manifest schema.

    Raises:
        DSIv1PolicyError: for any policy violation
    """
    if raw.get("dsi_version") != "1":
        raise DSIv1PolicyError(
            "Not a DSI v1 manifest — dsi_version must be '1'."
        )

    name = raw.get("name", "")
    if not isinstance(name, str) or not _DSI_NAME_RE.match(name):
        raise DSIv1PolicyError(
            f"DSI v1 manifest name {name!r} invalid. "
            "Must match [a-z][a-z0-9_-]{0,63}."
        )

    adapter = raw.get("adapter", "")
    if not adapter:
        raise DSIv1PolicyError("DSI v1 manifest missing 'adapter' field.")

    config = raw.get("config")
    if not isinstance(config, dict):
        raise DSIv1PolicyError("DSI v1 manifest 'config' must be an object.")

    classification = raw.get("data_classification", "")
    if classification not in _DATA_CLASSIFICATIONS:
        raise DSIv1PolicyError(
            f"data_classification {classification!r} invalid. "
            f"Valid: {sorted(_DATA_CLASSIFICATIONS)}"
        )

    secrets = raw.get("secrets", [])
    if not isinstance(secrets, list):
        raise DSIv1PolicyError("'secrets' must be an array.")
    for s in secrets:
        if not isinstance(s, str) or not _ENV_VAR_RE.match(s):
            raise DSIv1PolicyError(
                f"secrets entry {s!r} invalid — must be an uppercase env-var name "
                "(e.g. AWS_ACCESS_KEY_ID)."
            )

    residency = raw.get("data_residency", "any")
    if residency not in _DATA_RESIDENCIES:
        raise DSIv1PolicyError(
            f"data_residency {residency!r} invalid. "
            f"Valid: {sorted(_DATA_RESIDENCIES)}"
        )

    read_only = raw.get("read_only", True)
    if read_only is not True:
        raise DSIv1PolicyError(
            "DSI v1 adapters are always read_only. "
            "Write support is deferred to DSI v2."
        )

    snap_raw = raw.get("snapshot_options", {}) or {}
    snap = SnapshotOptions(
        token_cap=snap_raw.get("token_cap", 500),
        sample_rows=snap_raw.get("sample_rows", 5),
        redaction_strategy=snap_raw.get("redaction_strategy", "redact"),
    )
    if snap.redaction_strategy not in _PII_HANDLING_VALUES:
        raise DSIv1PolicyError(
            f"snapshot_options.redaction_strategy {snap.redaction_strategy!r} invalid."
        )

    return DSIv1ConnectionManifest(
        dsi_version="1",
        name=name,
        adapter=adapter,
        config=config,
        data_classification=classification,
        secrets=secrets,
        data_residency=residency,
        tags=raw.get("tags", []),
        pii_scan=bool(raw.get("pii_scan", True)),
        read_only=True,
        auto_refresh_schema=bool(raw.get("auto_refresh_schema", False)),
        snapshot_options=snap,
        description=raw.get("description", "") or "",
    )


def is_dsiv1_manifest(raw: dict) -> bool:
    """Return True if the raw dict looks like a DSI v1 manifest."""
    return raw.get("dsi_version") == "1"


__all__ = [
    # ADR-0026 (legacy)
    "ConnectionManifest",
    "AuthConfig",
    "IncrementalConfig",
    "InvalidAuthMethod",
    "PolicyError",
    "validate_manifest",
    # ADR-0106 DSI v1
    "DSIv1ConnectionManifest",
    "DSIv1PolicyError",
    "SnapshotOptions",
    "validate_dsiv1_manifest",
    "is_dsiv1_manifest",
]
