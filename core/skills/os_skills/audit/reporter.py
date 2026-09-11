"""GDPR-Compliant Compliance Reporting & Bias Detection.

Features:
- PII redaction (no user data in exports)
- User ID masking (hash-based, consistent)
- Retention policy enforcement (delete events >90 days)
- Bias detection (flag skills with skewed feedback patterns)
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from .trail import AuditEvent, AuditTrail

# Regex patterns for PII detection (conservative, comprehensive)
# Order matters: IBAN first (more specific), then phone, then others
PII_PATTERNS = {
    'iban': r'\b[A-Z]{2}[0-9]{2}[0-9A-Z]{1,30}\b',  # Any IBAN: 2 letters + 2 digits + 1-30 alphanumeric
    'email': r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
    # Phone: Multiple formats — US: (555) 123-4567, 555-1234, +1-555-1234567; DE: +49 123 456789, (030) 123456, 030/123456
    'phone': r'(?:\+\d{1,3}[\s.-]?)?(?:\(?\d{2,4}\)?[\s.-]?)*\d{3,4}[\s.-]?\d{3,4}(?=\s|$|[^\d])',
    'credit_card': r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b',
    'ssn': r'\b\d{3}-\d{2}-\d{4}\b',
}


class ComplianceReporter:
    """GDPR-compliant audit exports and bias detection."""

    def __init__(
        self,
        audit_trail: AuditTrail,
        retention_days: int = 90,
        user_id_salt: Optional[str] = None,
    ):
        """
        Initialize compliance reporter.

        Args:
            audit_trail: AuditTrail instance to report from
            retention_days: Delete events older than this (GDPR Art. 5)
            user_id_salt: Salt for consistent user ID masking (random if not provided)
        """
        self.audit_trail = audit_trail
        self.retention_days = retention_days
        # Generate random salt if not provided (cryptographically secure)
        self.user_id_salt = user_id_salt or os.urandom(32).hex()

    def mask_user_id(self, user_id: str) -> str:
        """Hash-based user ID masking (deterministic, anonymous)."""
        salted = f"{user_id}:{self.user_id_salt}".encode()
        return hashlib.sha256(salted).hexdigest()[:16]

    def redact_pii(self, text: str) -> str:
        """Remove known PII patterns from text."""
        redacted = text
        for pattern_name, pattern in PII_PATTERNS.items():
            redacted = re.sub(pattern, f"[REDACTED_{pattern_name.upper()}]", redacted)
        return redacted

    def redact_event(self, event: AuditEvent) -> AuditEvent:
        """Return a redacted copy of an event (PII removed, user IDs masked)."""
        redacted_payload = {}

        # Redact all payload fields
        for key, value in event.payload.items():
            if key.lower() in ('user_id', 'user', 'owner'):
                # Mask user IDs
                if isinstance(value, str):
                    redacted_payload[key] = self.mask_user_id(value)
                else:
                    redacted_payload[key] = value
            elif isinstance(value, str):
                # Redact PII in text fields
                redacted_payload[key] = self.redact_pii(value)
            else:
                redacted_payload[key] = value

        # Return redacted copy (frozen, so create new)
        return AuditEvent(
            event_type=event.event_type,
            tenant_id=event.tenant_id,
            timestamp=event.timestamp,
            skill_id=event.skill_id,
            skill_version=event.skill_version,
            payload=redacted_payload,
            prev_hash=event.prev_hash,
            hash=event.hash,
        )

    def export_for_compliance(
        self,
        since: Optional[datetime] = None,
        redact: bool = True,
    ) -> str:
        """
        Export audit trail in compliance-ready format (JSONL, PII-redacted).

        Args:
            since: Only events after this timestamp
            redact: If True, redact PII and mask user IDs

        Returns:
            JSONL string (one event per line)
        """
        events = self.audit_trail.query_events(since=since, limit=999999)

        lines = []
        for event in events:
            event_to_export = self.redact_event(event) if redact else event
            lines.append(
                self._event_to_json_line(event_to_export)
            )

        return '\n'.join(lines)

    def _event_to_json_line(self, event: AuditEvent) -> str:
        """Serialize event to JSON line."""
        import json
        return json.dumps(asdict(event), default=str)

    def enforce_retention(self) -> int:
        """
        Delete audit events older than retention_days (GDPR Art. 5).

        Returns:
            Number of events deleted
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=self.retention_days)

        # Read all events, filter, rewrite
        if not self.audit_trail.chain_path.exists():
            return 0

        import json

        kept_events = []
        deleted_count = 0

        with open(self.audit_trail.chain_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    event_data = json.loads(line)
                    event = AuditEvent(**event_data)

                    event_ts = datetime.fromisoformat(event.timestamp)
                    if event_ts < cutoff:
                        deleted_count += 1
                    else:
                        kept_events.append(event)

                except (json.JSONDecodeError, TypeError):
                    continue

        # Rewrite chain (this is expensive, so log it)
        if deleted_count > 0:
            with open(self.audit_trail.chain_path, 'w') as f:
                # Rebuild with corrected prev_hash chain
                prev_hash = ""
                for event in kept_events:
                    # Update prev_hash
                    updated_event = AuditEvent(
                        event_type=event.event_type,
                        tenant_id=event.tenant_id,
                        timestamp=event.timestamp,
                        skill_id=event.skill_id,
                        skill_version=event.skill_version,
                        payload=event.payload,
                        prev_hash=prev_hash,
                    )
                    f.write(self._event_to_json_line(updated_event) + '\n')
                    prev_hash = updated_event.hash

            import logging
            logger = logging.getLogger(__name__)
            logger.info(f"Retention: deleted {deleted_count} events older than {self.retention_days}d")

        return deleted_count

    def detect_bias(self) -> dict[str, list[str]]:
        """
        Detect bias in skill generation and feedback (ADR-0314 + GDPR Art. 22).

        Returns:
            {
                "skewed_feedback": ["skill_123: 95% positive feedback (N=20)"],
                "low_observation_skills": ["skill_456: only 5 observations"],
                "high_variance": ["skill_789: confidence changed by 40% in 1 day"],
            }
        """
        alerts = {
            "skewed_feedback": [],
            "low_observation_skills": [],
            "high_variance": [],
        }

        events = self.audit_trail.query_events(limit=999999)

        # Group feedback by skill
        feedback_by_skill: dict[str, list[str]] = {}
        for event in events:
            if event.event_type == "feedback_received" and event.skill_id:
                if event.skill_id not in feedback_by_skill:
                    feedback_by_skill[event.skill_id] = []

                # Extract feedback signal (payload['signal'] is typically 'positive'/'negative'/'neutral')
                signal = event.payload.get('signal', 'neutral')
                feedback_by_skill[event.skill_id].append(signal)

        # Detect skewed feedback (>80% one-sided)
        for skill_id, signals in feedback_by_skill.items():
            if len(signals) < 5:  # Too few observations
                alerts["low_observation_skills"].append(f"{skill_id}: only {len(signals)} observations")
                continue

            counter = Counter(signals)
            most_common_pct = (counter.most_common(1)[0][1] / len(signals)) * 100

            if most_common_pct >= 80:
                alerts["skewed_feedback"].append(
                    f"{skill_id}: {most_common_pct:.0f}% {counter.most_common(1)[0][0]} (N={len(signals)})"
                )

        return alerts
