"""
Security Orchestrator Skill (ADR-2031)

Detects threat patterns from the audit trail and dynamically hardens security gates.

Patterns:
- Brute force: >N failed auth attempts in T seconds
- Privilege escalation: Unauthorized override attempts  
- Data exfiltration: >N high-risk data flows to external engine
- Distributed attack: >N requests from different IPs

All policy changes are audited, reversible (TTL), and never bypass house-rules.

Exports:
- SecurityOrchestratorSkill: Main Skill class
- ThreatDetector: Pattern matching
- PolicyEngine: Dynamic security policy state machine
"""

from .security_orchestrator import SecurityOrchestratorSkill
from .threat_detection import ThreatDetector, ThreatSignal, ThreatPattern
from .policy_engine import PolicyEngine, SecurityPolicy, PolicyTightening

__version__ = "1.0.0"
__all__ = [
    "SecurityOrchestratorSkill",
    "ThreatDetector",
    "ThreatSignal",
    "ThreatPattern",
    "PolicyEngine",
    "SecurityPolicy",
    "PolicyTightening",
]
