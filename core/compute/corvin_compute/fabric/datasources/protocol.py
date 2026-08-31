"""DataSourceAdapter protocol + core data types (ADR-0026 Section D / ADR-0106 DSI v1).

IMPORTANT: NO import anthropic, NO import openai, NO import google.cloud.aiplatform.
All values in FilterExpr are NEVER interpolated into strings — parameterized only.
"""
from __future__ import annotations

import dataclasses
import os
import socket
import time
from typing import Any, ClassVar, Iterator, Literal, Optional, Protocol, runtime_checkable


# ---------------------------------------------------------------------------
# Scalar helpers
# ---------------------------------------------------------------------------

class MissingSecret(KeyError):
    """Raised when a required secret is absent from the environment."""


class SecretEnv:
    """Thin wrapper around os.environ that provides require() semantics.

    In production the env is populated via bwrap injection from the vault.
    In tests, pass a plain dict as the backing store.
    """

    def __init__(self, store: Optional[dict[str, str]] = None) -> None:
        self._store: dict[str, str] = store if store is not None else os.environ  # type: ignore[assignment]

    def get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        return self._store.get(key, default)

    def require(self, key: str) -> str:
        val = self._store.get(key)
        if val is None:
            raise MissingSecret(f"Required secret '{key}' not found in environment")
        return val


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class SourceConfig:
    """Opaque source-level configuration: adapter name, region, and raw dict."""

    adapter: str
    region: str
    raw: dict[str, Any] = dataclasses.field(default_factory=dict)


# ---------------------------------------------------------------------------
# Query primitives
# ---------------------------------------------------------------------------

_VALID_OPS = frozenset({
    "=", "!=", "<", "<=", ">", ">=", "in", "not_in", "like", "is_null",
})


@dataclasses.dataclass
class FilterExpr:
    """A single filter predicate.

    Values are NEVER interpolated into SQL strings — always passed as
    parameterized query arguments.
    """

    col: str
    op: str  # must be one of _VALID_OPS
    value: Any = None  # None for is_null

    def __post_init__(self) -> None:
        if self.op not in _VALID_OPS:
            raise ValueError(
                f"FilterExpr.op {self.op!r} is not a recognised operator. "
                f"Valid: {sorted(_VALID_OPS)}"
            )


@dataclasses.dataclass
class SourceQuery:
    """Describes which data to fetch from a source."""

    columns: list[str] = dataclasses.field(default_factory=list)
    filters: list[FilterExpr] = dataclasses.field(default_factory=list)
    shard_index: int = 0
    n_shards: int = 1
    limit: Optional[int] = None
    order_by: Optional[str] = None


# ---------------------------------------------------------------------------
# Schema primitives
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class ColumnInfo:
    """Metadata for a single column."""

    name: str
    dtype: str
    nullable: bool = True
    pii_tagged: bool = False


@dataclasses.dataclass
class SourceSchema:
    """Discovered schema for a data source."""

    columns: list[ColumnInfo]
    estimated_row_count: Optional[int] = None
    source_format: str = "unknown"
    partitioning: Optional[list[str]] = None


# ---------------------------------------------------------------------------
# Session + cursor
# ---------------------------------------------------------------------------

class SourceSession:
    """Opaque session handle returned by DataSourceAdapter.connect().

    Sub-classes hold the live connection object; the protocol only
    guarantees that close() is idempotent.
    """

    def close(self) -> None:  # pragma: no cover
        pass


# DataCursor is just a typed alias; adapters return Iterator[dict].
DataCursor = Iterator[dict]


# ---------------------------------------------------------------------------
# Adapter Protocol
# ---------------------------------------------------------------------------

@runtime_checkable
class DataSourceAdapter(Protocol):
    """Structural protocol every DataSource adapter must satisfy.

    adapter.connect() MUST only be called inside bwrap (never in MCP
    or bridge process).  In tests, mock connect().
    """

    # Capability flags — adapters declare what they can do.
    supports_streaming: bool
    supports_pushdown: bool
    supports_schema_discovery: bool
    supports_incremental: bool

    def connect(self, config: SourceConfig, secret_env: SecretEnv) -> SourceSession:
        """Open a live connection.  Only called inside bwrap."""
        ...

    def discover_schema(
        self, session: SourceSession, config: SourceConfig
    ) -> SourceSchema:
        """Introspect column names, types, and estimated row count."""
        ...

    def create_cursor(
        self,
        session: SourceSession,
        config: SourceConfig,
        query: SourceQuery,
    ) -> DataCursor:
        """Return an iterator of dicts (one per row).

        FilterExpr values must be sent as query parameters, NEVER
        interpolated into SQL strings.
        """
        ...

    def estimate_rows(
        self, session: SourceSession, config: SourceConfig, query: SourceQuery
    ) -> Optional[int]:
        """Best-effort row estimate; may return None."""
        ...

    def close(self, session: SourceSession) -> None:
        """Release the connection."""
        ...


# ---------------------------------------------------------------------------
# DSI v1 — error hierarchy (ADR-0106)
# ---------------------------------------------------------------------------

class DSIError(Exception):
    """Base for all DSI errors."""


class DSIConnectionError(DSIError):
    """Network or auth failure in connect()."""


class DSIConfigError(DSIError):
    """Structurally invalid config — caught before connect()."""


class DSISchemaError(DSIError):
    """Schema cannot be determined."""


class DSIFetchError(DSIError):
    """I/O or query execution failure in fetch()."""


class DSIResidencyError(DSIError):
    """Raised when the data_residency gate blocks the operation."""


# ---------------------------------------------------------------------------
# DSI v1 — PingResult (ADR-0106)
# ---------------------------------------------------------------------------

@dataclasses.dataclass(frozen=True)
class PingResult:
    """Result of DataSourceAdapter.ping()."""

    ok: bool
    latency_ms: float
    detail: str = ""


# ---------------------------------------------------------------------------
# DSI v1 — SchemaColumn (additive alongside ColumnInfo; ADR-0106)
# ---------------------------------------------------------------------------

_PII_TAG_VALUES = frozenset({
    "email", "name", "phone", "address", "national_id", "financial", "custom",
})
_CARDINALITY_CLASSES = frozenset({
    "unique", "high", "medium", "low", "const",
})


@dataclasses.dataclass(frozen=True)
class SchemaColumn:
    """Column metadata for DSI v1 schema discovery.

    Additive alongside ColumnInfo — use SchemaColumn for new DSI v1 adapters.
    """

    name: str
    dtype: str
    nullable: bool
    pii_tag: Optional[str] = None
    cardinality_class: Optional[str] = None

    def __post_init__(self) -> None:
        if self.pii_tag is not None and self.pii_tag not in _PII_TAG_VALUES:
            raise ValueError(
                f"SchemaColumn.pii_tag {self.pii_tag!r} invalid. "
                f"Valid: {sorted(_PII_TAG_VALUES)}"
            )
        if self.cardinality_class is not None and self.cardinality_class not in _CARDINALITY_CLASSES:
            raise ValueError(
                f"SchemaColumn.cardinality_class {self.cardinality_class!r} invalid. "
                f"Valid: {sorted(_CARDINALITY_CLASSES)}"
            )


# ---------------------------------------------------------------------------
# DSI v1 — BaseDataSourceAdapter (ADR-0106)
#
# Adapters should extend this class to get:
#   - DSI_VERSION = "1" (validated by DataSourceRegistry.register())
#   - default ping() (no-op; override for real connectivity test)
#   - __init_subclass__ that enforces required class-level declarations
# ---------------------------------------------------------------------------

class BaseDataSourceAdapter:
    """Base class for DSI v1 adapters.

    Subclasses MUST declare the following class attributes:
        adapter_name, display_name, description, supported_formats,
        locality, network_egress, config_schema
    """

    DSI_VERSION: ClassVar[str] = "1"

    # These are declared here as ClassVars to document the requirement.
    # Subclasses must override them with concrete values.
    adapter_name: ClassVar[str]
    display_name: ClassVar[str]
    description: ClassVar[str]
    supported_formats: ClassVar[frozenset]
    # locality: "local" | "eu_cloud" | "us_cloud" | "any"
    locality: ClassVar[str]
    # network_egress: "none" | "eu" | "us" | "any"
    network_egress: ClassVar[str]
    config_schema: ClassVar[dict]

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        required = (
            "adapter_name", "display_name", "description",
            "supported_formats", "locality", "network_egress", "config_schema",
        )
        missing = [attr for attr in required if not hasattr(cls, attr)]
        if missing:
            raise TypeError(
                f"{cls.__name__} is missing required DSI v1 class attributes: "
                + ", ".join(missing)
            )

    def ping(
        self,
        timeout_s: float = 5.0,
        config: Optional[SourceConfig] = None,
    ) -> PingResult:
        """Default ping for adapters that do not implement a real test.

        Returns ``ok=False`` with an explicit "not implemented" detail so the
        console never renders a misleading green "connection OK" for an adapter
        that has not actually probed reachability. Adapters that CAN cheaply and
        safely test connectivity (local files, SQL databases, object stores)
        MUST override this with a real probe that has a timeout and returns
        ``ok=False`` with a concise, secret-free reason on failure.

        ``ping()`` runs in the console/MCP process — OUTSIDE bwrap and WITHOUT
        vault secrets. Overrides MUST therefore use only the non-secret
        ``config`` (host/port/path) and MUST NOT call the heavyweight data-path
        ``connect()`` nor read the vault. The shipped overrides perform a
        credential-free reachability probe (filesystem stat or TCP connect).
        """
        return PingResult(
            ok=False,
            latency_ms=0.0,
            detail="connectivity test not implemented for this adapter",
        )


def tcp_reachability_ping(
    host: str,
    port: int,
    timeout_s: float,
    *,
    auth_note: str = "auth not verified",
) -> PingResult:
    """Credential-free TCP reachability probe shared by network adapters.

    Opens (and immediately closes) a TCP connection to ``host:port`` with a
    hard timeout. This verifies the endpoint is reachable from the host
    running the console — it does NOT authenticate, so success is reported
    with an explicit ``auth_note`` so the UI does not over-promise. The detail
    string NEVER contains credentials; on failure only a coarse category is
    surfaced (host/port are user-supplied, non-secret config fields).
    """
    if not host:
        return PingResult(ok=False, latency_ms=0.0, detail="no host configured")
    try:
        port_int = int(port)
    except (TypeError, ValueError):
        return PingResult(ok=False, latency_ms=0.0, detail="invalid port")

    t0 = time.monotonic()
    try:
        with socket.create_connection((host, port_int), timeout=max(0.1, timeout_s)):
            pass
    except socket.timeout:
        return PingResult(ok=False, latency_ms=0.0, detail="connection timed out")
    except socket.gaierror:
        return PingResult(ok=False, latency_ms=0.0, detail="host could not be resolved")
    except (ConnectionRefusedError, OSError):
        return PingResult(ok=False, latency_ms=0.0, detail="host unreachable")

    latency = (time.monotonic() - t0) * 1000
    return PingResult(
        ok=True,
        latency_ms=latency,
        detail=f"{host}:{port_int} reachable ({auth_note})",
    )


__all__ = [
    # original ADR-0026 types
    "MissingSecret",
    "SecretEnv",
    "SourceConfig",
    "FilterExpr",
    "SourceQuery",
    "ColumnInfo",
    "SourceSchema",
    "SourceSession",
    "DataCursor",
    "DataSourceAdapter",
    "_VALID_OPS",
    # DSI v1 additions (ADR-0106)
    "DSIError",
    "DSIConnectionError",
    "DSIConfigError",
    "DSISchemaError",
    "DSIFetchError",
    "DSIResidencyError",
    "PingResult",
    "SchemaColumn",
    "BaseDataSourceAdapter",
    "tcp_reachability_ping",
]
