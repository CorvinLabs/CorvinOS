"""Tenant-scoped path construction API (Phase A: Tenant-Native Data Persistence).

Central API for constructing paths to tenant-specific resources. Every function
validates tenant_id (and session_id for session-scoped paths) to ensure fail-closed
safety against path traversal and cross-tenant data access.

This is the single source of truth for tenant path resolution. All subsystems
must use these functions rather than constructing paths directly.

GDPR Art. 5 (integrity) + ADR-0007 (multi-tenant axis).
"""

import os
from pathlib import Path

from core.tenants import (
    validate_channel_id,
    validate_session_id,
    validate_tenant_id,
)


def corvin_home() -> Path:
    """The runtime root: ``$CORVIN_HOME``, else ``~/.corvin`` (CLAUDE.md § Project Identity).

    Mirrors ``operator/bridges/shared/paths.py::corvin_home`` — the canonical
    resolver. Until 2026-09-06 this module hard-wired ``~/.corvin`` and ignored
    ``CORVIN_HOME`` entirely, so every learning/skills path built here (the
    learning event dirs, the per-tenant audit file, skill configs) pointed at
    the operator's HOME while the live services ran on a different root
    (``CORVIN_HOME=<repo>/.corvin``): learning data and audit joins silently
    landed outside the live install (adversarial review F25).
    """
    root = os.environ.get("CORVIN_HOME", "").strip()
    if root:
        return Path(os.path.expandvars(root)).expanduser()
    repo_local = Path(__file__).resolve().parents[2] / ".corvin"
    if repo_local.is_dir():  # source checkout: the repo-local root is the live one
        return repo_local
    return Path.home() / ".corvin"


def tenant_home(tenant_id: str) -> Path:
    """Construct tenant home directory path.

    Returns: <corvin_home>/tenants/<tenant_id>/

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant home directory

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return corvin_home() / "tenants" / tenant_id


def tenant_skill_dir(tenant_id: str) -> Path:
    """Construct tenant skill-forge directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/skill-forge/skills/

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant skills directory

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "skill-forge" / "skills"


def tenant_tool_dir(tenant_id: str) -> Path:
    """Construct tenant forge (tools) directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/forge/tools/

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant tools directory

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "forge" / "tools"


def tenant_session_dir(tenant_id: str, session_id: str) -> Path:
    """Construct tenant session directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/sessions/<session_id>/

    Args:
        tenant_id: Tenant identifier (validated)
        session_id: Session identifier (validated)

    Returns:
        Path to session directory

    Raises:
        ValueError: If tenant_id or session_id is invalid
    """
    validate_tenant_id(tenant_id)
    validate_session_id(session_id)
    return tenant_home(tenant_id) / "sessions" / session_id


def tenant_learning_dir(tenant_id: str) -> Path:
    """Construct tenant learning directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/learning/

    Learning events, decision history, outcome feedback, style preferences,
    attention budgets, and metrics are stored here (ADR-0314+).

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant learning directory

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "learning"


def tenant_memory_dir(tenant_id: str) -> Path:
    """Construct tenant memory directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/memory/

    Conversation recall, user modeling, session memory, and other persistent
    memory artifacts are stored here.

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant memory directory

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "memory"


def tenant_audit_file(tenant_id: str) -> Path:
    """Construct tenant audit trail file path.

    Returns: ~/.corvin/tenants/<tenant_id>/audit.jsonl

    The audit trail is hash-chained and immutable. Each tenant has its own
    audit file to ensure data isolation (GDPR Art. 30, 32).

    Args:
        tenant_id: Tenant identifier (validated)

    Returns:
        Path to tenant audit trail file

    Raises:
        ValueError: If tenant_id is invalid
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "audit.jsonl"


def tenant_bridge_dir(tenant_id: str, channel: str) -> Path:
    """Construct tenant bridge (messenger channel) directory path.

    Returns: ~/.corvin/tenants/<tenant_id>/bridges/<channel>/

    Bridges (Discord, Slack, Telegram, etc.) have isolated directories per
    channel to keep session and conversation state separate.

    Args:
        tenant_id: Tenant identifier (validated)
        channel: Channel/bridge identifier (validated, e.g., "discord", "slack")

    Returns:
        Path to bridge directory

    Raises:
        ValueError: If tenant_id or channel is invalid
    """
    validate_tenant_id(tenant_id)
    validate_channel_id(channel)
    return tenant_home(tenant_id) / "bridges" / channel


__all__ = [
    "tenant_home",
    "tenant_skill_dir",
    "tenant_tool_dir",
    "tenant_session_dir",
    "tenant_learning_dir",
    "tenant_memory_dir",
    "tenant_audit_file",
    "tenant_bridge_dir",
]
