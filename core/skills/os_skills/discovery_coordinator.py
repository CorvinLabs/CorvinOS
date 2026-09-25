"""Backend Discovery Coordinator Skill — A2A Peer Discovery & Candidate Ranking

Phase 1 (k=1-5): Peer discovery via relay, local caching, eligibility filtering, candidate ranking.
Defers auto-pairing to Phase 2.

Architecture:
- Registers with relay for discoverability
- Fetches catalog of available peers from relay
- Filters by static eligibility (version, declared capabilities)
- Caches locally for relay resilience
- Ranks candidates by learning-based preferences
- Returns ordered candidate list + recommendation scores

Compliance:
- GDPR Art. 30: Every discovery decision audited
- ADR-0059: Discovered peers are NOT auto-trusted; explicit consent required for pairing
- ADR-0314: Learning signal: outcome_feedback per peer (successful invocation → rank up)
- Tenant-scoped execution (no cross-tenant discovery)

P99 latency: <100ms (lookup + filtering only, excludes pairing negotiation)
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional
import threading

logger = logging.getLogger(__name__)


class PeerEligibility(str, Enum):
    """Peer eligibility verdict (for audit trail)."""
    ELIGIBLE = "eligible"
    VERSION_MISMATCH = "version_mismatch"
    CAPABILITY_MISSING = "capability_missing"
    NETWORK_UNREACHABLE = "network_unreachable"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class PeerCandidate:
    """Immutable peer candidate (audit-safe)."""
    peer_id: str  # Unique peer identifier
    relay_endpoint: str  # Where relay found them (endpoint URL)
    declared_version: str  # Claimed software version
    declared_capabilities: List[str]  # Claimed capabilities (e.g., ["skill-executor", "l38-a2a"])
    network_hint: Optional[str] = None  # IP/port if known
    last_seen_ts: str = field(default_factory=lambda: datetime.utcnow().isoformat())

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit-safe dict."""
        return asdict(self)


@dataclass(frozen=True)
class DiscoveryCandidateRanking:
    """Ranked candidate with confidence + reasoning."""
    peer_id: str
    rank: int  # 1 = highest, ascending
    eligibility_verdict: PeerEligibility
    confidence: float  # 0.0–1.0: how confident we are this is a good peer
    reasoning: str  # Human-readable explanation (for audit + learning)
    learned_quality_score: float = 0.0  # From ADR-0314 feedback (0.0–1.0)

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to audit-safe dict."""
        data = asdict(self)
        data["eligibility_verdict"] = self.eligibility_verdict.value
        return data


class DiscoveryCoordinator:
    """Backend Discovery Coordinator — Peer discovery + ranking skill.

    Responsibilities:
    - Register with relay (announce this instance)
    - Fetch peer catalog from relay
    - Filter by eligibility (version, capabilities)
    - Rank candidates (static heuristics + learned preferences)
    - Cache locally (resilience when relay unavailable)
    - Emit audit trail (every decision)
    - Learn feedback (ADR-0314: outcome_feedback → rank adjustment)
    """

    # Version compatibility: peers within this range are eligible
    MIN_COMPATIBLE_VERSION = "1.0.0"
    MAX_COMPATIBLE_VERSION = "2.0.0"

    # Required capabilities for peer to be considered eligible
    REQUIRED_CAPABILITIES = {
        "skill-executor",  # Can execute Skills
        "l38-a2a",  # Speaks A2A protocol
    }

    # Optional capabilities (nice-to-have, boost rank)
    OPTIONAL_CAPABILITIES = {
        "l14-clustering",  # Supports clustering
        "l16-audit",  # Emits audit trail
        "high-bandwidth",  # Good for large payloads
    }

    def __init__(
        self,
        relay_endpoint: str = "http://localhost:8765/v1/a2a/relay",
        cache_ttl_minutes: int = 60,
        tenant_id: str = "_default",
        audit_backend: Optional[Any] = None,
        learning_backend: Optional[Any] = None,
    ):
        """Initialize Discovery Coordinator.

        Args:
            relay_endpoint: Relay URL to connect to
            cache_ttl_minutes: How long to keep cached catalog before re-fetching
            tenant_id: Tenant scope (no cross-tenant discovery)
            audit_backend: Audit trail backend (implements write_event)
            learning_backend: Learning backend (ADR-0314, implements emit_event)
        """
        self.relay_endpoint = relay_endpoint
        self.cache_ttl = timedelta(minutes=cache_ttl_minutes)
        self.tenant_id = tenant_id
        self.audit_backend = audit_backend
        self.learning_backend = learning_backend

        # Skill metadata
        self.skill_id = "os.discovery_coordinator"
        self.version = "1.0.0"

        # Local cache state
        self._cache_lock = threading.Lock()
        self._last_catalog: Optional[List[PeerCandidate]] = None
        self._catalog_timestamp: Optional[datetime] = None
        self._paired_peers: Dict[str, str] = {}  # peer_id → pairing_status
        self._peer_learning_scores: Dict[str, float] = {}  # peer_id → learned quality (0.0–1.0)

    def execute(self, input: Dict[str, Any]) -> Dict[str, Any]:
        """Execute discovery coordinator.

        Input:
            {
                "action": "discover" | "refresh_cache" | "get_candidates" | "record_feedback",
                "required_capabilities": ["skill-executor", ...],  # optional override
                "feedback_peer_id": "peer_xyz",  # for record_feedback action
                "feedback_outcome": "success" | "failure" | "timeout",  # for record_feedback
                "max_candidates": 10,  # max results to return
            }

        Output:
            {
                "action": input.action,
                "success": bool,
                "candidates": [DiscoveryCandidateRanking, ...],  # ranked list
                "catalog_size": int,  # total peers found
                "eligible_count": int,  # passed eligibility filter
                "confidence": float,  # overall confidence in results
                "reasoning": str,  # human-readable summary
                "cache_age_minutes": float,  # how fresh is the data
                "error": str or None,  # if success=false
            }
        """
        start_time = time.time()
        action = input.get("action", "discover")
        error = None
        result = {
            "action": action,
            "success": True,
            "candidates": [],
            "catalog_size": 0,
            "eligible_count": 0,
            "confidence": 0.0,
            "reasoning": "",
            "cache_age_minutes": None,
            "error": None,
        }

        try:
            if action == "discover":
                result = self._execute_discover(input, result)
            elif action == "refresh_cache":
                result = self._execute_refresh_cache(input, result)
            elif action == "get_candidates":
                result = self._execute_get_candidates(input, result)
            elif action == "record_feedback":
                result = self._execute_record_feedback(input, result)
            else:
                result["success"] = False
                result["error"] = f"Unknown action: {action}"

        except Exception as e:
            result["success"] = False
            result["error"] = self._sanitize_error_message(str(e))
            error = str(e)
            logger.exception(f"Discovery coordinator error: {e}")

        # Emit audit event
        latency_ms = int((time.time() - start_time) * 1000)
        self._emit_audit_event(
            event_type="discovery_executed",
            input_hash=self._hash_input(input),
            output_hash=self._hash_output(result),
            latency_ms=latency_ms,
            action=action,
            success=result["success"],
            error=error,
        )

        return result

    def _execute_discover(self, input: Dict[str, Any], result: Dict) -> Dict:
        """Discover peers: fetch catalog + rank candidates."""
        max_candidates = input.get("max_candidates", 10)
        required_caps = input.get("required_capabilities", list(self.REQUIRED_CAPABILITIES))

        # Step 1: Fetch catalog (or use cached if fresh)
        catalog = self._fetch_or_use_cached_catalog()
        result["catalog_size"] = len(catalog)
        result["cache_age_minutes"] = self._get_cache_age_minutes()

        # Step 2: Filter by eligibility
        eligible = self._filter_eligibility(catalog, required_caps)
        result["eligible_count"] = len(eligible)

        # Step 3: Rank candidates
        ranked = self._rank_candidates(eligible)
        result["candidates"] = [c.to_dict() for c in ranked[:max_candidates]]

        # Step 4: Compute confidence
        result["confidence"] = self._compute_confidence(len(catalog), len(eligible))
        result["reasoning"] = (
            f"Discovered {len(catalog)} peers via relay; {len(eligible)} passed eligibility; "
            f"top {len(result['candidates'])} ranked by learned quality + capability match"
        )

        return result

    def _execute_refresh_cache(self, input: Dict[str, Any], result: Dict) -> Dict:
        """Force refresh of peer catalog."""
        self._last_catalog = None  # Invalidate cache
        self._catalog_timestamp = None

        catalog = self._fetch_catalog_from_relay()
        result["catalog_size"] = len(catalog)
        result["reasoning"] = f"Cache refreshed: fetched {len(catalog)} peers from relay"

        return result

    def _execute_get_candidates(self, input: Dict[str, Any], result: Dict) -> Dict:
        """Get ranked candidates without forcing refresh."""
        max_candidates = input.get("max_candidates", 10)
        required_caps = input.get("required_capabilities", list(self.REQUIRED_CAPABILITIES))

        catalog = self._fetch_or_use_cached_catalog()
        eligible = self._filter_eligibility(catalog, required_caps)
        ranked = self._rank_candidates(eligible)

        result["catalog_size"] = len(catalog)
        result["eligible_count"] = len(eligible)
        result["candidates"] = [c.to_dict() for c in ranked[:max_candidates]]
        result["confidence"] = self._compute_confidence(len(catalog), len(eligible))
        result["cache_age_minutes"] = self._get_cache_age_minutes()

        return result

    def _execute_record_feedback(self, input: Dict[str, Any], result: Dict) -> Dict:
        """Record feedback on a peer (ADR-0314 learning signal)."""
        peer_id = input.get("feedback_peer_id")
        outcome = input.get("feedback_outcome", "unknown")  # success | failure | timeout

        if not peer_id:
            result["success"] = False
            result["error"] = "Missing feedback_peer_id"
            return result

        # Update learned score based on outcome
        if outcome == "success":
            # Successful invocation → boost score
            current_score = self._peer_learning_scores.get(peer_id, 0.5)
            new_score = min(1.0, current_score + 0.1)
            self._peer_learning_scores[peer_id] = new_score
            result["reasoning"] = f"Peer {peer_id}: success feedback → score {new_score:.2f}"
        elif outcome == "failure":
            # Failed invocation → penalize score
            current_score = self._peer_learning_scores.get(peer_id, 0.5)
            new_score = max(0.0, current_score - 0.2)
            self._peer_learning_scores[peer_id] = new_score
            result["reasoning"] = f"Peer {peer_id}: failure feedback → score {new_score:.2f}"
        else:
            result["reasoning"] = f"Peer {peer_id}: unknown outcome {outcome}"

        # Emit learning event (ADR-0314)
        self._emit_learning_event(
            feedback_type="outcome_feedback",
            peer_id=peer_id,
            outcome=outcome,
            new_score=self._peer_learning_scores.get(peer_id, 0.5),
        )

        return result

    # =========== Private Helper Methods ===========

    def _fetch_or_use_cached_catalog(self) -> List[PeerCandidate]:
        """Fetch catalog from relay, or return cached if fresh."""
        with self._cache_lock:
            if self._last_catalog is not None and self._catalog_timestamp is not None:
                age = datetime.utcnow() - self._catalog_timestamp
                if age < self.cache_ttl:
                    logger.debug(f"Using cached catalog ({age.total_seconds():.1f}s old)")
                    return self._last_catalog

        # Fetch from relay
        catalog = self._fetch_catalog_from_relay()

        with self._cache_lock:
            self._last_catalog = catalog
            self._catalog_timestamp = datetime.utcnow()

        return catalog

    def _fetch_catalog_from_relay(self) -> List[PeerCandidate]:
        """Fetch peer catalog from relay (abstract).

        In k=2+, this will call the real relay API. For k=1, we stub it.
        """
        # STUB: Return empty list (will be wired in k=2: integration tests)
        return []

    def _filter_eligibility(
        self, candidates: List[PeerCandidate], required_caps: List[str]
    ) -> List[PeerCandidate]:
        """Filter candidates by version + capability match."""
        eligible = []

        for peer in candidates:
            verdict = self._check_eligibility(peer, required_caps)
            if verdict == PeerEligibility.ELIGIBLE:
                eligible.append(peer)
            else:
                # Emit audit event for filtered-out candidate
                self._emit_audit_event(
                    event_type="discovery_peer_filtered",
                    peer_id=peer.peer_id,
                    verdict=verdict.value,
                )

        return eligible

    def _check_eligibility(self, peer: PeerCandidate, required_caps: List[str]) -> PeerEligibility:
        """Check if peer meets eligibility criteria."""
        # Check version
        if not self._version_in_range(peer.declared_version):
            return PeerEligibility.VERSION_MISMATCH

        # Check required capabilities
        peer_caps = set(peer.declared_capabilities)
        required_set = set(required_caps)
        if not required_set.issubset(peer_caps):
            return PeerEligibility.CAPABILITY_MISSING

        # Future: Check network reachability (k=2: integration tests)
        # For now, assume reachable

        return PeerEligibility.ELIGIBLE

    def _version_in_range(self, version: str) -> bool:
        """Check if version is in compatible range (stub: accept all for k=1)."""
        # STUB: Proper semantic versioning check will be added in k=2
        # For now, any version is acceptable
        return True

    def _rank_candidates(self, eligible: List[PeerCandidate]) -> List[DiscoveryCandidateRanking]:
        """Rank eligible candidates by learned quality + capability match."""
        ranked = []

        for rank, peer in enumerate(eligible, start=1):
            # Get learned quality score for this peer (or default 0.5)
            learned_score = self._peer_learning_scores.get(peer.peer_id, 0.5)

            # Compute capability-match bonus
            cap_match = len(set(peer.declared_capabilities) & self.OPTIONAL_CAPABILITIES) / len(
                self.OPTIONAL_CAPABILITIES
            )
            capability_bonus = cap_match * 0.2  # Up to +0.2 boost

            # Final confidence = learned score + capability bonus
            confidence = min(1.0, learned_score + capability_bonus)

            reasoning = (
                f"Version {peer.declared_version}, capabilities {len(peer.declared_capabilities)}, "
                f"learned_score {learned_score:.2f}, capability_match {cap_match:.2f}"
            )

            ranked.append(
                DiscoveryCandidateRanking(
                    peer_id=peer.peer_id,
                    rank=rank,
                    eligibility_verdict=PeerEligibility.ELIGIBLE,
                    confidence=confidence,
                    reasoning=reasoning,
                    learned_quality_score=learned_score,
                )
            )

        return ranked

    def _compute_confidence(self, total_peers: int, eligible_peers: int) -> float:
        """Compute overall confidence in discovery results."""
        if total_peers == 0:
            return 0.0

        eligibility_rate = eligible_peers / total_peers
        # Confidence = eligibility rate (higher is better)
        # Clamp to [0.0, 1.0]
        return min(1.0, max(0.0, eligibility_rate))

    def _get_cache_age_minutes(self) -> Optional[float]:
        """Get age of cached catalog in minutes."""
        if self._catalog_timestamp is None:
            return None
        age = datetime.utcnow() - self._catalog_timestamp
        return age.total_seconds() / 60.0

    def _hash_input(self, input: Dict[str, Any]) -> str:
        """Hash input for audit trail (never store raw input)."""
        try:
            # Scrub sensitive fields
            scrubbed = {k: v for k, v in input.items() if k not in ["password", "api_key"]}
            return hashlib.sha256(json.dumps(scrubbed, sort_keys=True).encode()).hexdigest()[:16]
        except Exception:
            return "unknown"

    def _hash_output(self, output: Dict[str, Any]) -> str:
        """Hash output for audit trail (never store raw output)."""
        try:
            # Include key summary only (not full candidate list)
            summary = {
                "success": output.get("success"),
                "candidates_count": len(output.get("candidates", [])),
                "eligible_count": output.get("eligible_count"),
                "catalog_size": output.get("catalog_size"),
            }
            return hashlib.sha256(json.dumps(summary, sort_keys=True).encode()).hexdigest()[:16]
        except Exception:
            return "unknown"

    def _sanitize_error_message(self, error_msg: str) -> str:
        """Sanitize error message to remove secrets/PII (GDPR Art. 32)."""
        import re

        # Remove common secret patterns
        patterns = [
            r"API[_-]?KEY\s*=\s*[^\s]+",
            r"PASSWORD\s*=\s*[^\s]+",
            r"SECRET\s*=\s*[^\s]+",
            r"TOKEN\s*=\s*[^\s]+",
            r"[a-zA-Z0-9]{32,}",  # Long hex/base64 strings (API keys)
        ]

        sanitized = error_msg
        for pattern in patterns:
            sanitized = re.sub(pattern, "***REDACTED***", sanitized, flags=re.IGNORECASE)

        return sanitized

    def _emit_audit_event(
        self,
        event_type: str,
        input_hash: Optional[str] = None,
        output_hash: Optional[str] = None,
        latency_ms: int = 0,
        **details: Any,
    ) -> None:
        """Emit audit event (GDPR Art. 30)."""
        if not self.audit_backend:
            return

        try:
            # Build event payload
            payload = {
                "event_type": event_type,
                "skill_id": self.skill_id,
                "tenant_id": self.tenant_id,
                "timestamp": datetime.utcnow().isoformat(),
            }

            if input_hash:
                payload["input_hash"] = input_hash
            if output_hash:
                payload["output_hash"] = output_hash
            if latency_ms > 0:
                payload["latency_ms"] = latency_ms

            payload.update(details)

            # Write to audit trail (immutable, hash-chained)
            self.audit_backend.write_event(event_type, payload)
        except Exception as e:
            logger.warning(f"Failed to emit audit event: {e}")

    def _emit_learning_event(
        self, feedback_type: str, peer_id: str, outcome: str, new_score: float
    ) -> None:
        """Emit learning event (ADR-0314)."""
        if not self.learning_backend:
            return

        try:
            event = {
                "event_type": "learning_feedback",
                "feedback_type": feedback_type,
                "skill_id": self.skill_id,
                "tenant_id": self.tenant_id,
                "peer_id": peer_id,
                "outcome": outcome,
                "new_score": new_score,
                "timestamp": datetime.utcnow().isoformat(),
            }
            self.learning_backend.emit_event(event)
        except Exception as e:
            logger.warning(f"Failed to emit learning event: {e}")
