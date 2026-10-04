"""os.security_orchestrator — Skill 2.0 wrapper around the ADR-2031 threat
detector (L16).

SHADOW MODE ONLY (ADR-0532 Phase 1 migration). This Skill never changes
production lockout behaviour — the real PIN-elevation lockout stays
``corvin_operator/bridges/shared/auth_elevation.py`` (deterministic
``_PIN_FAIL_THRESHOLD`` / ``_PIN_LOCKOUT_S``). This wrapper runs *after*
a PIN failure is recorded, purely to emit an audited threat-detection
record so the Skill can accumulate a track record before it is ever
allowed to influence anything (same ADR-2092 G0 gate as os.flow_guard
and os.delegation_router: >=500 decisions + join >=0.95 before any
de-escalation path opens).

Do NOT wire this Skill's return value into the lockout decision.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..skill_registry_phase1 import Skill, SkillMetadata, SkillOrigin
from .security_orchestrator.security_orchestrator import ThreatDetector

logger = logging.getLogger(__name__)

_detector = ThreatDetector()


class SecurityOrchestratorSkill(Skill):
    """Skill 2.0 shadow wrapper for L16 threat detection.

    Input:
        events: List[dict] — recent auth events for this chat_key/source
            (each at minimum a timestamp; the detector in this phase only
            counts volume, ADR-2031 full pattern matching is future work).
        threat_type: str — "brute_force" (only type implemented today).

    Output:
        {
            "threat_detected": bool,
            "pattern": str | None,
            "confidence": float | None,
            "severity": str | None,
        }

    This output is NEVER consumed by the lockout decision. It exists to
    be audited and compared against the production lockout's own
    threshold-crossing so the Skill accrues a trust record (ADR-2092).
    """

    def __init__(self) -> None:
        metadata = SkillMetadata(
            id="os.security_orchestrator",
            name="Security Orchestrator (L16 shadow)",
            description="Shadow-mode threat-pattern detection, compared against the live PIN-lockout gate",
            version="1.0.0",
            origin=SkillOrigin.BUILTIN,
            owner="corvin-os-team",
            tags=["l16", "security", "shadow", "adr-2031", "adr-0532"],
            # CORE (default), not COMPLIANCE: shadow-only output, must stay
            # disableable while it accrues the ADR-2092 trust record.
        )
        super().__init__(metadata)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        events: List[Dict[str, Any]] = input.get("events") or []
        threat_type = input.get("threat_type", "brute_force")

        if threat_type != "brute_force":
            return {"threat_detected": False, "reason": f"unsupported_threat_type:{threat_type}"}

        signal = _detector.analyze_auth_events(events)
        if signal is None:
            return {"threat_detected": False, "pattern": None, "confidence": None, "severity": None}

        return {
            "threat_detected": True,
            "pattern": signal.pattern,
            "confidence": signal.confidence,
            "severity": signal.severity,
        }


def shadow_compare(
    *,
    chat_key: str,
    tenant_id: str,
    recent_failure_timestamps: List[float],
    production_lockout_triggered: bool,
) -> Optional[Dict[str, Any]]:
    """Run the Skill in shadow mode (via the audited registry) and return a
    comparison record.

    Routed through ``get_registry().execute()`` (not a direct
    ``SecurityOrchestratorSkill().execute()`` call) so the run is audited
    as ``skill.executed`` / ``skill_executed`` like every other OS-Skill
    invocation.

    Returns None when there is nothing to compare (no failure history) or
    the Skill is not registered for this tenant. Never raises — a Skill
    failure must not affect the caller's lockout decision.
    """
    if not recent_failure_timestamps:
        return None
    try:
        from ..skill_registry_phase1 import get_registry

        registry = get_registry()
        events = [{"timestamp": ts, "chat_key": chat_key} for ts in recent_failure_timestamps]
        exec_result = registry.execute(
            "os.security_orchestrator",
            {"events": events, "threat_type": "brute_force"},
            lom="corvin_operator/bridges/shared/auth_elevation.py:grant",
            tenant_id=tenant_id,
        )
    except Exception as exc:  # noqa: BLE001 — shadow path must never raise
        logger.warning("os.security_orchestrator shadow execution failed: %r", exc)
        return None

    if exec_result.status != "success":
        return None

    result = exec_result.output or {}
    skill_flagged = bool(result.get("threat_detected"))
    return {
        "skill_threat_detected": skill_flagged,
        "skill_pattern": result.get("pattern"),
        "production_lockout_triggered": production_lockout_triggered,
        "agree": skill_flagged == production_lockout_triggered,
    }
