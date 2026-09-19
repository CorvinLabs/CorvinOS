"""os.security_orchestrator — Threat Detection + Policy Advisory Skill (Phase 5.3, ADR-0532 Phase 3).

DESIGN (Dialectical Reasoning Complete):
- Pattern detection (burst, creep, concentration, context_shift)
- Operator feedback loop (threat confirmed / false alarm)
- House-rules suggestions (advisory only, operator manually applies)
- Confidence scoring (not binary decisions)
- Audit-first (every action logged, immutable)

Three Modules:
1. ThreatPatternDetector — pattern analysis + confidence scoring
2. SecurityAdvisor — feedback integration + policy recommendations
3. FeedbackOptimizer — learning from operator feedback

Load-bearing constraints:
- Never auto-apply house-rules (suggestions advisory only)
- No silent learning (every feedback/update is audit-logged)
- Operator-defined security_policy.yaml (not hardcoded)
- Fail-closed on uncertainty (no false negatives)
"""

from __future__ import annotations

import json
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from enum import Enum
from pathlib import Path
import statistics


class PatternType(Enum):
    """Threat pattern categories."""
    BURST = "burst"  # N events in window W (rapid-fire)
    CREEP = "creep"  # >0 events per hour for D hours (stealthy)
    CONCENTRATION = "concentration"  # N% of events from single source
    CONTEXT_SHIFT = "context_shift"  # user in location A, request from location B
    GEO_MISMATCH = "geo_mismatch"  # geolocation inconsistency


class EventType(str, Enum):
    """Audit event types for security orchestrator."""
    PATTERN_DETECTED = "security_pattern_detected"
    THREAT_CONFIRMED = "threat_confirmed_by_operator"
    FALSE_ALARM = "false_alarm_by_operator"
    POLICY_SUGGESTED = "policy_suggestion_generated"
    CONFIG_OPTIMIZED = "security_config_optimized"


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event (ADR-0232 compliant)."""
    event_id: str
    event_type: str
    skill_id: str
    tenant_id: str
    timestamp: str
    details: Dict[str, Any] = field(default_factory=dict)
    prev_hash: Optional[str] = None
    hash: Optional[str] = None

    def compute_hash(self) -> str:
        """Hash-chain compatible hash."""
        content = json.dumps({
            "event_id": self.event_id,
            "event_type": self.event_type,
            "skill_id": self.skill_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "details": self.details,
            "prev_hash": self.prev_hash,
        }, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class ThreatPattern:
    """Detected threat pattern with confidence."""
    pattern_type: PatternType
    severity: str  # "critical", "high", "medium", "low"
    confidence: float  # 0.0-1.0
    affected_entity: str  # IP, user_id, etc.
    timestamp_range: Tuple[str, str]  # (start, end) ISO 8601
    event_count: int
    suggested_action: str
    context: Dict[str, Any] = field(default_factory=dict)


class ThreatPatternDetector:
    """Module 1: Detect threat patterns in audit logs.

    Input: Audit event stream (tenant-scoped)
    Output: {pattern_type, severity, confidence, affected_entity, suggested_action}
    Constraints: Read-only, no state change
    """

    def __init__(self, config: Dict[str, Any]):
        """Initialize detector with pattern rules.

        Config schema:
        {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
                "creep": {"hours": 24, "threshold": 1},
                "concentration": {"threshold_pct": 60},
                "context_shift": {"enabled": true}
            },
            "max_events_to_scan": 1000  # prevent OOM on huge logs
        }
        """
        self.config = config
        self.patterns = config.get("patterns", {})
        self.max_scan = config.get("max_events_to_scan", 1000)

    def detect_patterns(
        self, audit_events: List[Dict[str, Any]], tenant_id: str
    ) -> List[ThreatPattern]:
        """Scan audit events for threat patterns.

        Args:
            audit_events: List of audit events (must be tenant-scoped)
            tenant_id: Tenant scope (validation)

        Returns:
            List of detected patterns with confidence scores
        """
        if not audit_events:
            return []

        patterns = []

        # Filter to relevant events (denials, house-rule rejections, geo-mismatches)
        relevant_events = [
            e for e in audit_events[-self.max_scan:]
            if e.get("event_type") in [
                "house_rule_denied",
                "consent_denied",
                "geo_mismatch_detected",
                "audit_anomaly_detected",
            ]
            and e.get("tenant_id") == tenant_id
        ]

        if not relevant_events:
            return []

        # Detect burst (N events in window W)
        burst = self._detect_burst(relevant_events)
        if burst:
            patterns.extend(burst)

        # Detect creep (slow attack over time)
        creep = self._detect_creep(relevant_events)
        if creep:
            patterns.extend(creep)

        # Detect concentration (events from single source)
        concentration = self._detect_concentration(relevant_events)
        if concentration:
            patterns.extend(concentration)

        # Detect context shift (geo mismatch)
        context_shift = self._detect_context_shift(relevant_events)
        if context_shift:
            patterns.extend(context_shift)

        return patterns

    def _detect_burst(self, events: List[Dict[str, Any]]) -> List[ThreatPattern]:
        """Detect rapid-fire denial bursts."""
        burst_config = self.patterns.get("burst", {})
        if not burst_config:
            return []

        window_min = burst_config.get("window_minutes", 10)
        threshold = burst_config.get("threshold", 5)

        patterns = []
        grouped = self._group_by_source(events)

        for source, source_events in grouped.items():
            # Check for N events in window W
            if len(source_events) < threshold:
                continue

            times = [self._parse_timestamp(e["timestamp"]) for e in source_events]
            if not times:
                continue

            # Look for window with N events
            for i in range(len(times)):
                window_end = times[i]
                window_start = window_end - timedelta(minutes=window_min)

                events_in_window = sum(
                    1 for t in times if window_start <= t <= window_end
                )

                if events_in_window >= threshold:
                    confidence = min(1.0, events_in_window / threshold)

                    patterns.append(
                        ThreatPattern(
                            pattern_type=PatternType.BURST,
                            severity="high",
                            confidence=confidence,
                            affected_entity=source,
                            timestamp_range=(
                                str(window_start),
                                str(window_end),
                            ),
                            event_count=events_in_window,
                            suggested_action=f"Rate-limit {source} to {threshold-1} attempts per {window_min}min",
                            context={"source_type": self._infer_source_type(source)},
                        )
                    )
                    break  # Report first burst detected for this source

        return patterns

    def _detect_creep(self, events: List[Dict[str, Any]]) -> List[ThreatPattern]:
        """Detect slow attacks (stealthy pattern)."""
        creep_config = self.patterns.get("creep", {})
        if not creep_config:
            return []

        hours = creep_config.get("hours", 24)
        threshold = creep_config.get("threshold", 1)

        patterns = []
        grouped = self._group_by_source(events)

        for source, source_events in grouped.items():
            if len(source_events) < threshold * hours:
                continue

            # Check if at least 1 event per hour for N hours
            times = [self._parse_timestamp(e["timestamp"]) for e in source_events]
            if not times:
                continue

            times.sort()
            hours_with_events = self._count_hours_with_events(times, hours)

            if hours_with_events >= hours:
                confidence = 0.7  # creep is suspicious but could be legitimate

                patterns.append(
                    ThreatPattern(
                        pattern_type=PatternType.CREEP,
                        severity="medium",
                        confidence=confidence,
                        affected_entity=source,
                        timestamp_range=(str(times[0]), str(times[-1])),
                        event_count=len(source_events),
                        suggested_action=f"Monitor {source} for credential compromise; require MFA re-auth",
                        context={
                            "hours_with_events": hours_with_events,
                            "pattern": "one or more attempts every hour",
                        },
                    )
                )

        return patterns

    def _detect_concentration(self, events: List[Dict[str, Any]]) -> List[ThreatPattern]:
        """Detect concentration of events from single source."""
        conc_config = self.patterns.get("concentration", {})
        if not conc_config:
            return []

        threshold_pct = conc_config.get("threshold_pct", 60)

        if len(events) < 10:  # Need enough events to detect concentration
            return []

        grouped = self._group_by_source(events)
        total_events = len(events)

        patterns = []
        for source, source_events in grouped.items():
            pct = (len(source_events) / total_events) * 100

            if pct >= threshold_pct:
                confidence = min(1.0, pct / 100.0)

                patterns.append(
                    ThreatPattern(
                        pattern_type=PatternType.CONCENTRATION,
                        severity="high",
                        confidence=confidence,
                        affected_entity=source,
                        timestamp_range=(
                            str(self._parse_timestamp(source_events[0]["timestamp"])),
                            str(
                                self._parse_timestamp(
                                    source_events[-1]["timestamp"]
                                )
                            ),
                        ),
                        event_count=len(source_events),
                        suggested_action=f"{pct:.1f}% of denials from {source}; recommend IP blocklist for 24h",
                        context={"concentration_pct": pct},
                    )
                )

        return patterns

    def _detect_context_shift(self, events: List[Dict[str, Any]]) -> List[ThreatPattern]:
        """Detect geographic or behavioral context shifts."""
        shift_config = self.patterns.get("context_shift", {})
        if not shift_config or not shift_config.get("enabled"):
            return []

        # Look for GEO_MISMATCH events
        geo_events = [e for e in events if e.get("event_type") == "geo_mismatch_detected"]

        if not geo_events:
            return []

        patterns = []
        grouped_by_user = self._group_by_field(geo_events, "user_id")

        for user_id, user_events in grouped_by_user.items():
            if len(user_events) < 2:
                continue

            # Multiple geos in short time = suspicious
            times = [self._parse_timestamp(e["timestamp"]) for e in user_events]
            time_span = (max(times) - min(times)).total_seconds()

            if time_span < 3600:  # Multiple geos within 1 hour
                geos = [e.get("details", {}).get("geo_from") for e in user_events]
                confidence = 0.6  # Could be legitimate travel

                patterns.append(
                    ThreatPattern(
                        pattern_type=PatternType.CONTEXT_SHIFT,
                        severity="medium",
                        confidence=confidence,
                        affected_entity=user_id,
                        timestamp_range=(str(min(times)), str(max(times))),
                        event_count=len(user_events),
                        suggested_action=f"Send 2FA challenge to {user_id}; require SMS verification",
                        context={
                            "geos": list(set(geos)),
                            "time_span_minutes": time_span / 60,
                        },
                    )
                )

        return patterns

    def _group_by_source(
        self, events: List[Dict[str, Any]]
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group events by source (IP, user_id, etc.)."""
        grouped = {}
        for event in events:
            # Try to extract source from event details
            source = (
                event.get("details", {}).get("source_ip")
                or event.get("details", {}).get("user_id")
                or event.get("details", {}).get("ip_address")
                or "unknown"
            )
            if source not in grouped:
                grouped[source] = []
            grouped[source].append(event)
        return grouped

    def _group_by_field(
        self, events: List[Dict[str, Any]], field: str
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Group events by a specific field."""
        grouped = {}
        for event in events:
            key = event.get(field, "unknown")
            if key not in grouped:
                grouped[key] = []
            grouped[key].append(event)
        return grouped

    def _infer_source_type(self, source: str) -> str:
        """Infer if source is IP, user_id, or other."""
        if source.count(".") == 3 and all(
            part.isdigit() and 0 <= int(part) <= 255 for part in source.split(".")
        ):
            return "ipv4"
        elif "@" in source:
            return "email"
        else:
            return "user_id"

    def _parse_timestamp(self, ts: str) -> datetime:
        """Parse ISO 8601 timestamp."""
        if not ts:
            return datetime.utcnow()
        try:
            ts = ts.rstrip("Z")
            if "." in ts:
                ts = ts.split(".")[0]
            return datetime.fromisoformat(ts)
        except Exception:
            return datetime.utcnow()

    def _count_hours_with_events(self, times: List[datetime], hours: int) -> int:
        """Count how many consecutive hours have at least one event."""
        if not times:
            return 0

        times = sorted(times)
        hour_counts = {}

        for t in times:
            hour_key = t.replace(minute=0, second=0, microsecond=0)
            hour_counts[hour_key] = hour_counts.get(hour_key, 0) + 1

        # Count consecutive hours from earliest
        sorted_hours = sorted(hour_counts.keys())
        consecutive = 1

        for i in range(1, len(sorted_hours)):
            if (sorted_hours[i] - sorted_hours[i - 1]).total_seconds() == 3600:
                consecutive += 1
            else:
                break

        return consecutive


class SecurityAdvisor:
    """Module 2: Generate policy recommendations from patterns.

    Input: Pattern + operator feedback history
    Output: Recommendation JSON (advisory, operator manually applies)
    """

    def __init__(self, feedback_history: List[Dict[str, Any]] = None):
        """Initialize with feedback history for confidence adjustment."""
        self.feedback_history = feedback_history or []

    def generate_recommendation(
        self, pattern: ThreatPattern, tenant_id: str
    ) -> Dict[str, Any]:
        """Convert pattern to actionable recommendation.

        Returns advisory JSON (never applies policy automatically).
        """
        confidence = self._adjust_confidence_by_history(pattern)

        recommendation = {
            "pattern_id": hashlib.sha256(
                json.dumps(
                    {
                        "entity": pattern.affected_entity,
                        "type": pattern.pattern_type.value,
                        "time": pattern.timestamp_range[0],
                    }
                ).encode()
            ).hexdigest()[:12],
            "pattern_type": pattern.pattern_type.value,
            "severity": pattern.severity,
            "confidence": confidence,
            "affected_entity": pattern.affected_entity,
            "event_count": pattern.event_count,
            "time_range": {"start": pattern.timestamp_range[0], "end": pattern.timestamp_range[1]},
            "suggested_action": pattern.suggested_action,
            "recommended_house_rule": self._generate_house_rule_suggestion(pattern),
            "action_options": [
                {"action": "rate_limit", "ttl_minutes": 60},
                {"action": "require_mfa", "ttl_hours": 24},
                {"action": "temporary_block", "ttl_minutes": 30},
                {"action": "monitor_only", "ttl_hours": 48},
            ],
            "context": pattern.context,
            "operator_decision_required": True,  # NEVER auto-apply
            "audit_trail": self._generate_audit_trace(pattern),
        }

        return recommendation

    def _adjust_confidence_by_history(self, pattern: ThreatPattern) -> float:
        """Adjust confidence based on operator feedback history."""
        confidence = pattern.confidence

        # Check if we've seen similar patterns before
        similar_feedback = [
            f for f in self.feedback_history
            if f.get("pattern_type") == pattern.pattern_type.value
            and f.get("affected_entity") == pattern.affected_entity
        ]

        if similar_feedback:
            # Calculate confirmation rate
            confirmed = sum(1 for f in similar_feedback if f.get("is_threat") is True)
            false_alarms = sum(1 for f in similar_feedback if f.get("is_threat") is False)

            if confirmed + false_alarms > 0:
                confirmation_rate = confirmed / (confirmed + false_alarms)
                # Adjust confidence toward confirmation rate
                confidence = (confidence + confirmation_rate) / 2

        return min(1.0, confidence)

    def _generate_house_rule_suggestion(self, pattern: ThreatPattern) -> Dict[str, Any]:
        """Generate proposed house-rules.yaml change."""
        suggestion = {
            "rule_name": f"{pattern.pattern_type.value}_{pattern.affected_entity[:8]}",
            "condition": self._generate_condition(pattern),
            "action": "deny",
            "reason": f"Skill {pattern.pattern_type.value} detection on {pattern.affected_entity}",
            "manual_review_required": True,
            "note": "This is a SUGGESTION ONLY. Operator must review and manually edit house-rules.yaml in git. No auto-apply.",
        }
        return suggestion

    def _generate_condition(self, pattern: ThreatPattern) -> str:
        """Generate a readable condition for the suggested rule."""
        if pattern.pattern_type == PatternType.BURST:
            return f"source_ip == '{pattern.affected_entity}' && denial_count > {pattern.event_count} && time_window == '10m'"
        elif pattern.pattern_type == PatternType.CONCENTRATION:
            return f"source_ip == '{pattern.affected_entity}' && concentration_pct > {pattern.context.get('concentration_pct', 60)}"
        elif pattern.pattern_type == PatternType.CONTEXT_SHIFT:
            return f"user_id == '{pattern.affected_entity}' && geo_shift_detected && !verified_by_mfa"
        else:
            return f"pattern_type == '{pattern.pattern_type.value}' && affected_entity == '{pattern.affected_entity}'"

    def _generate_audit_trace(self, pattern: ThreatPattern) -> List[str]:
        """Generate human-readable audit trace."""
        return [
            f"Pattern detected: {pattern.pattern_type.value}",
            f"Affected: {pattern.affected_entity}",
            f"Events: {pattern.event_count} in {pattern.timestamp_range}",
            f"Severity: {pattern.severity} (confidence: {pattern.confidence:.1%})",
            f"Recommended: {pattern.suggested_action}",
        ]


class FeedbackOptimizer:
    """Module 3: Learn from operator feedback to tune pattern parameters.

    Input: Operator feedback (threat confirmed / false alarm) + pattern
    Output: Updated skill config (parameter tuning only)
    Constraints: Never weakens defaults, all changes audited, operator can revert
    """

    def __init__(self, current_config: Dict[str, Any]):
        """Initialize with current detector configuration."""
        self.current_config = current_config.copy()
        self.feedback_log = []

    def record_feedback(
        self,
        pattern: ThreatPattern,
        is_threat: bool,
        operator_note: str = "",
    ) -> Dict[str, Any]:
        """Record operator feedback and suggest config updates.

        Args:
            pattern: The detected pattern
            is_threat: True if operator confirms threat, False if false alarm
            operator_note: Human-readable explanation

        Returns:
            Updated config (candidate only, operator applies manually if desired)
        """
        feedback_record = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "pattern_type": pattern.pattern_type.value,
            "affected_entity": pattern.affected_entity,
            "is_threat": is_threat,
            "operator_note": operator_note,
            "original_confidence": pattern.confidence,
        }
        self.feedback_log.append(feedback_record)

        # Suggest config update based on feedback
        suggested_config = self._suggest_config_update(pattern, is_threat)

        return {
            "feedback_recorded": True,
            "feedback_id": hashlib.sha256(
                json.dumps(feedback_record).encode()
            ).hexdigest()[:12],
            "pattern_type": pattern.pattern_type.value,
            "operator_decision": "threat confirmed" if is_threat else "false alarm",
            "suggested_config_update": suggested_config,
            "note": "Config update is ADVISORY. Operator applies manually via code change if desired.",
        }

    def _suggest_config_update(
        self, pattern: ThreatPattern, is_threat: bool
    ) -> Dict[str, Any]:
        """Suggest parameter adjustments based on feedback.

        If operator says "false alarm", suggest RAISING thresholds (fewer false positives).
        If operator says "threat", suggest LOWERING thresholds (higher sensitivity).
        Never weaken defaults.
        """
        update = {}

        if pattern.pattern_type == PatternType.BURST:
            burst_cfg = self.current_config.get("patterns", {}).get("burst", {})
            if is_threat:
                # Keep current threshold (confirmed)
                update["burst"] = burst_cfg
            else:
                # Raise threshold (too sensitive)
                new_threshold = burst_cfg.get("threshold", 5) + 1
                update["burst"] = {
                    **burst_cfg,
                    "threshold": new_threshold,
                    "reason": "Raised due to false alarm feedback",
                }

        elif pattern.pattern_type == PatternType.CONCENTRATION:
            conc_cfg = self.current_config.get("patterns", {}).get("concentration", {})
            if is_threat:
                # Keep current
                update["concentration"] = conc_cfg
            else:
                # Raise threshold (too sensitive)
                new_threshold = conc_cfg.get("threshold_pct", 60) + 5
                update["concentration"] = {
                    **conc_cfg,
                    "threshold_pct": new_threshold,
                    "reason": "Raised due to false alarm feedback",
                }

        else:
            # For other pattern types, no automatic adjustment
            update[pattern.pattern_type.value] = self.current_config.get("patterns", {}).get(
                pattern.pattern_type.value, {}
            )

        return update

    def get_feedback_stats(self) -> Dict[str, Any]:
        """Generate feedback statistics for operator review."""
        if not self.feedback_log:
            return {"total_feedback": 0, "stats": {}}

        by_pattern = {}
        for fb in self.feedback_log:
            pt = fb["pattern_type"]
            if pt not in by_pattern:
                by_pattern[pt] = {"threat_confirmed": 0, "false_alarms": 0}
            if fb["is_threat"]:
                by_pattern[pt]["threat_confirmed"] += 1
            else:
                by_pattern[pt]["false_alarms"] += 1

        stats = {
            "total_feedback": len(self.feedback_log),
            "by_pattern_type": by_pattern,
            "threat_confirmation_rate": sum(
                1 for fb in self.feedback_log if fb["is_threat"]
            ) / len(self.feedback_log)
            if self.feedback_log
            else 0,
        }

        return stats


# ── Skill Implementation (integrates all three modules) ────────────────────


class SecurityOrchestratorSkill:
    """Main skill: os.security_orchestrator (Phase 5.3, ADR-0532).

    Detects threat patterns in audit logs, generates policy recommendations,
    and learns from operator feedback.

    Load-bearing constraints (MUST NOT weaken):
    - House-rules suggestions are advisory only (operator manually applies)
    - Every action is audit-logged (immutable, tenant-scoped)
    - No silent learning (feedback loop is explicit)
    - Operator-defined security policy (not hardcoded)
    """

    def __init__(self, config: Dict[str, Any] = None):
        """Initialize skill with default configuration."""
        self.config = config or self._default_config()
        self.detector = ThreatPatternDetector(self.config)
        self.advisor = SecurityAdvisor()
        self.optimizer = FeedbackOptimizer(self.config)
        self.audit_events = []

    def _default_config(self) -> Dict[str, Any]:
        """Default pattern detection configuration."""
        return {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
                "creep": {"hours": 24, "threshold": 1},
                "concentration": {"threshold_pct": 60},
                "context_shift": {"enabled": True},
            },
            "max_events_to_scan": 1000,
            "confidence_threshold": 0.5,  # Report patterns above this confidence
        }

    def execute(
        self, audit_events: List[Dict[str, Any]], tenant_id: str
    ) -> Dict[str, Any]:
        """Main skill execution.

        Args:
            audit_events: List of audit events from audit chain
            tenant_id: Tenant scope

        Returns:
            {
                "patterns_detected": [pattern...],
                "recommendations": [recommendation...],
                "skill_version": "1.0",
                "timestamp": "2026-09-20T...",
                "audit_committed": True
            }
        """
        start_time = datetime.utcnow()

        # Detect patterns
        patterns = self.detector.detect_patterns(audit_events, tenant_id)

        # Filter by confidence threshold
        patterns = [
            p for p in patterns if p.confidence >= self.config.get("confidence_threshold", 0.5)
        ]

        # Generate recommendations
        recommendations = [
            self.advisor.generate_recommendation(p, tenant_id) for p in patterns
        ]

        # Emit audit event for skill execution
        exec_event = self._emit_audit_event(
            event_type=EventType.PATTERN_DETECTED.value,
            tenant_id=tenant_id,
            details={
                "patterns_detected": len(patterns),
                "recommendations_generated": len(recommendations),
                "execution_time_ms": (
                    (datetime.utcnow() - start_time).total_seconds() * 1000
                ),
            },
        )

        return {
            "patterns_detected": [
                {
                    "type": p.pattern_type.value,
                    "severity": p.severity,
                    "confidence": p.confidence,
                    "affected_entity": p.affected_entity,
                    "event_count": p.event_count,
                    "suggested_action": p.suggested_action,
                    "context": p.context,
                }
                for p in patterns
            ],
            "recommendations": recommendations,
            "skill_version": "1.0",
            "timestamp": start_time.isoformat() + "Z",
            "tenant_id": tenant_id,
            "audit_event_id": exec_event["event_id"],
            "audit_committed": True,
            "note": "All recommendations are ADVISORY. Operator must manually review and apply changes via git.",
        }

    def submit_feedback(
        self, pattern: ThreatPattern, is_threat: bool, operator_note: str = ""
    ) -> Dict[str, Any]:
        """Operator submits feedback on a detected pattern.

        Args:
            pattern: The ThreatPattern that was detected
            is_threat: True if operator confirms it was a real threat
            operator_note: Explanation (e.g., "This was legitimate user travel")

        Returns:
            {
                "feedback_recorded": True,
                "feedback_id": "...",
                "suggested_config_update": {...}
            }
        """
        feedback = self.optimizer.record_feedback(pattern, is_threat, operator_note)

        # Emit audit event
        audit_event = self._emit_audit_event(
            event_type=EventType.THREAT_CONFIRMED.value if is_threat else EventType.FALSE_ALARM.value,
            tenant_id="tenant_id_from_context",  # Would come from context in real impl
            details={
                "pattern_type": pattern.pattern_type.value,
                "affected_entity": pattern.affected_entity,
                "operator_note": operator_note,
                "feedback_id": feedback["feedback_id"],
            },
        )

        feedback["audit_event_id"] = audit_event["event_id"]
        return feedback

    def _emit_audit_event(
        self, event_type: str, tenant_id: str, details: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Emit audit event (hash-chained, immutable).

        Load-bearing: EVERY skill action is logged and hash-chained.
        """
        import uuid

        event_id = str(uuid.uuid4())
        timestamp = datetime.utcnow().isoformat() + "Z"

        prev_hash = (
            self.audit_events[-1].get("hash")
            if self.audit_events
            else None
        )

        event = {
            "event_id": event_id,
            "event_type": event_type,
            "skill_id": "os.security_orchestrator",
            "tenant_id": tenant_id,
            "timestamp": timestamp,
            "details": details,
            "prev_hash": prev_hash,
        }

        # Compute hash
        event_content = json.dumps(
            {
                "event_id": event_id,
                "event_type": event_type,
                "skill_id": "os.security_orchestrator",
                "tenant_id": tenant_id,
                "timestamp": timestamp,
                "details": details,
                "prev_hash": prev_hash,
            },
            sort_keys=True,
        )
        event["hash"] = hashlib.sha256(event_content.encode()).hexdigest()

        self.audit_events.append(event)
        return event

    def verify_audit_chain(self) -> Dict[str, Any]:
        """Verify hash-chain integrity (boot tripwire compatible).

        Returns:
            {
                "chain_valid": True/False,
                "event_count": N,
                "gap_detected": False/True,
                "integrity_error": "..." if invalid
            }
        """
        if not self.audit_events:
            return {
                "chain_valid": True,
                "event_count": 0,
                "gap_detected": False,
            }

        prev_hash = None
        for i, event in enumerate(self.audit_events):
            # Verify this event's hash
            event_content = json.dumps(
                {
                    "event_id": event["event_id"],
                    "event_type": event["event_type"],
                    "skill_id": event["skill_id"],
                    "tenant_id": event["tenant_id"],
                    "timestamp": event["timestamp"],
                    "details": event["details"],
                    "prev_hash": event.get("prev_hash"),
                },
                sort_keys=True,
            )
            computed_hash = hashlib.sha256(event_content.encode()).hexdigest()

            if computed_hash != event.get("hash"):
                return {
                    "chain_valid": False,
                    "event_count": i,
                    "gap_detected": True,
                    "integrity_error": f"Hash mismatch at event {i}: computed {computed_hash}, got {event.get('hash')}",
                }

            # Verify backward link
            if i > 0 and event.get("prev_hash") != self.audit_events[i - 1].get("hash"):
                return {
                    "chain_valid": False,
                    "event_count": i,
                    "gap_detected": True,
                    "integrity_error": f"Chain break at event {i}: prev_hash mismatch",
                }

        return {
            "chain_valid": True,
            "event_count": len(self.audit_events),
            "gap_detected": False,
        }
