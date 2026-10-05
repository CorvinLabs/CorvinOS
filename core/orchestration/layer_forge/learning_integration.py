"""Learning loop integration for Layer Forge (ADR-0314, M3 Phase 2).

Emits learning events (confidence_score + outcome_feedback) for Layer Forge
definitions. These events feed the decision loop: confidence changes based on
gate/enforcement results, and transitions/rejections emit outcome signals that
close the loop.

Gates/Enforcement verdicts → confidence_score events (P(correct decision | input))
- PASS gate: confidence += 0.1 (strong positive signal)
- FAIL gate: confidence -= 0.2 (negative signal)
- SKIPPED enforcement: confidence += 0.05 (neutral/acceptable)
- FAIL enforcement: confidence -= 0.3 (strong negative signal)

Transitions/Rejections → outcome_feedback events
- transition → deployed: positive outcome, high confidence in full chain
- transition → accepted: neutral/proposed outcome, learning continues
- rejection (any phase): negative outcome, chain failed at that phase
"""

from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# The Layer Forge Skill (container for Layer Forge decisions)
LAYER_FORGE_SKILL_ID = "os.layer_forge"


def learning_emitter() -> Optional:
    """Get the booted ACP registry's learning emitter, or None."""
    try:
        from core.skills import skill_registry_phase1 as _reg  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — stripped install
        return None
    registry = getattr(_reg, "_global_registry", None)
    backend = getattr(registry, "learning_backend", None) if registry is not None else None
    return getattr(backend, "emitter", None)


def emit_gate_confidence(
    *,
    tenant_id: str,
    entry_id: str,
    version: str,
    gate_id: str,
    status: str,
    emitter: Optional = None,
) -> bool:
    """Emit confidence score based on quality gate result.

    Args:
        tenant_id: Tenant scope
        entry_id: Layer definition ID
        version: Layer definition version
        gate_id: Quality gate identifier
        status: Gate status (PASS | FAIL | ERROR | SKIPPED)
        emitter: Explicit EventEmitter (tests); default is booted registry's

    Returns:
        True when the event was queued
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("gate confidence dropped: no tenant_id (entry %s)", entry_id)
        return False

    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("gate confidence dropped: no learning emitter booted (entry %s)", entry_id)
        return False

    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        # Map gate status to confidence delta
        confidence_map = {
            "PASS": 0.15,      # Strong positive signal: gate passed
            "FAIL": -0.25,     # Strong negative signal: gate failed
            "ERROR": -0.20,    # Negative signal: gate errored
            "SKIPPED": 0.05,   # Neutral signal: gate was skipped (acceptable)
        }
        confidence_delta = confidence_map.get(status, 0.0)

        signal: dict = {
            "entry_id": entry_id,
            "version": version,
            "gate_id": gate_id,
            "gate_status": status,
            "confidence_delta": confidence_delta,
            "decision_type": "quality_gate",
        }

        event = LearningEvent.create(
            event_type=EventType.CONFIDENCE,
            skill_id=LAYER_FORGE_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/orchestration/layer_forge/learning_integration.py:emit_gate_confidence",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning("gate confidence not recorded (%s/%s): %s", entry_id, gate_id, type(exc).__name__)
        return False


def emit_enforcement_confidence(
    *,
    tenant_id: str,
    entry_id: str,
    version: str,
    rule_id: str,
    status: str,
    emitter: Optional = None,
) -> bool:
    """Emit confidence score based on enforcement rule result.

    Args:
        tenant_id: Tenant scope
        entry_id: Layer definition ID
        version: Layer definition version
        rule_id: Enforcement rule identifier
        status: Rule status (PASS | FAIL | ERROR | SKIPPED)
        emitter: Explicit EventEmitter (tests); default is booted registry's

    Returns:
        True when the event was queued
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("enforcement confidence dropped: no tenant_id (entry %s)", entry_id)
        return False

    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("enforcement confidence dropped: no emitter (entry %s)", entry_id)
        return False

    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        # Map enforcement status to confidence delta
        confidence_map = {
            "PASS": 0.12,      # Positive signal: enforcement rule passed
            "FAIL": -0.30,     # Strong negative signal: enforcement failed (breaks safety)
            "ERROR": -0.25,    # Negative signal: enforcement errored
            "SKIPPED": 0.08,   # Neutral signal: enforcement was skipped (acceptable)
        }
        confidence_delta = confidence_map.get(status, 0.0)

        signal: dict = {
            "entry_id": entry_id,
            "version": version,
            "rule_id": rule_id,
            "rule_status": status,
            "confidence_delta": confidence_delta,
            "decision_type": "enforcement_rule",
        }

        event = LearningEvent.create(
            event_type=EventType.CONFIDENCE,
            skill_id=LAYER_FORGE_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/orchestration/layer_forge/learning_integration.py:emit_enforcement_confidence",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "enforcement confidence not recorded (%s/%s): %s", entry_id, rule_id, type(exc).__name__
        )
        return False


def emit_transition_outcome(
    *,
    tenant_id: str,
    entry_id: str,
    version: str,
    from_status: str,
    to_status: str,
    emitter: Optional = None,
) -> bool:
    """Emit outcome feedback on definition transition (positive outcome).

    A transition represents progress through the layer forge lifecycle:
    - proposed → accepted: definition passed initial checks
    - accepted → deployed: definition is live (strongest positive outcome)
    - deployed → retired: definition's life cycle ended

    Args:
        tenant_id: Tenant scope
        entry_id: Layer definition ID
        version: Layer definition version
        from_status: Current status
        to_status: New status
        emitter: Explicit EventEmitter (tests); default is booted registry's

    Returns:
        True when the event was queued
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("transition outcome dropped: no tenant_id (entry %s)", entry_id)
        return False

    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("transition outcome dropped: no emitter (entry %s)", entry_id)
        return False

    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        # Map transitions to outcome signals
        # Deployed = strongest positive (definition is live and validated)
        # Accepted = moderate positive (passed gates + enforcement)
        success_map = {
            ("proposed", "accepted"): True,     # Passed gates/enforcement
            ("accepted", "deployed"): True,     # Now live and running
            ("deployed", "retired"): True,      # Clean lifecycle end
        }
        is_success = success_map.get((from_status, to_status), False)

        signal: dict = {
            "entry_id": entry_id,
            "version": version,
            "from_status": from_status,
            "to_status": to_status,
            "success": is_success,
            "outcome_type": "status_transition",
            "confidence_boost": 0.20 if to_status == "deployed" else 0.10,
        }

        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=LAYER_FORGE_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/orchestration/layer_forge/learning_integration.py:emit_transition_outcome",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning("transition outcome not recorded (%s/%s): %s", entry_id, version, type(exc).__name__)
        return False


def emit_rejection_outcome(
    *,
    tenant_id: str,
    entry_id: str,
    version: str,
    phase: str,
    error: str,
    emitter: Optional = None,
) -> bool:
    """Emit outcome feedback on definition rejection (negative outcome).

    A rejection at any phase indicates the chain failed at that phase.
    The phase indicates WHERE in the pipeline the failure occurred:
    - validate: schema/dependency check failed
    - test: quality gate(s) failed
    - enforce: enforcement rule(s) failed
    - audit: audit chain write failed (system error)

    Args:
        tenant_id: Tenant scope
        entry_id: Layer definition ID
        version: Layer definition version
        phase: Where rejection occurred (validate | test | enforce | audit | plan)
        error: Human-readable error message (NOT stored in learning event for privacy)
        emitter: Explicit EventEmitter (tests); default is booted registry's

    Returns:
        True when the event was queued
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("rejection outcome dropped: no tenant_id (entry %s)", entry_id)
        return False

    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("rejection outcome dropped: no emitter (entry %s)", entry_id)
        return False

    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        # Phase confidence penalty (where in the pipeline did it fail?)
        phase_penalty_map = {
            "validate": -0.15,    # Early rejection (schema/dep check)
            "test": -0.25,        # Moderate rejection (gates failed)
            "enforce": -0.30,     # Strong rejection (safety/compliance failed)
            "audit": -0.20,       # System-level error
            "plan": -0.10,        # LLM planning failed
        }
        confidence_delta = phase_penalty_map.get(phase, -0.20)

        signal: dict = {
            "entry_id": entry_id,
            "version": version,
            "phase": phase,
            "success": False,
            "outcome_type": "rejection",
            "confidence_delta": confidence_delta,
            # NOTE: error string NOT stored for GDPR (content-free by construction)
        }

        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=LAYER_FORGE_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/orchestration/layer_forge/learning_integration.py:emit_rejection_outcome",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning("rejection outcome not recorded (%s/%s): %s", entry_id, version, type(exc).__name__)
        return False


__all__ = [
    "emit_gate_confidence",
    "emit_enforcement_confidence",
    "emit_transition_outcome",
    "emit_rejection_outcome",
    "learning_emitter",
    "LAYER_FORGE_SKILL_ID",
]
