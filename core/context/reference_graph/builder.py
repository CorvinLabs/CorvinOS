"""CRGBuilder: builds Context Digests from references."""

import os
from typing import Callable, Optional, List

from .types import ContextDigest, ContextReference, ContextBuildError
from .validation import (
    build_digest as validate_and_build_digest,
    compute_file_sha256,
    detect_cycles_in_references,
)
from .audit import emit_digest_validated, emit_builder_error, emit_event
from .dag import validate_reference_dag


class CRGBuilder:
    """Build a Context Digest from References (fail-closed)."""

    def __init__(self, tenant_id: str = "_default"):
        self._references: List[ContextReference] = []
        self._tenant_id = tenant_id
        self._dedup_fn: Optional[Callable] = None

    def add_reference(
        self,
        file_path: str,
        *,
        load_on_demand: bool = True,
        summary: Optional[str] = None,
        tier: str = "injected",
        depends_on: Optional[List[str]] = None,
    ) -> None:
        """
        Add a reference to a context file.

        Args:
            file_path: Path to file (relative or absolute)
            load_on_demand: If True, file is resolved on-demand (not pre-loaded yet)
            summary: Human-readable summary (auto-generated if None)
            tier: "base" | "injected" | "merged" — base is immune to dedup (ADR-0564)
            depends_on: file paths this reference depends on (DAG edges, Phase 2)

        Raises:
            FileNotFoundError: If file does not exist (fail-closed)
            ValueError: If file path is invalid
        """

        if not file_path:
            raise ValueError("file_path cannot be empty")

        # Resolve relative paths
        if not os.path.isabs(file_path):
            file_path = os.path.abspath(file_path)

        # Check file exists (fail-closed)
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Reference file not found: {file_path}")

        if not os.access(file_path, os.R_OK):
            raise PermissionError(f"Reference file not readable: {file_path}")

        # Compute file size + hash
        try:
            size_bytes = os.path.getsize(file_path)
            hash_sha256 = compute_file_sha256(file_path)
        except Exception as e:
            raise ContextBuildError(
                reason="hash_computation_failed",
                reference_file=file_path,
                details=str(e)
            )

        # Auto-generate summary if not provided
        if not summary:
            summary = f"{os.path.basename(file_path)} ({size_bytes} bytes)"

        # Create reference
        ref = ContextReference(
            file_path=file_path,
            hash_sha256=hash_sha256,
            size_bytes=size_bytes,
            summary=summary,
            load_on_demand=load_on_demand,
            tier=tier,
            depends_on=tuple(os.path.abspath(d) for d in (depends_on or [])),
        )

        self._references.append(ref)

    def add_deduplicator(self, dedup_fn: Callable) -> None:
        """
        Add a deduplication function.

        The dedup function will be called during build() to remove redundant blocks.
        """
        self._dedup_fn = dedup_fn

    def build(self) -> ContextDigest:
        """
        Build a Digest from all added references.

        Pre-loads all references, validates hashes, computes completeness checksum.

        Returns:
            ContextDigest with validated references + checksums

        Raises:
            ContextBuildError: If any reference validation fails
            ValueError: If circular references detected
        """

        if not self._references:
            raise ContextBuildError(
                reason="no_references_added",
                details="CRGBuilder.add_reference() must be called at least once"
            )

        # Duplicate paths (fail-closed)
        if detect_cycles_in_references(self._references):
            raise ContextBuildError(
                reason="duplicate_reference_path",
                details="Each reference path may appear only once"
            )

        # Full DAG validation over depends_on edges (Phase 2, fail-closed)
        dag = validate_reference_dag(self._references)
        if not dag.ok:
            if dag.cycle is not None:
                err = ContextBuildError(
                    reason="circular_references_detected",
                    details=" -> ".join(dag.cycle),
                )
            else:
                src, missing = dag.dangling or ("?", "?")
                err = ContextBuildError(
                    reason="dangling_dependency",
                    reference_file=src,
                    details=f"depends on {missing}, which is not in the digest",
                )
            emit_builder_error(err, self._tenant_id)
            raise err
        emit_event(
            "context_reference_dag_validated",
            tenant_id=self._tenant_id,
            lom="core/context/reference_graph/builder.py:CRGBuilder.build",
            node_count=dag.node_count,
            edge_count=dag.edge_count,
        )

        # Validate all references + compute checksum
        try:
            digest = validate_and_build_digest(
                self._references,
                tenant_id=self._tenant_id,
                lom="core/context/reference_graph/builder.py:CRGBuilder.build"
            )
        except ContextBuildError as e:
            emit_builder_error(e, self._tenant_id)
            raise

        # Emit audit event
        emit_digest_validated(digest)

        return digest

    def reference_count(self) -> int:
        """Return number of references added."""
        return len(self._references)

    def total_size_bytes(self) -> int:
        """Return total size of all references."""
        return sum(ref.size_bytes for ref in self._references)

    def clear(self) -> None:
        """Clear all references (for testing/reuse)."""
        self._references.clear()
        self._dedup_fn = None

    def __repr__(self) -> str:
        return (
            f"CRGBuilder(references={len(self._references)}, "
            f"total_size={self.total_size_bytes()} bytes)"
        )
