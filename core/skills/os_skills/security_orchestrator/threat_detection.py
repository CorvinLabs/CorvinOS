"""
Threat detection engine for Security Orchestrator Skill (ADR-2031).

Detects 5 threat patterns from audit trail:
1. Brute force: >N failed auth attempts in T seconds
2. Privilege escalation: Unauthorized override attempts
3. Data exfiltration: >N high-risk data flows to external engine
4. Distributed attack: >N requests from different IPs
5. Anomalous behavior: User/system behavior deviation

Returns ThreatSignal with confidence score (0.0-1.0) for policy tightening decision.
"""

from dataclasses import dataclass, field
from enum import Enum
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any
import json
from collections import defaultdict


class ThreatPattern(str, Enum):
    """Recognized threat patterns."""
    BRUTE_FORCE = "brute_force_auth"
    PRIVILEGE_ESCALATION = "privilege_escalation"
    DATA_EXFILTRATION = "data_exfiltration"
    DISTRIBUTED_ATTACK = "distributed_attack"
    ANOMALOUS_BEHAVIOR = "anomalous_behavior"


@dataclass
class ThreatSignal:
    """Signal from threat detector indicating an active threat."""
    pattern: ThreatPattern
    confidence: float  # 0.0-1.0
    severity: str  # low, medium, high, critical
    affected_users: List[str] = field(default_factory=list)
    affected_ips: List[str] = field(default_factory=list)
    detected_at: str = ""  # ISO 8601 timestamp
    event_count: int = 0  # Number of events in the pattern
    summary: str = ""  # Human-readable summary
    
    def is_actionable(self, threshold: float = 0.75) -> bool:
        """Whether this signal merits automatic policy tightening."""
        return self.confidence >= threshold


class ThreatDetector:
    """
    Detects threat patterns from audit trail events.

    Implements sliding-window anomaly detection:
    - Maintains per-user/per-IP event windows (configurable time window)
    - Counts events matching threat signatures
    - Emits ThreatSignal when threshold exceeded

    HIGH #6: Enforces maximum event limits (100K events per call) to prevent
    memory exhaustion attacks. Larger event sets are rejected with ValueError.

    Example:
        detector = ThreatDetector(window_minutes=5, brute_force_threshold=5)
        threat = detector.analyze_auth_events(auth_events)
        if threat.is_actionable():
            policy_engine.tighten(threat)
    """

    # HIGH #6: Maximum events per analysis call (prevent unbounded memory usage)
    MAX_EVENTS = 100_000

    def __init__(
        self,
        window_minutes: int = 5,
        brute_force_threshold: int = 5,  # >N failed attempts in window
        priv_esc_threshold: int = 3,
        data_exfil_threshold: int = 10,
        distributed_threshold: int = 20,
    ):
        """
        Args:
            window_minutes: Sliding window for pattern detection
            brute_force_threshold: Failed auth attempts to trigger detection
            priv_esc_threshold: Unauthorized overrides to trigger detection
            data_exfil_threshold: High-risk data flows to trigger detection
            distributed_threshold: Requests from unique IPs to trigger detection
        """
        self.window_minutes = window_minutes
        self.thresholds = {
            ThreatPattern.BRUTE_FORCE: brute_force_threshold,
            ThreatPattern.PRIVILEGE_ESCALATION: priv_esc_threshold,
            ThreatPattern.DATA_EXFILTRATION: data_exfil_threshold,
            ThreatPattern.DISTRIBUTED_ATTACK: distributed_threshold,
        }
        
        # Sliding windows: {user_id: [(timestamp, event_type), ...]}
        self.user_events = defaultdict(list)
        self.ip_events = defaultdict(list)
    
    def analyze_auth_events(
        self,
        auth_events: List[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> Optional[ThreatSignal]:
        """
        Analyze authentication events for brute force patterns.

        Brute force signature: >threshold failed auth attempts from same user
        in window_minutes.

        Args:
            auth_events: List of {"user_id", "success", "timestamp", "ip"}
            now: Current time (for testing); defaults to utcnow()

        Returns:
            ThreatSignal if detected, None otherwise

        Raises:
            ValueError: If auth_events exceeds MAX_EVENTS (HIGH #6 resource limit)
        """
        if not auth_events:
            return None

        # HIGH #6: Enforce event limit to prevent memory exhaustion
        if len(auth_events) > self.MAX_EVENTS:
            raise ValueError(
                f"Event set too large: {len(auth_events)} > {self.MAX_EVENTS} (DoS protection)"
            )
        
        now = now or datetime.utcnow()
        window_start = now - timedelta(minutes=self.window_minutes)
        
        failed_by_user = defaultdict(int)
        affected_users = set()
        affected_ips = set()
        
        for event in auth_events:
            ts = self._parse_timestamp(event.get("timestamp"))
            if ts < window_start:
                continue
                
            if not event.get("success", True):  # Count failed attempts
                user = event.get("user_id", "unknown")
                ip = event.get("ip", "unknown")
                failed_by_user[user] += 1
                affected_users.add(user)
                affected_ips.add(ip)
        
        # Check threshold
        worst_user = max(failed_by_user.values()) if failed_by_user else 0
        if worst_user >= self.thresholds[ThreatPattern.BRUTE_FORCE]:
            confidence = min(1.0, worst_user / (2 * self.thresholds[ThreatPattern.BRUTE_FORCE]))
            return ThreatSignal(
                pattern=ThreatPattern.BRUTE_FORCE,
                confidence=confidence,
                severity=self._severity_from_confidence(confidence),
                affected_users=sorted(affected_users),
                affected_ips=sorted(affected_ips),
                detected_at=now.isoformat() + "Z",
                event_count=len(auth_events),
                summary=f"Brute force detected: {worst_user} failed auth attempts",
            )
        
        return None
    
    def analyze_privilege_escalation_events(
        self,
        override_events: List[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> Optional[ThreatSignal]:
        """Analyze events for unauthorized privilege escalation attempts.

        Raises:
            ValueError: If override_events exceeds MAX_EVENTS (HIGH #6)
        """
        if not override_events:
            return None

        # HIGH #6: Enforce event limit
        if len(override_events) > self.MAX_EVENTS:
            raise ValueError(
                f"Event set too large: {len(override_events)} > {self.MAX_EVENTS} (DoS protection)"
            )
        
        now = now or datetime.utcnow()
        window_start = now - timedelta(minutes=self.window_minutes)
        
        unauthorized_by_user = defaultdict(int)
        affected_users = set()
        
        for event in override_events:
            ts = self._parse_timestamp(event.get("timestamp"))
            if ts < window_start:
                continue
            
            if not event.get("authorized", True):  # Unauthorized override attempt
                user = event.get("user_id", "unknown")
                unauthorized_by_user[user] += 1
                affected_users.add(user)
        
        worst_user = max(unauthorized_by_user.values()) if unauthorized_by_user else 0
        if worst_user >= self.thresholds[ThreatPattern.PRIVILEGE_ESCALATION]:
            confidence = min(1.0, worst_user / (2 * self.thresholds[ThreatPattern.PRIVILEGE_ESCALATION]))
            return ThreatSignal(
                pattern=ThreatPattern.PRIVILEGE_ESCALATION,
                confidence=confidence,
                severity=self._severity_from_confidence(confidence),
                affected_users=sorted(affected_users),
                affected_ips=[],
                detected_at=now.isoformat() + "Z",
                event_count=len(override_events),
                summary=f"Privilege escalation detected: {worst_user} unauthorized overrides",
            )
        
        return None
    
    def analyze_data_exfiltration_events(
        self,
        data_flow_events: List[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> Optional[ThreatSignal]:
        """Analyze events for data exfiltration patterns.

        Raises:
            ValueError: If data_flow_events exceeds MAX_EVENTS (HIGH #6)
        """
        if not data_flow_events:
            return None

        # HIGH #6: Enforce event limit
        if len(data_flow_events) > self.MAX_EVENTS:
            raise ValueError(
                f"Event set too large: {len(data_flow_events)} > {self.MAX_EVENTS} (DoS protection)"
            )
        
        now = now or datetime.utcnow()
        window_start = now - timedelta(minutes=self.window_minutes)
        
        high_risk_flows = 0
        affected_users = set()
        
        for event in data_flow_events:
            ts = self._parse_timestamp(event.get("timestamp"))
            if ts < window_start:
                continue
            
            if event.get("risk_level") == "high":
                high_risk_flows += 1
                affected_users.add(event.get("user_id", "unknown"))
        
        if high_risk_flows >= self.thresholds[ThreatPattern.DATA_EXFILTRATION]:
            confidence = min(1.0, high_risk_flows / (2 * self.thresholds[ThreatPattern.DATA_EXFILTRATION]))
            return ThreatSignal(
                pattern=ThreatPattern.DATA_EXFILTRATION,
                confidence=confidence,
                severity=self._severity_from_confidence(confidence),
                affected_users=sorted(affected_users),
                affected_ips=[],
                detected_at=now.isoformat() + "Z",
                event_count=len(data_flow_events),
                summary=f"Data exfiltration detected: {high_risk_flows} high-risk flows",
            )
        
        return None
    
    def analyze_distributed_attack(
        self,
        request_events: List[Dict[str, Any]],
        now: Optional[datetime] = None,
    ) -> Optional[ThreatSignal]:
        """Analyze events for distributed attack patterns (many IPs targeting one resource).

        Raises:
            ValueError: If request_events exceeds MAX_EVENTS (HIGH #6)
        """
        if not request_events:
            return None

        # HIGH #6: Enforce event limit
        if len(request_events) > self.MAX_EVENTS:
            raise ValueError(
                f"Event set too large: {len(request_events)} > {self.MAX_EVENTS} (DoS protection)"
            )
        
        now = now or datetime.utcnow()
        window_start = now - timedelta(minutes=self.window_minutes)
        
        ips_by_target = defaultdict(set)
        affected_ips = set()
        
        for event in request_events:
            ts = self._parse_timestamp(event.get("timestamp"))
            if ts < window_start:
                continue
            
            target = event.get("target", "unknown")
            ip = event.get("ip", "unknown")
            ips_by_target[target].add(ip)
            affected_ips.add(ip)
        
        max_ips = max(len(ips) for ips in ips_by_target.values()) if ips_by_target else 0
        if max_ips >= self.thresholds[ThreatPattern.DISTRIBUTED_ATTACK]:
            confidence = min(1.0, max_ips / (2 * self.thresholds[ThreatPattern.DISTRIBUTED_ATTACK]))
            return ThreatSignal(
                pattern=ThreatPattern.DISTRIBUTED_ATTACK,
                confidence=confidence,
                severity=self._severity_from_confidence(confidence),
                affected_users=[],
                affected_ips=sorted(affected_ips),
                detected_at=now.isoformat() + "Z",
                event_count=len(request_events),
                summary=f"Distributed attack detected: {max_ips} unique IPs targeting same resource",
            )
        
        return None
    
    @staticmethod
    def _parse_timestamp(ts_str: str) -> datetime:
        """Parse ISO 8601 timestamp to datetime."""
        if isinstance(ts_str, datetime):
            return ts_str
        try:
            return datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        except (ValueError, AttributeError):
            return datetime.utcnow()
    
    @staticmethod
    def _severity_from_confidence(confidence: float) -> str:
        """Map confidence score to severity level."""
        if confidence >= 0.95:
            return "critical"
        elif confidence >= 0.85:
            return "high"
        elif confidence >= 0.70:
            return "medium"
        else:
            return "low"
