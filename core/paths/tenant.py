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


AUDIT_CHAIN_NAME = "audit.jsonl"


def tenant_audit_chain(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tid>/global/forge/audit.jsonl`` — THE audit chain.

    Every writer of a hash-chained audit record for *tenant_id* resolves here.
    This is the file the ADR-0232 boot tripwire verifies, that
    ``corvin_compliance_reports.audit_query`` reads, and that every compliance
    report is generated from — so a record written anywhere else is, from the
    operator's and the auditor's point of view, not in the audit trail at all.

    Byte-identical mirror of ``operator/forge/forge/paths.py::tenant_audit_chain``
    and ``operator/bridges/shared/paths.py::tenant_audit_chain`` (the established
    three-copy paths.py pattern; core/ cannot import forge/ at every call site
    and the bridge daemons do not have core/ on sys.path). The guard test
    ``tests/security/test_audit_chain_ssot.py`` fails if the three diverge.

    R4 (2026-09-07): before this existed, ``security_events.write_event`` took
    its path from the caller and every caller composed its own — six live chain
    files for one tenant across two roots, none a symlink of another. See the
    forge copy for the measured breakdown.
    """
    validate_tenant_id(tenant_id)
    return tenant_home(tenant_id) / "global" / "forge" / AUDIT_CHAIN_NAME


def legacy_audit_chains(tenant_id: str) -> dict[str, Path]:
    """``{label: path}`` for every NON-canonical chain location ever written.

    Read-only by contract — nothing may resolve a WRITE here. Used by the boot
    tripwire to name a live split and by the seam recorder to point at what the
    canonical chain superseded.
    """
    validate_tenant_id(tenant_id)
    root = corvin_home()
    tenant = tenant_home(tenant_id)
    return {
        "host_global_forge": root / "global" / "forge" / AUDIT_CHAIN_NAME,
        "host_forge":        root / "forge" / AUDIT_CHAIN_NAME,
        "tenant_global":     tenant / "global" / AUDIT_CHAIN_NAME,
        "tenant_forge":      tenant / "forge" / AUDIT_CHAIN_NAME,
        "tenant_root":       tenant / AUDIT_CHAIN_NAME,
    }


def all_audit_chains(tenant_id: str) -> dict[str, Path]:
    """``{"canonical": ..., **legacy}`` — every chain location this host knows."""
    return {"canonical": tenant_audit_chain(tenant_id), **legacy_audit_chains(tenant_id)}


def tenant_audit_file(tenant_id: str) -> Path:
    """DEPRECATED alias for :func:`tenant_audit_chain`.

    Until 2026-09-07 this returned ``<tenant_home>/audit.jsonl`` and its
    docstring called that "the tenant audit trail file". It was not: nothing
    verifies that path, the boot tripwire does not read it, and no compliance
    report covers it. Its callers (``core/awpkg``, ``core/learning``,
    ``core/orchestration``) were therefore writing GDPR Art. 30 records into a
    seventh file that no auditor would ever open — 757 ``skill.create`` records
    sat there on the maintainer install. It now resolves to the real chain.

    Kept as a name so the existing call sites keep working; new code calls
    :func:`tenant_audit_chain`.
    """
    return tenant_audit_chain(tenant_id)


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
    "corvin_home",
    "tenant_home",
    "tenant_audit_chain",
    "legacy_audit_chains",
    "all_audit_chains",
    "tenant_skill_dir",
    "tenant_tool_dir",
    "tenant_session_dir",
    "tenant_learning_dir",
    "tenant_memory_dir",
    "tenant_audit_file",
    "tenant_bridge_dir",
]
