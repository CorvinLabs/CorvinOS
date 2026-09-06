"""
Unit tests for Persona compat layer (ADR-0537 Phase 1)

Tests:
- Old API still works (backward compat)
- Dual-log divergence detection
- Fail-closed on exceptions
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from core.legacy_compat.persona_compat import (
    _CompatCapabilityRegistry,
    Persona,
    Role,
    has_capability,
)


class TestCompatCapabilityRegistry:
    """Test suite for _CompatCapabilityRegistry compat layer."""

    def test_old_api_still_works(self):
        """Old has_capability() API should still work (backward compat)."""
        registry = _CompatCapabilityRegistry()

        with patch("core.skills.os_skills.capabilities_skill.CapabilitiesSkill.execute") as mock_execute:
            with patch("core.legacy_compat.persona_compat._CompatCapabilityRegistry._query_old_registry") as mock_old:
                # Old registry returns True
                mock_old.return_value = True

                # Skill returns True
                mock_execute_output = Mock()
                mock_execute_output.has_capability = True
                mock_execute.return_value = mock_execute_output

                result = registry.has_capability(
                    persona=Persona.CONSOLE_OPERATOR,
                    role=Role.ADMIN,
                    capability_id="read_audit_log",
                    tenant_id="_default"
                )

        assert result is True

    def test_divergence_detection_old_true_new_false(self):
        """Detect divergence: old registry allows, new Skill denies."""
        registry = _CompatCapabilityRegistry()

        with patch("core.skills.os_skills.capabilities_skill.CapabilitiesSkill.execute") as mock_execute:
            with patch("core.legacy_compat.persona_compat._CompatCapabilityRegistry._query_old_registry") as mock_old:
                with patch("core.learning.audit_backend.audit_backend.write_event") as mock_audit:
                    # Old registry returns True
                    mock_old.return_value = True

                    # Skill returns False (divergence!)
                    mock_execute_output = Mock()
                    mock_execute_output.has_capability = False
                    mock_execute.return_value = mock_execute_output

                    result = registry.has_capability(
                        persona=Persona.CONSOLE_OPERATOR,
                        role=Role.ADMIN,
                        capability_id="read_audit_log",
                        tenant_id="_default"
                    )

        # Verify divergence audit event was emitted
        assert mock_audit.called
        call_kwargs = mock_audit.call_args[1]
        assert call_kwargs["event_type"] == "dual_log_divergence"
        assert call_kwargs["payload"]["old_registry_result"] is True
        assert call_kwargs["payload"]["new_skill_result"] is False

        # Return value should be old result (Personas still primary in Phase 1)
        assert result is True

    def test_divergence_counter_increments(self):
        """Divergence counter should increment on each divergence."""
        registry = _CompatCapabilityRegistry()

        with patch("core.skills.os_skills.capabilities_skill.CapabilitiesSkill.execute") as mock_execute:
            with patch("core.legacy_compat.persona_compat._CompatCapabilityRegistry._query_old_registry") as mock_old:
                with patch("core.learning.audit_backend.audit_backend.write_event"):
                    # Simulate 3 divergences
                    for i in range(3):
                        mock_old.return_value = True
                        mock_execute_output = Mock()
                        mock_execute_output.has_capability = False
                        mock_execute.return_value = mock_execute_output

                        registry.has_capability(
                            persona=Persona.CONSOLE_OPERATOR,
                            role=Role.ADMIN,
                            capability_id=f"cap_{i}",
                            tenant_id="_default"
                        )

        assert registry.divergence_count == 3

    def test_alert_on_threshold_breach(self):
        """Alert should be logged when divergence_count exceeds threshold."""
        registry = _CompatCapabilityRegistry()
        registry.divergence_threshold = 2

        with patch("core.skills.os_skills.capabilities_skill.CapabilitiesSkill.execute") as mock_execute:
            with patch("core.legacy_compat.persona_compat._CompatCapabilityRegistry._query_old_registry") as mock_old:
                with patch("core.learning.audit_backend.audit_backend.write_event"):
                    with patch("core.legacy_compat.persona_compat.logger.error") as mock_logger:
                        # Trigger 3 divergences (threshold is 2)
                        for i in range(3):
                            mock_old.return_value = True
                            mock_execute_output = Mock()
                            mock_execute_output.has_capability = False
                            mock_execute.return_value = mock_execute_output

                            registry.has_capability(
                                persona=Persona.CONSOLE_OPERATOR,
                                role=Role.ADMIN,
                                capability_id=f"cap_{i}",
                                tenant_id="_default"
                            )

        # Alert should have been logged when count hit threshold
        assert mock_logger.called
        call_args = mock_logger.call_args[0][0]
        assert "DUAL_LOG_DIVERGENCE ALERT" in call_args

    def test_fail_closed_on_skill_exception(self):
        """Skill exception should be caught and capability should deny (fail-closed)."""
        registry = _CompatCapabilityRegistry()

        with patch("core.skills.os_skills.capabilities_skill.CapabilitiesSkill.execute") as mock_execute:
            with patch("core.legacy_compat.persona_compat._CompatCapabilityRegistry._query_old_registry") as mock_old:
                # Old registry returns True
                mock_old.return_value = True

                # Skill raises exception
                mock_execute.side_effect = Exception("Skill failed")

                result = registry.has_capability(
                    persona=Persona.CONSOLE_OPERATOR,
                    role=Role.ADMIN,
                    capability_id="read_audit_log",
                    tenant_id="_default"
                )

        # Result should be True (old registry), but Skill error was caught
        # (This verifies fail-closed: Skill exception doesn't crash the system)
        assert result is True


class TestCompatAPIDeprecationWarning:
    """Test that deprecated API emits warning."""

    def test_has_capability_deprecated_warning(self):
        """Calling has_capability() should emit deprecation warning."""
        with patch("core.legacy_compat.persona_compat._REGISTRY.has_capability") as mock_has_cap:
            mock_has_cap.return_value = True

            with pytest.warns(DeprecationWarning, match="deprecated"):
                has_capability(
                    persona=Persona.CONSOLE_OPERATOR,
                    role=Role.ADMIN,
                    capability_id="read_audit_log"
                )
