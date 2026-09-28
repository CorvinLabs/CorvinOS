"""Audit-chain integration for AWPKG.

Writes through forge.security_events.write_event() — the one chain writer.
No standalone fallback: its records lacked the keyed MAC, so a mixed log
failed verify_chain (see ``_core_write_event``).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def _audit_path(tenant_id: str = "_default") -> Path:
    """Get tenant-scoped audit trail file path.

    Args:
        tenant_id: Tenant identifier (default: "_default")

    Returns:
        Path to tenant audit.jsonl file

    Raises:
        ValueError: If tenant_id is invalid
    """
    # Explicit env override (for migration/testing)
    env = os.environ.get("VOICE_AUDIT_PATH") or os.environ.get("FORGE_AUDIT_PATH")
    if env:
        return Path(env)

    # THE tenant chain: <corvin_home>/tenants/<tid>/global/forge/audit.jsonl
    try:
        from core.paths import tenant_audit_chain
        return tenant_audit_chain(tenant_id)
    except (ImportError, ValueError):
        # Fallback for bootstrap or core.paths unavailable
        pass

    # Bootstrap fallback, before core.paths can be imported. R4: the tail is
    # ``global/forge/audit.jsonl`` — THE tenant chain — not ``<tenant>/audit.jsonl``,
    # which nothing verifies and no compliance report covers.
    _tail = ("tenants", tenant_id, "global", "forge", "audit.jsonl")
    corvin_home = os.environ.get("CORVIN_HOME")
    if corvin_home:
        return Path(corvin_home).joinpath(*_tail)
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            candidate = parent / ".corvin"
            return candidate.joinpath(*_tail)
    return Path.home().joinpath(".corvin", *_tail)


#: ``core/awpkg/awpkg/audit.py`` → parents[3] is the repo root; the core writer
#: lives at ``<repo>/corvin_operator/forge/forge``. (This pointed at
#: ``<repo>/forge`` — nonexistent — so wherever ``forge`` was not already
#: importable every record went through a private standalone writer.)
_FORGE_DIR = Path(__file__).resolve().parents[3] / "corvin_operator" / "forge"


class AwpkgAuditUnavailable(RuntimeError):
    """The core chain writer could not record an awpkg event."""


def _core_write_event():
    """``forge.security_events.write_event`` of this checkout, or raise.

    There is deliberately NO standalone fallback any more: it wrote records
    without the keyed ``mac`` (ADR-0137 M2) into THE tenant chain, and one such
    record after a keyed one makes ``verify_chain`` fail (``mac_missing``) —
    i.e. the ADR-0232 boot tripwire refuses to boot. A second chain format is
    not a fallback, it is chain corruption.
    """
    if _FORGE_DIR.is_dir() and str(_FORGE_DIR) not in sys.path:
        sys.path.insert(0, str(_FORGE_DIR))
    bound = sys.modules.get("forge")
    if bound is not None and getattr(bound, "__file__", None) is None \
            and (_FORGE_DIR / "forge" / "__init__.py").is_file():
        # Empty namespace binding (``corvin_operator/`` on sys.path): it can
        # never resolve ``forge.security_events``; drop it.
        del sys.modules["forge"]
    try:
        from forge.security_events import write_event  # type: ignore[import]
    except Exception as exc:  # noqa: BLE001
        raise AwpkgAuditUnavailable(
            f"core audit writer not importable: {type(exc).__name__}") from exc
    return write_event


def emit(event_type: str, *, tenant_id: str = "_default", **details: Any) -> None:
    """Emit an audit event into the tenant-scoped audit hash chain.

    Args:
        event_type: Type of audit event (e.g., "package.installed")
        tenant_id: Tenant identifier (default: "_default", keyword-only)
        **details: Additional event details (arbitrary key-value pairs)

    Raises:
        ValueError: If tenant_id is invalid
        AwpkgAuditUnavailable: If the core writer cannot be resolved — the
            caller's action is not recorded, so it must not proceed silently.
    """
    write_event = _core_write_event()
    write_event(_audit_path(tenant_id), event_type, details=details)
