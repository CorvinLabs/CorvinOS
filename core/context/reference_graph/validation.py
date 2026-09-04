"""Pre-validation and digest building (fail-closed)."""

import hashlib
import os
import time
from pathlib import Path
from typing import Optional

from .types import ContextDigest, ContextReference, ValidateResult, ContextBuildError


def compute_sha256(data: bytes) -> str:
    """Compute SHA256 hash of data."""
    return hashlib.sha256(data).hexdigest()


def compute_file_sha256(file_path: str) -> str:
    """Compute SHA256 hash of file content."""
    with open(file_path, 'rb') as f:
        return compute_sha256(f.read())


def validate_reference(ref: ContextReference) -> ValidateResult:
    """
    Pre-load + hash-check a single reference.

    Fails-closed: any error → ValidateResult(ok=False)
    """
    start_time = time.time()

    try:
        # Check file exists
        if not os.path.exists(ref.file_path):
            return ValidateResult(
                ok=False,
                error=f"file_not_found: {ref.file_path}",
                latency_ms=(time.time() - start_time) * 1000
            )

        # Check readable
        if not os.access(ref.file_path, os.R_OK):
            return ValidateResult(
                ok=False,
                error=f"permission_denied: {ref.file_path}",
                latency_ms=(time.time() - start_time) * 1000
            )

        # Check size matches
        actual_size = os.path.getsize(ref.file_path)
        if actual_size != ref.size_bytes:
            return ValidateResult(
                ok=False,
                error=f"size_mismatch: expected {ref.size_bytes}, got {actual_size}",
                latency_ms=(time.time() - start_time) * 1000
            )

        # Compute hash + validate
        actual_hash = compute_file_sha256(ref.file_path)
        if actual_hash != ref.hash_sha256:
            return ValidateResult(
                ok=False,
                error=f"hash_mismatch",
                hash_actual=actual_hash,
                latency_ms=(time.time() - start_time) * 1000
            )

        return ValidateResult(
            ok=True,
            latency_ms=(time.time() - start_time) * 1000
        )

    except Exception as e:
        return ValidateResult(
            ok=False,
            error=f"validation_error: {str(e)}",
            latency_ms=(time.time() - start_time) * 1000
        )


def validate_all_references(references: list[ContextReference]) -> Optional[ValidateResult]:
    """
    Validate all references. Returns first error or None if all OK.

    Fails-closed: any validation error stops the build.
    """
    for ref in references:
        result = validate_reference(ref)
        if not result.ok:
            return result
    return None


def compute_completeness_checksum(references: list[ContextReference]) -> str:
    """
    Compute checksum of all reference hashes (proof-of-what-was-included).

    This is a hash of hashes: sha256(sorted(ref.hash for ref in references))
    """
    hashes = sorted([ref.hash_sha256 for ref in references])
    data = '\n'.join(hashes).encode('utf-8')
    return compute_sha256(data)


def validate_completeness_checksum(references: list[ContextReference], checksum: str) -> bool:
    """Validate that checksum matches the references."""
    expected = compute_completeness_checksum(references)
    return expected == checksum


def build_digest(
    references: list[ContextReference],
    tenant_id: str = "_default",
    lom: str = "core/context/reference_graph/validation.py:build_digest"
) -> ContextDigest:
    """
    Build a Digest from references.

    Pre-loads all references, validates hashes, computes completeness checksum.

    Fails-closed: any validation error raises ContextBuildError.
    """

    # Validate all references
    error_result = validate_all_references(references)
    if error_result is not None:
        raise ContextBuildError(
            reason=error_result.error,
            reference_file=references[0].file_path if references else None,
            details=f"hash_actual={error_result.hash_actual}" if error_result.hash_actual else None
        )

    # Compute completeness checksum
    completeness_checksum = compute_completeness_checksum(references)

    # Create digest (immutable tuple for references)
    digest = ContextDigest(
        references=tuple(references),
        checksum_sha256=completeness_checksum,
        tenant_id=tenant_id,
        lom=lom
    )

    return digest


def detect_cycles_in_references(references: list[ContextReference]) -> bool:
    """
    Detect circular references in reference names.

    Example: A → B → A would be a cycle.

    Currently simple: checks if any reference name appears > 2x
    (full DAG analysis deferred to Phase 2).
    """
    file_paths = [ref.file_path for ref in references]
    seen = {}
    for fp in file_paths:
        seen[fp] = seen.get(fp, 0) + 1
        if seen[fp] > 1:  # Same file added twice
            return True
    return False
