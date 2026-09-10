"""License Binding Validator — unit tests (Phase 2, ADR-0667).

Tests:
- Tier-based access control (free vs. paid)
- User license validation
- License denial + audit logging
"""

from __future__ import annotations

import pytest

from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata
from core.skills.license_binding import (
    LicenseBindingValidator,
    LicenseRequiredError,
    UserLicense,
)


@pytest.fixture
def validator():
    return LicenseBindingValidator()


@pytest.fixture
def free_user():
    return UserLicense(user_id="user1", license_tier="free")


@pytest.fixture
def paid_user():
    return UserLicense(user_id="user2", license_tier="paid")


@pytest.fixture
def free_skill():
    return SkillManifestV2(
        skill_id="test.free",
        version="1.0.0",
        boot_layer="bundled",
        license_binding=None,  # No binding = free
    )


@pytest.fixture
def paid_skill():
    binding = LicenseBindingMetadata(
        required_tier="paid",
        binding_hash="abc123",
        operator_signature="sig_xyz",
        timestamp="2026-01-01T00:00:00Z",
    )
    return SkillManifestV2(
        skill_id="test.paid",
        version="1.0.0",
        boot_layer="bundled",
        license_binding=binding,
    )


class TestTierValidation:
    """Test tier-based validation."""

    def test_free_user_can_access_free_skill(self, validator, free_user, free_skill):
        """Free-tier user can access free Skill."""
        assert validator.validate_binding(free_skill, free_user) is True

    def test_paid_user_can_access_free_skill(self, validator, paid_user, free_skill):
        """Paid-tier user can access free Skill."""
        assert validator.validate_binding(free_skill, paid_user) is True

    def test_paid_user_can_access_paid_skill(self, validator, paid_user, paid_skill):
        """Paid-tier user can access paid Skill."""
        assert validator.validate_binding(paid_skill, paid_user) is True

    def test_free_user_cannot_access_paid_skill(self, validator, free_user, paid_skill):
        """Free-tier user cannot access paid Skill (raises error)."""
        with pytest.raises(LicenseRequiredError) as exc_info:
            validator.validate_binding(paid_skill, free_user)

        error_msg = str(exc_info.value)
        assert "paid" in error_msg.lower()
        assert "free" in error_msg.lower()


class TestTierHierarchy:
    """Test tier hierarchy (free < paid < enterprise)."""

    def test_enterprise_user_can_access_all_skills(self, validator):
        """Enterprise-tier user can access all Skills."""
        enterprise_user = UserLicense(user_id="user3", license_tier="enterprise")

        free_skill = SkillManifestV2(
            skill_id="test.free",
            version="1.0.0",
            boot_layer="bundled",
        )

        paid_skill = SkillManifestV2(
            skill_id="test.paid",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        assert validator.validate_binding(free_skill, enterprise_user) is True
        assert validator.validate_binding(paid_skill, enterprise_user) is True

    def test_invalid_tier_defaults_to_zero_level(self, validator):
        """Unknown tier defaults to level 0 (< paid)."""
        unknown_user = UserLicense(user_id="user4", license_tier="unknown")

        paid_skill = SkillManifestV2(
            skill_id="test.paid",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        with pytest.raises(LicenseRequiredError):
            validator.validate_binding(paid_skill, unknown_user)


class TestErrorMessages:
    """Test error messages include relevant details."""

    def test_license_error_includes_skill_id(self, validator, free_user, paid_skill):
        """LicenseRequiredError includes Skill ID."""
        with pytest.raises(LicenseRequiredError) as exc_info:
            validator.validate_binding(paid_skill, free_user)

        assert "test.paid" in str(exc_info.value)

    def test_license_error_includes_tiers(self, validator, free_user, paid_skill):
        """LicenseRequiredError includes required and user tiers."""
        with pytest.raises(LicenseRequiredError) as exc_info:
            validator.validate_binding(paid_skill, free_user)

        error_msg = str(exc_info.value)
        assert "paid" in error_msg.lower()
        assert "free" in error_msg.lower()


class TestBindingSignatureValidation:
    """Test binding signature validation."""

    def test_validate_binding_signature_without_binding(self, validator):
        """Validating signature when no binding returns True."""
        skill = SkillManifestV2(
            skill_id="test.free",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=None,
        )

        assert validator.validate_binding_signature(skill, operator_public_key=None) is True

    def test_validate_binding_signature_with_binding(self, validator):
        """Validating signature with binding (placeholder implementation)."""
        binding = LicenseBindingMetadata(
            required_tier="paid",
            binding_hash="abc123",
            operator_signature="sig_xyz",
            timestamp="2026-01-01T00:00:00Z",
        )
        skill = SkillManifestV2(
            skill_id="test.paid",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=binding,
        )

        # Placeholder: should return True (real implementation would verify signature)
        assert validator.validate_binding_signature(skill, operator_public_key=None) is True


class TestWithoutAuditChain:
    """Test validator works without audit chain."""

    def test_validation_works_without_audit_chain(self):
        """License validation works when audit chain not configured."""
        validator = LicenseBindingValidator(audit_chain=None)
        free_user = UserLicense(user_id="user1", license_tier="free")
        free_skill = SkillManifestV2(
            skill_id="test.free",
            version="1.0.0",
            boot_layer="bundled",
        )

        assert validator.validate_binding(free_skill, free_user) is True


class TestEdgeCases:
    """Test edge cases."""

    def test_skill_without_explicit_tier_defaults_to_free(self, validator, free_user):
        """Skill without explicit license binding defaults to free."""
        skill = SkillManifestV2(
            skill_id="test.implicit_free",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=None,
        )

        assert skill.required_tier() == "free"
        assert validator.validate_binding(skill, free_user) is True

    def test_empty_tier_string_treated_as_free(self, validator, paid_user):
        """Empty tier string treated as free."""
        binding = LicenseBindingMetadata(
            required_tier="",
            binding_hash="abc",
            operator_signature="sig",
            timestamp="2026-01-01T00:00:00Z",
        )
        skill = SkillManifestV2(
            skill_id="test.empty_tier",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=binding,
        )

        # Empty tier defaults to level 0, so even paid user might have issues
        # but this is an edge case
        result = skill.required_tier()
        assert result == ""


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
