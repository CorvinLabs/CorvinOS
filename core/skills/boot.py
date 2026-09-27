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

ADR-0667 (2026-09-11) claimed a 3-layer ForgeSkillValidator run at boot; it
never validated anything (see ``_validate_builtin_skills``, corrected
2026-09-27 adversarial review).
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .os_skills_integration import initialize_integration
from .os_skills_phase1 import BUILTIN_SKILL_IDS
from .skill_registry_phase1 import CoreAuditBackend, LearningEmitterBackend, get_registry

logger = logging.getLogger(__name__)


def _validate_builtin_skills(
    tenant_id: str = "_default",
    audit_emit: Optional[Callable[[str, dict], None]] = None,
) -> bool:
    """Report — honestly — that builtin Skills are NOT signature-validated.

    Returns ``False`` always: no validation is performed.

    This used to construct ``OperatorKeyManager()`` without its required
    ``corvin_home`` (TypeError, logged as "non-fatal" on every boot) and,
    had that call succeeded, would have emitted ``skill_validation_passed``
    for each builtin id without running a single check — the loop body only
    built a throw-away license object. A chained "passed" record for a check
    that never ran is fabricated evidence, so it is gone.

    Builtin Skills are Python classes inside this wheel, not signed packages;
    a signature over them would attest nothing the import did not already do
    (CLAUDE.md: the in-process plugin perimeter is attribution, not security).
    The 3-layer ForgeSkillValidator applies to INSTALLED skill packages
    (``skill_installer`` / marketplace paths), not here. One content-free
    ``skill_validation_not_performed`` record per boot says so.
    """
    logger.info(
        "Builtin Skills are in-wheel code: signature validation not performed "
        "(%d skills, tenant=%s)", len(BUILTIN_SKILL_IDS), tenant_id,
    )
    if audit_emit is not None:
        try:
            try:
                from forge.security_events import register_event_allowlist  # type: ignore[import-not-found]  # noqa: PLC0415

                register_event_allowlist(
                    "skill_validation_not_performed",
                    frozenset({"reason_code", "skill_count", "phase", "tenant_id"}),
                )
            except ImportError:
                pass
            audit_emit("skill_validation_not_performed", {
                "reason_code": "builtin_in_wheel_code",
                "skill_count": len(BUILTIN_SKILL_IDS),
                "phase": "boot",
                "tenant_id": tenant_id,
            })
        except Exception as exc:  # noqa: BLE001 — boot must not depend on it
            logger.warning("skill_validation_not_performed not chained (%s)", type(exc).__name__)
    return False


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

    Builtin Skills are NOT signature-validated (they are in-wheel code); boot
    records ``skill_validation_not_performed`` instead of claiming a check —
    see :func:`_validate_builtin_skills`.

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
    # A re-boot in the same process replaces the global registry; stop the
    # previous learning emitter so its worker thread does not linger.
    try:
        from .skill_registry_phase1 import _global_registry as _previous

        prev_lb = getattr(_previous, "learning_backend", None)
        if prev_lb is not None and hasattr(prev_lb, "emitter"):
            prev_lb.emitter.stop()
    except Exception:  # noqa: BLE001 — nothing to stop, or already stopped
        pass

    _validate_builtin_skills(tenant_id=tenant_id, audit_emit=audit_emit)

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
