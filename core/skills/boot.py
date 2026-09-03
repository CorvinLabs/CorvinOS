"""Boot-time wiring of the ACP Skills registry (the missing production call site).

Until 2026-09-03 nothing in either shipped host populated the global Skills
registry: ``get_registry()`` lazily created an EMPTY registry, so every
production consumer — the gateway health collector, the console headless check,
``/build`` (plugin builder), the vibe ``active_enabled`` flag and the
``/capabilities`` flag manifest — received ``"Skill not found"`` and fell back
to *off*. Seven Skills were unit- and "E2E"-tested and reachable from zero live
call sites: the exact defect class CLAUDE.md § E2E Wiring Proof names.

``boot_skills`` is called from :func:`corvin_plugins.bootstrap.boot_platform`
(one sequence, two hosts) right after the plugins load, with the same
``audit_emit`` those plugins receive, so Skill decisions land in the same
hash-chained audit log (GDPR Art. 30/32; CLAUDE.md § Audit Chain as Ground Truth).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .os_skills_integration import initialize_integration
from .os_skills_phase1 import BUILTIN_SKILL_IDS
from .skill_registry_phase1 import CoreAuditBackend, LearningEmitterBackend, get_registry

logger = logging.getLogger(__name__)


def _default_learning_backend(tenant_id: str) -> Optional[Any]:
    """Wire the ADR-0314 EventEmitter when a tenant home is resolvable.

    Returns None (learning stays optional) when the learning package or the
    tenant home is unavailable — never raises, the boot must not depend on it.
    """
    try:
        from forge.tenants import tenant_home  # type: ignore[import-not-found]
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore

        store = EventStore(tenant_home(tenant_id))
        return LearningEmitterBackend(EventEmitter(store), session_id="boot")
    except Exception as exc:  # noqa: BLE001
        logger.info("skills learning backend not wired (%s)", type(exc).__name__)
        return None


def boot_skills(
    tenant_id: str = "_default",
    audit_emit: Optional[Callable[[str, dict], None]] = None,
    learning_backend: Optional[Any] = None,
    wire_learning: bool = True,
) -> list[str]:
    """Populate the global Skills registry for ``tenant_id``.

    Args:
        tenant_id: The boot tenant (``forge.tenants.current_tenant()`` in hosts)
        audit_emit: ``(event_type, details)`` writer reaching the core audit chain;
            resolved from the ``audit`` module when omitted.
        learning_backend: Explicit ``emit_event(dict)`` backend; when omitted and
            ``wire_learning`` is true the ADR-0314 emitter is attached.
        wire_learning: Set False to skip the learning emitter (tests).

    Returns:
        The registered builtin Skill ids.
    """
    audit_backend = CoreAuditBackend(tenant_id=tenant_id, audit_emit=audit_emit)
    if learning_backend is None and wire_learning:
        learning_backend = _default_learning_backend(tenant_id)

    integration = initialize_integration(
        audit_backend=audit_backend,
        tenant_id=tenant_id,
        learning_backend=learning_backend,
    )
    registered = [m.id for m in integration.registry.list_skills()]
    missing = [sid for sid in BUILTIN_SKILL_IDS if sid not in registered]
    if missing:
        raise RuntimeError(f"builtin Skills missing after boot: {missing}")

    assert get_registry() is integration.registry  # one global registry, not two
    logger.info("ACP Skills booted for tenant %s: %d skills", tenant_id, len(registered))
    return registered


__all__ = ["boot_skills"]
