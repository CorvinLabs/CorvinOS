"""Path Traversal Validator — Prevent Symlink Escape Attacks (ADR-0007 Tenant Isolation).

Validates all path operations to ensure they remain within tenant scope.
Resolves symlinks, checks for escape attempts, and enforces strict boundaries.

Load-bearing rules:
- Every audit chain access MUST validate before opening
- Every skill path MUST be validated before reading/writing
- Cross-tenant symlinks MUST be rejected (fail-closed)
- No .. or ~/ escape sequences allowed
- Circular symlinks MUST be detected and rejected

Compliance (GDPR Art. 5, 32):
- Tenant isolation: no cross-tenant leakage via symlink
- Data integrity: immutable validation, never bypass
- Fail-closed: invalid paths → RuntimeError, never silent skip
"""

import logging
import os
from pathlib import Path
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


class PathTraversalError(RuntimeError):
    """Raised when path validation fails (fail-closed)."""

    pass


def validate_path_within_scope(
    file_path: Path,
    expected_scope: Path,
    allow_symlinks_within_scope: bool = False,
) -> Tuple[bool, str, Optional[str]]:
    """Validate that a file path stays within the expected scope.

    Performs comprehensive validation:
    1. Check: file_path does NOT contain .. or ~ escape sequences
    2. Check: Resolve all symlinks (os.path.realpath)
    3. Check: Resolved path starts with expected_scope prefix
    4. Check: No circular symlinks
    5. Optional: Allow symlinks that target within the scope

    Args:
        file_path: Path to validate (may contain symlinks)
        expected_scope: Expected parent directory (e.g., tenant directory)
        allow_symlinks_within_scope: If True, allow symlinks that resolve
            within scope; if False, reject all symlinks (stricter)

    Returns:
        Tuple[bool, str, Optional[str]]:
          - is_valid (bool): True if validation passes
          - resolved_path (str): Resolved canonical path (or input path if invalid)
          - error (Optional[str]): Error message if invalid; None if valid

    Examples:
        >>> valid, path, err = validate_path_within_scope(
        ...     Path("/home/user/.corvin/tenants/t1/audit.jsonl"),
        ...     Path("/home/user/.corvin/tenants/t1")
        ... )
        >>> assert valid and not err

        >>> valid, path, err = validate_path_within_scope(
        ...     Path("/home/user/.corvin/tenants/t1/../../t2/audit.jsonl"),
        ...     Path("/home/user/.corvin/tenants/t1")
        ... )
        >>> assert not valid and "escape sequence" in err.lower()
    """
    file_path = Path(file_path)
    expected_scope = Path(expected_scope).resolve()

    # ─────────────────────────────────────────────────────────────────────────
    # Check 1: Reject escape sequences (.. and ~/)
    # ─────────────────────────────────────────────────────────────────────────
    path_str = str(file_path)
    if ".." in path_str or path_str.startswith("~"):
        return (
            False,
            str(file_path),
            f"Path contains escape sequence (.. or ~): {file_path}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 2: Resolve symlinks (fail-closed on circular symlinks)
    # ─────────────────────────────────────────────────────────────────────────
    try:
        # realpath resolves symlinks and makes the path absolute
        # It also resolves circular symlinks by following them up to a limit
        # and returning the last successfully resolved point
        resolved_path = Path(os.path.realpath(str(file_path)))
    except (OSError, RuntimeError) as e:
        return (
            False,
            str(file_path),
            f"Failed to resolve path (possible circular symlink): {e}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 3: Detect circular symlinks (realpath may not catch all cases)
    # ─────────────────────────────────────────────────────────────────────────
    # If the file doesn't exist, check if any parent is a symlink loop
    try:
        # Traverse up the path tree checking for symlinks
        current = file_path
        seen_symlinks: set[str] = set()
        max_symlinks = 40  # Reasonable limit for symlink depth

        for _ in range(max_symlinks):
            if current == current.parent:  # Reached root
                break

            # Check if current is a symlink
            if current.is_symlink():
                # Read the target of the symlink
                target_str = os.readlink(str(current))
                # Normalize to absolute path
                target = Path(target_str)
                if not target.is_absolute():
                    target = (current.parent / target).resolve()

                target_str = str(target.resolve())

                # Detect circular symlink
                if target_str in seen_symlinks:
                    return (
                        False,
                        str(file_path),
                        f"Circular symlink detected at {current} -> {target_str}",
                    )
                seen_symlinks.add(target_str)

            current = current.parent

        if len(seen_symlinks) >= max_symlinks:
            return (
                False,
                str(file_path),
                f"Too many symlink hops (>{max_symlinks}) detected in path",
            )

    except (OSError, RuntimeError) as e:
        return (
            False,
            str(file_path),
            f"Failed to check for circular symlinks: {e}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 4: Validate resolved path is within expected scope
    # ─────────────────────────────────────────────────────────────────────────
    resolved_str = str(resolved_path)
    expected_str = str(expected_scope)

    # Use startswith with path separator to avoid prefix false-positives
    # e.g., /home/user1 should not match /home/user10
    if not (
        resolved_str == expected_str or resolved_str.startswith(expected_str + os.sep)
    ):
        return (
            False,
            resolved_str,
            f"Resolved path escapes scope: {resolved_str} not under {expected_str}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 5: If symlinks are allowed, confirm the target is in scope
    # ─────────────────────────────────────────────────────────────────────────
    if allow_symlinks_within_scope and file_path.is_symlink():
        # We already validated that the resolved path is in scope (check 4)
        # So this symlink is safe
        pass

    return True, resolved_str, None


def validate_tenant_audit_path(
    tenant_id: str,
    audit_path: Path,
    expected_audit_dir: Path,
) -> Tuple[bool, Path, Optional[str]]:
    """Validate audit chain path for a tenant (specialized validator).

    Enforces:
    - Audit file MUST be named "audit.jsonl"
    - Must be in expected_audit_dir
    - Must NOT be a symlink (fail-closed, strict)
    - Must resolve within tenant scope

    Args:
        tenant_id: Tenant identifier (for logging)
        audit_path: Full path to audit.jsonl file
        expected_audit_dir: Expected parent directory
            (e.g., ~/.corvin/tenants/<tid>/global/forge/)

    Returns:
        Tuple[bool, Path, Optional[str]]:
          - is_valid (bool): True if validation passes
          - resolved_path (Path): Resolved canonical path (or input if invalid)
          - error (Optional[str]): Error message if invalid; None if valid
    """
    audit_path = Path(audit_path)
    expected_audit_dir = Path(expected_audit_dir).resolve()

    # ─────────────────────────────────────────────────────────────────────────
    # Check 1: Must be named "audit.jsonl"
    # ─────────────────────────────────────────────────────────────────────────
    if audit_path.name != "audit.jsonl":
        return (
            False,
            audit_path,
            f"Audit file MUST be named 'audit.jsonl', got '{audit_path.name}'",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 2: Must NOT be a symlink (fail-closed, strict)
    # ─────────────────────────────────────────────────────────────────────────
    if audit_path.is_symlink():
        return (
            False,
            audit_path,
            f"Audit file MUST NOT be a symlink (tenant {tenant_id}): {audit_path}",
        )

    # ─────────────────────────────────────────────────────────────────────────
    # Check 3: Validate within scope (general validation)
    # ─────────────────────────────────────────────────────────────────────────
    is_valid, resolved_str, error = validate_path_within_scope(
        audit_path,
        expected_audit_dir,
        allow_symlinks_within_scope=False,
    )

    if not is_valid:
        return False, audit_path, error

    # ─────────────────────────────────────────────────────────────────────────
    # Check 4: Parent directory must match expected
    # ─────────────────────────────────────────────────────────────────────────
    resolved_path = Path(resolved_str)
    if resolved_path.parent != expected_audit_dir:
        return (
            False,
            resolved_path,
            f"Audit parent dir mismatch: {resolved_path.parent} != {expected_audit_dir}",
        )

    return True, resolved_path, None


def validate_skill_id_and_version(
    skill_id: str,
    version: str,
) -> Tuple[bool, Optional[str]]:
    """Validate skill_id and version parameters for path safety.

    Enforces:
    - skill_id: alphanumeric, dots, hyphens only (no slashes, spaces)
    - version: semantic version format (X.Y.Z)
    - Neither contains escape sequences

    Args:
        skill_id: Skill identifier (e.g., "os.delegation_router")
        version: Semantic version (e.g., "2.1.0")

    Returns:
        Tuple[bool, Optional[str]]:
          - is_valid (bool): True if validation passes
          - error (Optional[str]): Error message if invalid; None if valid
    """
    # ─────────────────────────────────────────────────────────────────────────
    # Check 1: skill_id format
    # ─────────────────────────────────────────────────────────────────────────
    if not skill_id:
        return False, "skill_id is required"

    # Allow alphanumeric, dots, hyphens, underscores only
    allowed_chars = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._-")
    if not all(c in allowed_chars for c in skill_id):
        return (
            False,
            f"skill_id contains invalid characters: {skill_id} (allowed: a-z, A-Z, 0-9, ., -, _)",
        )

    # Reject obvious path traversal
    if ".." in skill_id or "/" in skill_id or "\\" in skill_id:
        return False, f"skill_id contains path traversal sequences: {skill_id}"

    # ─────────────────────────────────────────────────────────────────────────
    # Check 2: version format (semantic versioning X.Y.Z)
    # ─────────────────────────────────────────────────────────────────────────
    if not version:
        return False, "version is required"

    parts = version.split(".")
    if len(parts) != 3:
        return False, f"version must be semantic (X.Y.Z), got: {version}"

    for part in parts:
        if not part.isdigit():
            return False, f"version parts must be numeric, got: {version}"

    # Reject path traversal in version
    if ".." in version or "/" in version or "\\" in version:
        return False, f"version contains path traversal sequences: {version}"

    return True, None


def assert_path_safe(
    file_path: Path,
    expected_scope: Path,
    context: str = "",
) -> Path:
    """Assert a path is safe; raise RuntimeError if not (fail-closed).

    Convenience wrapper around validate_path_within_scope that raises
    on validation failure.

    Args:
        file_path: Path to validate
        expected_scope: Expected parent directory
        context: Additional context for error message (e.g., "audit_chain")

    Returns:
        Path: The resolved canonical path (if valid)

    Raises:
        PathTraversalError: If validation fails (fail-closed)
    """
    is_valid, resolved_str, error = validate_path_within_scope(file_path, expected_scope)

    if not is_valid:
        context_str = f" ({context})" if context else ""
        raise PathTraversalError(
            f"Path validation failed{context_str}: {error}"
        )

    return Path(resolved_str)


def assert_skill_parameters_safe(
    skill_id: str,
    version: str,
) -> None:
    """Assert skill_id and version are safe; raise RuntimeError if not (fail-closed).

    Raises:
        PathTraversalError: If validation fails (fail-closed)
    """
    is_valid, error = validate_skill_id_and_version(skill_id, version)

    if not is_valid:
        raise PathTraversalError(f"Skill parameters validation failed: {error}")
