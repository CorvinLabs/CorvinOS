"""On-demand reference resolution with per-request cache (ADR-0563 Tier 3, Phase 2).

Design (load-bearing):
- A loader is bound to ONE validated ``ContextDigest``. It can only resolve
  references the digest proved; arbitrary paths are refused.
- Every resolution reads the file ONCE into bytes, hashes THOSE bytes and
  returns THOSE bytes (no TOCTOU window between check and use).
- Hash or size mismatch -> ``ReferenceHashMismatchError`` (error, never a
  silent refresh / fallback) + ``context_reference_hash_mismatch`` audit event.
- The cache is per loader instance (= per request). Entries are inserted only
  after verification and are immutable ``bytes``; a lock makes hit/miss
  accounting and insertion atomic across threads.
- Every resolve emits ``context_reference_resolved`` (status ok|cache_hit|error).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Optional

from .audit import emit_event, emit_reference_hash_mismatch, emit_reference_resolved
from .dag import ReferenceGraph
from .types import ContextDigest, ContextReference
from .validation import compute_sha256

_LOM_RESOLVE = "core/context/reference_graph/loader.py:ReferenceLoader.resolve"
_LOM_DEPS = "core/context/reference_graph/loader.py:ReferenceLoader.resolve_with_dependencies"


@dataclass(frozen=True)
class ReferenceNotInDigestError(Exception):
    """The requested ref_id is not part of the bound digest (fail-closed)."""

    ref_id: str

    def __str__(self) -> str:
        return f"ReferenceNotInDigestError: {self.ref_id} is not in the digest"


@dataclass(frozen=True)
class ReferenceHashMismatchError(Exception):
    """Content on disk no longer matches the digest (fail-closed, not loaded)."""

    file_path: str
    hash_expected: str
    hash_actual: str
    reason: str = "hash_mismatch"

    def __str__(self) -> str:
        return (
            f"ReferenceHashMismatchError[{self.reason}]: {self.file_path} "
            f"expected={self.hash_expected[:12]}... actual={self.hash_actual[:12]}..."
        )


@dataclass(frozen=True)
class ReferenceUnavailableError(Exception):
    """File vanished or became unreadable after the digest was built."""

    file_path: str
    detail: str

    def __str__(self) -> str:
        return f"ReferenceUnavailableError: {self.file_path} ({self.detail})"


@dataclass(frozen=True)
class ResolvedReference:
    """Immutable resolution result."""

    reference: ContextReference
    content: bytes
    cache_hit: bool
    latency_ms: float

    def __post_init__(self) -> None:
        if len(self.content) != self.reference.size_bytes:
            raise ValueError("ResolvedReference size does not match reference (internal invariant)")


@dataclass(frozen=True)
class LoaderStats:
    hits: int
    misses: int
    errors: int
    cached_bytes: int
    max_cache_bytes: int

    @property
    def hit_ratio(self) -> float:
        total = self.hits + self.misses
        return (self.hits / total) if total else 0.0


class ReferenceLoader:
    """Resolve references of ONE digest on demand, with a per-request cache."""

    DEFAULT_MAX_CACHE_BYTES = 64 * 1024 * 1024  # 64 MiB per request

    def __init__(
        self,
        digest: ContextDigest,
        *,
        max_cache_bytes: int = DEFAULT_MAX_CACHE_BYTES,
    ) -> None:
        if not isinstance(digest, ContextDigest):
            raise TypeError("ReferenceLoader requires a validated ContextDigest")
        if max_cache_bytes < 0:
            raise ValueError("max_cache_bytes must be >= 0")
        self._digest = digest
        self._tenant_id = digest.tenant_id
        self._max_cache_bytes = max_cache_bytes
        self._by_path: dict[str, ContextReference] = {}
        self._by_hash: dict[str, ContextReference] = {}
        for ref in digest.references:
            self._by_path[ref.file_path] = ref
            # Two paths with identical content share a hash; keep the first (same bytes anyway).
            self._by_hash.setdefault(ref.hash_sha256, ref)
        self._cache: dict[str, bytes] = {}  # keyed by hash_sha256
        self._cached_bytes = 0
        self._hits = 0
        self._misses = 0
        self._errors = 0
        self._lock = threading.Lock()
        self._graph = ReferenceGraph.from_references(digest.references)

    # ------------------------------------------------------------------ lookup
    @property
    def digest(self) -> ContextDigest:
        return self._digest

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def lookup(self, ref_id: str) -> ContextReference:
        """Map a ref_id (file path, 'sha256:<hex>' or bare 64-hex) to a digest reference."""
        if not ref_id or not isinstance(ref_id, str):
            raise ReferenceNotInDigestError(ref_id=str(ref_id))
        if ref_id in self._by_path:
            return self._by_path[ref_id]
        key = ref_id[7:] if ref_id.startswith("sha256:") else ref_id
        if len(key) == 64 and key in self._by_hash:
            return self._by_hash[key]
        raise ReferenceNotInDigestError(ref_id=ref_id)

    def is_cached(self, ref_id: str) -> bool:
        ref = self.lookup(ref_id)
        with self._lock:
            return ref.hash_sha256 in self._cache

    # ----------------------------------------------------------------- resolve
    def resolve(self, ref_id: str) -> bytes:
        """Return the verified content of a digest reference (fail-closed)."""
        return self.resolve_detailed(ref_id).content

    def resolve_detailed(self, ref_id: str) -> ResolvedReference:
        start = time.perf_counter()
        try:
            ref = self.lookup(ref_id)
        except ReferenceNotInDigestError:
            with self._lock:
                self._errors += 1
            emit_event(
                "context_reference_resolved",
                tenant_id=self._tenant_id,
                lom=_LOM_RESOLVE,
                reference_file=str(ref_id),
                status="error",
                error="not_in_digest",
                latency_ms=round((time.perf_counter() - start) * 1000, 3),
            )
            raise

        # Cache hit path (atomic under lock; bytes are immutable so no copy needed)
        with self._lock:
            cached = self._cache.get(ref.hash_sha256)
            if cached is not None:
                self._hits += 1
        if cached is not None:
            latency = (time.perf_counter() - start) * 1000
            emit_reference_resolved(
                ref.file_path, ref.hash_sha256, ref.hash_sha256, "cache_hit", latency, self._tenant_id
            )
            return ResolvedReference(reference=ref, content=cached, cache_hit=True, latency_ms=latency)

        # Miss: single read, hash the bytes we will hand out
        try:
            with open(ref.file_path, "rb") as fh:
                content = fh.read()
        except OSError as exc:
            self._record_error()
            latency = (time.perf_counter() - start) * 1000
            emit_reference_resolved(ref.file_path, ref.hash_sha256, "", "error", latency, self._tenant_id)
            raise ReferenceUnavailableError(file_path=ref.file_path, detail=type(exc).__name__) from exc

        actual_hash = compute_sha256(content)
        if len(content) != ref.size_bytes or actual_hash != ref.hash_sha256:
            self._record_error()
            reason = "size_mismatch" if len(content) != ref.size_bytes else "hash_mismatch"
            latency = (time.perf_counter() - start) * 1000
            emit_reference_hash_mismatch(ref.file_path, ref.hash_sha256, actual_hash, self._tenant_id)
            emit_reference_resolved(ref.file_path, ref.hash_sha256, actual_hash, "error", latency, self._tenant_id)
            raise ReferenceHashMismatchError(
                file_path=ref.file_path,
                hash_expected=ref.hash_sha256,
                hash_actual=actual_hash,
                reason=reason,
            )

        with self._lock:
            self._misses += 1
            if ref.hash_sha256 not in self._cache:
                if self._cached_bytes + len(content) <= self._max_cache_bytes:
                    self._cache[ref.hash_sha256] = content
                    self._cached_bytes += len(content)
                # else: budget exhausted -> return verified bytes, do not cache (still correct)

        latency = (time.perf_counter() - start) * 1000
        emit_reference_resolved(ref.file_path, ref.hash_sha256, actual_hash, "ok", latency, self._tenant_id)
        return ResolvedReference(reference=ref, content=content, cache_hit=False, latency_ms=latency)

    def resolve_with_dependencies(self, ref_id: str) -> tuple[ResolvedReference, ...]:
        """Resolve a reference and its transitive ``depends_on`` closure.

        Order: dependencies first, the requested reference last. The graph is
        validated (cycles / dangling) before the first read - a cycle aborts
        with ``CycleError`` and nothing is loaded.
        """
        ref = self.lookup(ref_id)
        deps = self._graph.transitive_dependencies(ref.file_path)  # raises on cycle
        emit_event(
            "context_reference_dependencies_resolved",
            tenant_id=self._tenant_id,
            lom=_LOM_DEPS,
            reference_file=ref.file_path,
            dependency_count=len(deps),
        )
        results = [self.resolve_detailed(path) for path in deps]
        results.append(self.resolve_detailed(ref.file_path))
        return tuple(results)

    # ------------------------------------------------------------------- stats
    def _record_error(self) -> None:
        with self._lock:
            self._errors += 1

    def stats(self) -> LoaderStats:
        with self._lock:
            return LoaderStats(
                hits=self._hits,
                misses=self._misses,
                errors=self._errors,
                cached_bytes=self._cached_bytes,
                max_cache_bytes=self._max_cache_bytes,
            )

    def clear_cache(self) -> None:
        with self._lock:
            self._cache.clear()
            self._cached_bytes = 0
