"""Unified path resolver for workflow infrastructure (ADR-0423 Phase 2, Gap 3).

Single source of truth for all workflow paths. Resolves:
- CORVIN_HOME (fail-closed if unset)
- tenant_id (from arg or CORVIN_TENANT_ID env)
- workflow runs directory
- individual run paths
- checkpoint directories
- claimed file paths

Used by: checkpoint.py, claim_registry.py, completion_queue.py, bridges
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from corvinOS.shared.paths import corvin_home, tenant_home, _validate_tenant_id


class PathResolutionError(Exception):
    """Raised when path resolution fails (fail-closed)."""

    pass


def resolve_corvin_home() -> Path:
    """Resolve CORVIN_HOME with fail-closed behavior.

    Raises:
        PathResolutionError: If CORVIN_HOME is not set and cannot be resolved.
    """
    # Try env var first
    env_home = os.environ.get("CORVIN_HOME", "").strip()
    if env_home:
        return Path(os.path.expanduser(os.path.expandvars(env_home)))

    # Try shared.paths resolver (includes repo-local fallback)
    try:
        home = corvin_home()
        if home and home.parent.exists():  # Basic sanity check
            return home
    except Exception as e:
        pass

    # Fail-closed
    raise PathResolutionError(
        "CORVIN_HOME not set and could not be resolved from repo-local fallback. "
        "Set CORVIN_HOME env var explicitly."
    )


def resolve_tenant_id(tenant_id: Optional[str] = None) -> str:
    """Resolve tenant_id from arg, env, or default.

    Args:
        tenant_id: Explicit tenant ID (validated if provided)

    Returns:
        Validated tenant ID

    Raises:
        ValueError: If tenant_id format is invalid
    """
    if tenant_id is not None:
        return _validate_tenant_id(tenant_id)

    env_tenant = os.environ.get("CORVIN_TENANT_ID", "").strip()
    if env_tenant:
        return _validate_tenant_id(env_tenant)

    return "_default"


def workflow_runs_dir(tenant_id: Optional[str] = None) -> Path:
    """Get workflow runs directory for a tenant.

    Args:
        tenant_id: Tenant ID (or resolved from env/default)

    Returns:
        Path to runs directory

    Raises:
        PathResolutionError: If CORVIN_HOME cannot be resolved
        ValueError: If tenant_id is invalid
    """
    home = resolve_corvin_home()
    tenant = resolve_tenant_id(tenant_id)
    runs_dir = home / "tenants" / tenant / "workflow_runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    return runs_dir


def workflow_run_path(run_id: str, tenant_id: Optional[str] = None) -> Path:
    """Get path to a single workflow run checkpoint.

    Args:
        run_id: Unique run identifier (validated)
        tenant_id: Tenant ID (or resolved from env/default)

    Returns:
        Path to checkpoint JSON file

    Raises:
        PathResolutionError: If CORVIN_HOME cannot be resolved
        ValueError: If run_id or tenant_id is invalid
    """
    if not run_id or any(c in run_id for c in ("/", "\\", "..", "\x00")):
        raise ValueError(f"Invalid run_id: {run_id!r}")

    runs_dir = workflow_runs_dir(tenant_id)
    return runs_dir / f"{run_id}.json"


def checkpoint_dir(run_id: str, tenant_id: Optional[str] = None) -> Path:
    """Get checkpoint directory for a run (for multi-file checkpoints).

    Args:
        run_id: Unique run identifier (validated)
        tenant_id: Tenant ID (or resolved from env/default)

    Returns:
        Path to checkpoint directory

    Raises:
        PathResolutionError: If CORVIN_HOME cannot be resolved
        ValueError: If run_id or tenant_id is invalid
    """
    if not run_id or any(c in run_id for c in ("/", "\\", "..", "\x00")):
        raise ValueError(f"Invalid run_id: {run_id!r}")

    runs_dir = workflow_runs_dir(tenant_id)
    ckpt_dir = runs_dir / f"{run_id}.checkpoints"
    ckpt_dir.mkdir(parents=True, exist_ok=True)
    return ckpt_dir


def claimed_file_path(run_id: str, tenant_id: Optional[str] = None) -> Path:
    """Get path to claimed checkpoint sidecar (.json.claimed).

    Args:
        run_id: Unique run identifier (validated)
        tenant_id: Tenant ID (or resolved from env/default)

    Returns:
        Path to .json.claimed file

    Raises:
        PathResolutionError: If CORVIN_HOME cannot be resolved
        ValueError: If run_id or tenant_id is invalid
    """
    if not run_id or any(c in run_id for c in ("/", "\\", "..", "\x00")):
        raise ValueError(f"Invalid run_id: {run_id!r}")

    path = workflow_run_path(run_id, tenant_id)
    return path.with_suffix(".json.claimed")


def completion_queue_path(tenant_id: Optional[str] = None) -> Path:
    """Get path to workflow completion queue (JSONL file).

    Args:
        tenant_id: Tenant ID (or resolved from env/default)

    Returns:
        Path to completions.jsonl file

    Raises:
        PathResolutionError: If CORVIN_HOME cannot be resolved
        ValueError: If tenant_id is invalid
    """
    home = resolve_corvin_home()
    tenant = resolve_tenant_id(tenant_id)
    workflows_dir = home / "tenants" / tenant / "workflow_runs"
    workflows_dir.mkdir(parents=True, exist_ok=True)
    return workflows_dir / "completions.jsonl"
