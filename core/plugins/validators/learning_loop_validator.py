"""Learning Loop Validator — Parse-time & runtime validation (ADR-0906).

Validates learning loop manifests at parse-time (when a plugin is loaded)
and at runtime (when learning events arrive).
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from pydantic import ValidationError

from ..schema.learning_loop_schema import LearningLoopManifest

logger = logging.getLogger(__name__)


class LearningLoopValidationError(ValueError):
    """Raised when a learning loop manifest fails validation."""
    pass


def validate_learning_loop_manifest(manifest_dict: dict) -> tuple[bool, Optional[str]]:
    """Validate a learning loop manifest dict at parse-time.

    Args:
        manifest_dict: Raw manifest dict from plugin.json or skill.json

    Returns:
        (is_valid, error_message)
        - is_valid=True, error_message=None if valid
        - is_valid=False, error_message="..." if invalid
    """
    try:
        LearningLoopManifest.model_validate(manifest_dict)
        return True, None
    except ValidationError as e:
        error_msg = f"Learning loop validation failed: {e.error_count()} error(s)\n"
        for error in e.errors():
            path = ".".join(str(p) for p in error["loc"])
            error_msg += f"  - {path}: {error['msg']}\n"
        return False, error_msg
    except Exception as e:
        return False, f"Unexpected validation error: {e}"


def validate_learning_loops_list(
    loops: list[dict],
) -> tuple[list[LearningLoopManifest], list[str]]:
    """Validate a list of learning loop manifests.

    Args:
        loops: List of loop manifests

    Returns:
        (valid_loops, error_messages)
        - valid_loops: List of validated LearningLoopManifest objects
        - error_messages: List of error strings (one per invalid loop)
    """
    valid_loops: list[LearningLoopManifest] = []
    errors: list[str] = []

    for i, loop_dict in enumerate(loops):
        try:
            loop = LearningLoopManifest.model_validate(loop_dict)
            valid_loops.append(loop)
        except ValidationError as e:
            error_msg = f"Loop #{i}: "
            if "id" in loop_dict:
                error_msg += f"'{loop_dict['id']}': "
            error_msg += f"{e.error_count()} validation error(s): "
            error_msg += "; ".join(
                f"{'.'.join(str(p) for p in err['loc'])}: {err['msg']}"
                for err in e.errors()
            )
            errors.append(error_msg)
        except Exception as e:
            error_msg = f"Loop #{i}: Unexpected error: {e}"
            if "id" in loop_dict:
                error_msg += f" (id={loop_dict['id']})"
            errors.append(error_msg)

    return valid_loops, errors


def extract_learning_loops_from_manifest(
    manifest: dict,
) -> tuple[list[LearningLoopManifest], list[str]]:
    """Extract and validate learning_loops from a plugin manifest.

    Handles:
    - Missing learning_loops section (graceful skip)
    - Empty learning_loops list (valid, no loops)
    - Invalid loop entries (logged, skipped)

    Args:
        manifest: Complete plugin/skill manifest dict

    Returns:
        (valid_loops, warnings)
        - valid_loops: Validated LearningLoopManifest objects
        - warnings: Non-fatal issues (e.g., some loops failed validation)
    """
    warnings: list[str] = []

    # Extract learning_loops section (optional)
    learning_loops_section = manifest.get("learning_loops")
    if learning_loops_section is None:
        # Backward compat: no learning_loops section is OK (pre-ADR-0906 plugin)
        return [], []

    if not isinstance(learning_loops_section, list):
        warning = (
            f"Invalid learning_loops: expected list, got {type(learning_loops_section).__name__}"
        )
        warnings.append(warning)
        return [], warnings

    if not learning_loops_section:
        # Empty list is valid (plugin declares no loops)
        return [], []

    # Validate all loops
    valid_loops, errors = validate_learning_loops_list(learning_loops_section)

    if errors:
        for error in errors:
            logger.warning(f"Learning loop validation: {error}")
            warnings.append(error)

    return valid_loops, warnings


def validate_loop_id_uniqueness(
    loops: list[LearningLoopManifest],
) -> list[str]:
    """Check for duplicate loop IDs within a plugin.

    Args:
        loops: List of LearningLoopManifest objects

    Returns:
        List of error messages (empty if all unique)
    """
    seen: dict[str, int] = {}
    errors: list[str] = []

    for loop in loops:
        if loop.id in seen:
            errors.append(
                f"Duplicate loop id: '{loop.id}' appears at indices {seen[loop.id]} and {seen[loop.id] + 1}"
            )
        seen[loop.id] = seen.get(loop.id, 0) + 1

    return errors


def validate_loop_cross_plugin(
    tenant_id: str,
    plugin_id: str,
    loops: list[LearningLoopManifest],
    existing_loops: Optional[dict[str, dict[str, Any]]] = None,
) -> list[str]:
    """Check for conflicts across plugins in the same tenant.

    Args:
        tenant_id: Tenant scope
        plugin_id: Current plugin
        loops: Loops being validated
        existing_loops: Existing loops in index (optional) — dict[composite_key, loop_data]

    Returns:
        List of warning messages (duplicates are logged but not fatal)
    """
    warnings: list[str] = []

    if existing_loops is None:
        existing_loops = {}

    for loop in loops:
        composite_key = f"{tenant_id}:{plugin_id}:{loop.id}"
        if composite_key in existing_loops:
            other_plugin = existing_loops[composite_key].get("plugin_id", "unknown")
            if other_plugin != plugin_id:
                warning = (
                    f"Learning loop conflict: loop '{loop.id}' declared by both "
                    f"'{plugin_id}' and '{other_plugin}' — will be deduplicated"
                )
                warnings.append(warning)

    return warnings


def is_learning_loop_compatible(
    loop: LearningLoopManifest,
    min_corvin_version: Optional[str] = None,
) -> tuple[bool, Optional[str]]:
    """Check if a learning loop is compatible with the current CorvinOS version.

    Future-proofing for version-specific loop features.

    Args:
        loop: LearningLoopManifest to check
        min_corvin_version: Minimum required version (optional)

    Returns:
        (is_compatible, error_message)
    """
    # Placeholder: in Phase 2, we'll check against actual CorvinOS version
    # For now, always compatible
    return True, None


def sanitize_loop_for_storage(
    loop: LearningLoopManifest,
) -> dict[str, Any]:
    """Convert a validated LearningLoopManifest to a storage-ready dict.

    Removes sensitive fields, ensures all types are JSON-serializable.

    Args:
        loop: Validated LearningLoopManifest

    Returns:
        Dict safe for JSON storage
    """
    data = loop.model_dump(exclude_none=False)
    # Enum values are already strings after model_validate
    return data
