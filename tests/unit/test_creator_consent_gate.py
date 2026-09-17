"""
Unit Tests: Creator 2.0 Consent Gate (SECURITY FIX #1 — GDPR Art. 6)

Tests fail-closed consent validation.
"""

import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from core.skills.creator.consent_gate import (
    ConsentGate,
    ConsentDenied,
    ConsentType,
    MockConsentProvider
)


class TestConsentGateLLMAPICall:
    """Test consent validation for LLM API calls"""

    def test_denies_without_consent(self):
        """Fail-closed: deny if user has not consented"""
        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        with pytest.raises(ConsentDenied):
            gate.validate_api_call(
                user_id='user_123',
                api_name='claude_api',
                input_data={'skill': 'test'}
            )

    def test_allows_with_consent(self):
        """Allow if user has consented"""
        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)

        result = gate.validate_api_call(
            user_id='user_123',
            api_name='claude_api',
            input_data={'skill': 'test'}
        )

        assert result is True

    def test_error_message_helpful(self):
        """Error message guides user to grant consent"""
        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        with pytest.raises(ConsentDenied) as exc_info:
            gate.validate_api_call(
                user_id='user_123',
                api_name='claude_api',
                input_data={'skill': 'test'}
            )

        error_msg = str(exc_info.value)
        assert 'user_123' in error_msg
        assert 'claude_api' in error_msg
        assert '/consent' in error_msg  # Points to consent endpoint

    def test_logs_denial_event(self):
        """Logs denial to audit trail"""
        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        try:
            gate.validate_api_call(
                user_id='user_123',
                api_name='claude_api',
                input_data={'skill': 'test'}
            )
        except ConsentDenied:
            pass

        # Verify denial was logged
        events = provider.logged_events
        assert len(events) == 1
        assert events[0]['event_type'] == 'consent_check_failed'
        assert events[0]['user_id'] == 'user_123'
        assert events[0]['result'] == 'DENIED'

    def test_logs_approval_event(self):
        """Logs approval to audit trail"""
        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)

        gate.validate_api_call(
            user_id='user_123',
            api_name='claude_api',
            input_data={'skill': 'test'}
        )

        # Verify approval was logged
        events = provider.logged_events
        assert len(events) == 1
        assert events[0]['event_type'] == 'consent_validated'
        assert events[0]['user_id'] == 'user_123'
        assert events[0]['result'] == 'ALLOWED'


class TestConsentGateDataStorage:
    """Test consent validation for data storage"""

    def test_denies_data_storage_without_consent(self):
        """Fail-closed: deny if user has not consented"""
        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        with pytest.raises(ConsentDenied):
            gate.validate_data_storage(user_id='user_123', data_size_bytes=1024)

    def test_allows_data_storage_with_consent(self):
        """Allow data storage if consented"""
        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)

        result = gate.validate_data_storage(user_id='user_123', data_size_bytes=1024)
        assert result is True


class TestConsentGateAnalytics:
    """Test consent validation for analytics"""

    def test_denies_analytics_without_consent(self):
        """Fail-closed: deny if user has not consented to analytics"""
        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        with pytest.raises(ConsentDenied):
            gate.validate_analytics(user_id='user_123', event_name='skill_created')

    def test_allows_analytics_with_consent(self):
        """Allow analytics if consented"""
        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)

        result = gate.validate_analytics(user_id='user_123', event_name='skill_created')
        assert result is True


class TestConsentProviderAbstraction:
    """Test that ConsentGate works with any provider"""

    def test_works_with_custom_provider(self):
        """ConsentGate abstracts from specific provider implementation"""

        class CustomConsentProvider:
            def __init__(self):
                self.consents = {}  # user_id -> {consent_type: bool}

            def has_granted(self, user_id, consent_type):
                return self.consents.get(user_id, {}).get(consent_type, False)

            def log_event(self, user_id, event_type, **kwargs):
                pass  # Custom logging implementation

        provider = CustomConsentProvider()
        provider.consents['user_123'] = {ConsentType.LLM_API_CALL: True}

        gate = ConsentGate(provider)

        # Should work with custom provider
        assert gate.validate_api_call('user_123', 'claude_api', {}) is True

        # But not for other users
        with pytest.raises(ConsentDenied):
            gate.validate_api_call('user_456', 'claude_api', {})


class TestConsentGateIntegration:
    """Integration tests for Creator skill workflow"""

    def test_creator_workflow_with_consent_gate(self):
        """Test realistic Creator skill workflow with consent validation"""

        provider = MockConsentProvider(has_consent=True)
        gate = ConsentGate(provider)

        # Simulate Creator skill workflow
        skill_spec = {
            'name': 'my_awesome_skill',
            'description': 'Does something cool',
            'code': 'def execute(input): return "result"'
        }

        # Step 1: Validate consent before API call
        try:
            gate.validate_api_call('user_123', 'claude_api', skill_spec)
            # Step 2: Now safe to call Claude API
            api_result = simulate_claude_api_call(skill_spec)
            assert api_result['success'] is True
        except ConsentDenied:
            # Consent denied; return error without calling API
            assert False, "Should have consent"

    def test_creator_workflow_without_consent_blocks_api_call(self):
        """Test that missing consent blocks API call"""

        provider = MockConsentProvider(has_consent=False)
        gate = ConsentGate(provider)

        skill_spec = {'name': 'my_skill', 'code': 'x=1'}

        # Should raise before API call
        with pytest.raises(ConsentDenied):
            gate.validate_api_call('user_123', 'claude_api', skill_spec)

        # Verify API was never called
        # (In real code, would use mock to verify)


def simulate_claude_api_call(skill_spec):
    """Mock Claude API call for testing"""
    return {
        'success': True,
        'skill_id': 'skill_xyz',
        'created_at': '2026-09-17T20:00:00Z'
    }
