"""Context-Reference-Graph data types (immutable, frozen)."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

_VALID_TIERS = frozenset({"base", "injected", "merged"})


@dataclass(frozen=True)
class ContextReference:
    """Immutable reference to a context file with cryptographic proof."""

    file_path: str
    hash_sha256: str
    size_bytes: int
    summary: str
    load_on_demand: bool = True
    created_at: datetime = field(default_factory=datetime.now)
    tier: str = "injected"  # ADR-0564: "base" is immune to deduplication
    depends_on: tuple[str, ...] = ()  # Phase 2: edges for DAG validation (file paths)

    def __post_init__(self):
        if self.tier not in _VALID_TIERS:
            raise ValueError(f"tier must be one of {sorted(_VALID_TIERS)}, got {self.tier!r}")
        if not isinstance(self.depends_on, tuple):
            raise TypeError("depends_on must be a tuple of file paths")
        if self.file_path in self.depends_on:
            raise ValueError(f"self-dependency: {self.file_path}")
        if not self.file_path:
            raise ValueError("file_path cannot be empty")
        if not self.hash_sha256:
            raise ValueError("hash_sha256 cannot be empty")
        if len(self.hash_sha256) != 64:  # SHA256 is 64 hex chars
            raise ValueError(f"Invalid SHA256 hash: {self.hash_sha256}")
        if self.size_bytes <= 0:
            raise ValueError("size_bytes must be positive")
        if not self.summary:
            raise ValueError("summary cannot be empty")


@dataclass(frozen=True)
class ContextDigest:
    """Immutable bundle of pre-validated references with completeness proof."""

    references: tuple[ContextReference, ...] = field(default_factory=tuple)
    checksum_sha256: str = ""
    tenant_id: str = "_default"
    timestamp: datetime = field(default_factory=datetime.now)
    lom: str = ""  # line-of-moral-responsibility
    dedup_metadata: Optional[dict[str, object]] = None

    def __post_init__(self):
        if not isinstance(self.references, (list, tuple)):
            raise TypeError("references must be list or tuple")
        if not self.checksum_sha256:
            raise ValueError("checksum_sha256 cannot be empty")
        if len(self.checksum_sha256) != 64:
            raise ValueError(f"Invalid SHA256 checksum: {self.checksum_sha256}")
        if not self.tenant_id:
            raise ValueError("tenant_id cannot be empty")
        if not self.lom:
            raise ValueError("lom cannot be empty")

    def reference_count(self) -> int:
        return len(self.references)

    def total_size_bytes(self) -> int:
        return sum(ref.size_bytes for ref in self.references)

    def file_paths(self) -> list[str]:
        return [ref.file_path for ref in self.references]


@dataclass(frozen=True)
class ValidateResult:
    """Result of reference validation."""

    ok: bool
    error: Optional[str] = None
    hash_actual: Optional[str] = None
    latency_ms: float = 0.0

    def __post_init__(self):
        if self.ok and self.error is not None:
            raise ValueError("Cannot have both ok=True and error set")
        if not self.ok and self.error is None:
            raise ValueError("When ok=False, error must be set")


@dataclass(frozen=True)
class ContextBuildError(Exception):
    """Error during Digest building."""

    reason: str
    reference_file: Optional[str] = None
    details: Optional[str] = None

    def __str__(self):
        msg = f"ContextBuildError: {self.reason}"
        if self.reference_file:
            msg += f" (file: {self.reference_file})"
        if self.details:
            msg += f" — {self.details}"
        return msg
