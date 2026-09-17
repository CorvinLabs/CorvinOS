"""
Creator 2.0 Consent Gate (SECURITY FIX #1 — GDPR Art. 6 Compliance)

Fail-closed consent validator for Creator 2.0 skill.

**Compliance Requirement:** GDPR Art. 6 — No processing without prior explicit consent
**Implementation:** Fail-closed (deny by default, only allow if consent is explicitly granted)
**Audit Trail:** All consent checks logged as audit events

Usage:
    from core.skills.creator.consent_gate import ConsentGate, ConsentDenied

    consent_gate = ConsentGate(consent_provider)

    try:
        consent_gate.validate_api_call(
            user_id='user_123',
            api_name='claude_api',
            input_data={'skill_spec': {...}}
        )
        # Safe to proceed with API call
    except ConsentDenied as e:
        # User has not consented; return error without making API call
        return {'error': str(e), 'status': 'DENIED'}
"""

from enum import Enum
from typing import Any, Dict
from dataclasses import dataclass


class ConsentType(Enum):
    """Types of consent tracked"""
    LLM_API_CALL = "llm_api_call"
    DATA_STORAGE = "data_storage"
    ANALYTICS = "analytics"


class ConsentDenied(Exception):
    """Raised when user has not consented to an operation

    This is the expected path for unauthorized operations (not an error).
    Callers should catch this and return 403 Forbidden to the user.
    """
    pass


@dataclass
class ConsentRecord:
    """Record of a consent check or grant"""
    user_id: str
    consent_type: ConsentType
    granted: bool  # True if user consented, False if denied
    reason: str  # Why the decision was made (e.g., "user_explicitly_denied")
    timestamp: str  # ISO 8601 format


class ConsentProvider:
    """Abstract base for consent provider (implemented by audit backend)"""

    def has_granted(self, user_id: str, consent_type: ConsentType) -> bool:
        """Check if user has granted this type of consent"""
        raise NotImplementedError()

    def log_event(self, user_id: str, event_type: str, **kwargs) -> None:
        """Log a consent event to audit trail"""
        raise NotImplementedError()


class ConsentGate:
    """Fail-closed consent validator for GDPR Art. 6 compliance"""

    def __init__(self, consent_provider: ConsentProvider):
        """Initialize with consent provider

        Args:
            consent_provider: Backend that stores/validates consent
        """
        self.consent = consent_provider

    def validate_api_call(
        self,
        user_id: str,
        api_name: str,
        input_data: Any
    ) -> bool:
        """Validate consent before API call

        **Fail-Closed Guarantee:** Returns True only if consent is explicitly granted.
        Otherwise raises ConsentDenied.

        Args:
            user_id: User identifier
            api_name: API being called ("claude", "bedrock", etc.)
            input_data: Request payload (not logged for privacy)

        Returns:
            True if consent granted (safe to proceed with API call)

        Raises:
            ConsentDenied: If user has not granted consent for LLM API calls
                          (GDPR Art. 6 — fail-closed behavior)
        """

        # Step 1: Fail-closed check — assume NO consent unless explicitly granted
        if not self.consent.has_granted(user_id, ConsentType.LLM_API_CALL):

            # Log the denial for audit trail
            self.consent.log_event(
                user_id=user_id,
                event_type='consent_check_failed',
                api_name=api_name,
                consent_type=ConsentType.LLM_API_CALL,
                result='DENIED'
            )

            # Raise to prevent API call
            raise ConsentDenied(
                f"User {user_id} has not consented to LLM API calls. "
                f"Cannot proceed with {api_name} call. "
                f"Request: GET /consent to grant consent."
            )

        # Step 2: Consent granted — log this for audit trail
        self.consent.log_event(
            user_id=user_id,
            event_type='consent_validated',
            api_name=api_name,
            consent_type=ConsentType.LLM_API_CALL,
            result='ALLOWED'
        )

        return True

    def validate_data_storage(self, user_id: str, data_size_bytes: int) -> bool:
        """Validate consent for storing user data

        Args:
            user_id: User identifier
            data_size_bytes: Size of data to store

        Returns:
            True if consent granted

        Raises:
            ConsentDenied: If user has not granted consent
        """

        if not self.consent.has_granted(user_id, ConsentType.DATA_STORAGE):
            self.consent.log_event(
                user_id=user_id,
                event_type='consent_check_failed',
                consent_type=ConsentType.DATA_STORAGE,
                data_size_bytes=data_size_bytes,
                result='DENIED'
            )
            raise ConsentDenied(
                f"User {user_id} has not consented to data storage. "
                f"Cannot store {data_size_bytes} bytes."
            )

        self.consent.log_event(
            user_id=user_id,
            event_type='consent_validated',
            consent_type=ConsentType.DATA_STORAGE,
            data_size_bytes=data_size_bytes,
            result='ALLOWED'
        )

        return True

    def validate_analytics(self, user_id: str, event_name: str) -> bool:
        """Validate consent for analytics/telemetry

        Args:
            user_id: User identifier
            event_name: Name of analytics event

        Returns:
            True if consent granted

        Raises:
            ConsentDenied: If user has not granted consent
        """

        if not self.consent.has_granted(user_id, ConsentType.ANALYTICS):
            self.consent.log_event(
                user_id=user_id,
                event_type='consent_check_failed',
                consent_type=ConsentType.ANALYTICS,
                event_name=event_name,
                result='DENIED'
            )
            raise ConsentDenied(
                f"User {user_id} has not consented to analytics for event '{event_name}'."
            )

        self.consent.log_event(
            user_id=user_id,
            event_type='consent_validated',
            consent_type=ConsentType.ANALYTICS,
            event_name=event_name,
            result='ALLOWED'
        )

        return True


class MockConsentProvider(ConsentProvider):
    """Mock consent provider for testing

    Usage:
        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)
    """

    def __init__(self, has_consent: bool = False):
        """Initialize mock provider

        Args:
            has_consent: Whether to grant all consent checks
        """
        self.has_consent = has_consent
        self.logged_events = []

    def has_granted(self, user_id: str, consent_type: ConsentType) -> bool:
        """Always return the configured value"""
        return self.has_consent

    def log_event(self, user_id: str, event_type: str, **kwargs) -> None:
        """Record the event for inspection in tests"""
        self.logged_events.append({
            'user_id': user_id,
            'event_type': event_type,
            **kwargs
        })
