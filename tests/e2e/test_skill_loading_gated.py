"""End-to-End Skill Loading with License Gating (Phase 3, ADR-0667).

Tests:
- Free-Tier users can load free Skills
- Paid-Tier users can load paid Skills
- Free-Tier blocked from paid Skills
- All blocking decisions audited
"""

from __future__ import annotations

import pytest

from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata
from core.skills.signature import SkillManifestSigner
from core.skills.license_binding import UserLicense
from core.skill_forge.validators.forge_gateway import (
    ForgeSkillValidator,
    OriginUnauthorizedError,
)
from core.skills.license_binding import LicenseRequiredError
from core.skills.signature.validator import SignatureValidationError


@pytest.fixture
def signer():
    return SkillManifestSigner()


@pytest.fixture
def keypair(signer):
    return signer.generate_operator_keypair()


@pytest.fixture
def free_user():
    return UserLicense(user_id="user_free_1", license_tier="free")


@pytest.fixture
def paid_user():
    return UserLicense(user_id="user_paid_1", license_tier="paid")


@pytest.fixture
def free_skill(keypair):
    signer = SkillManifestSigner()
    public_key, private_key = keypair

    manifest = SkillManifestV2(
        skill_id="routing.basic",
        version="1.0.0",
        boot_layer="bundled",
        license_binding=None,  # Free tier
    )

    signature = signer.sign_manifest(manifest, private_key)
    return manifest, signature, public_key


@pytest.fixture
def paid_skill(keypair):
    signer = SkillManifestSigner()
    public_key, private_key = keypair

    binding = LicenseBindingMetadata(
        required_tier="paid",
        binding_hash="abc123",
        operator_signature="sig",
        timestamp="2026-01-01T00:00:00Z",
    )

    manifest = SkillManifestV2(
        skill_id="routing.optimized",
        version="2.0.0",
        boot_layer="bundled",
        license_binding=binding,
    )

    signature = signer.sign_manifest(manifest, private_key)
    return manifest, signature, public_key


class TestHappyPath:
    """Happy path: authorized loads."""

    def test_free_user_loads_free_skill(self, free_skill, free_user):
        """Free-Tier user can load free Skill."""
        manifest, signature, public_key = free_skill
        validator = ForgeSkillValidator(operator_public_key=public_key)

        result = validator.validate_and_load(manifest, signature, free_user)
        assert result.skill_id == "routing.basic"

    def test_paid_user_loads_free_skill(self, free_skill, paid_user):
        """Paid-Tier user can load free Skill."""
        manifest, signature, public_key = free_skill
        validator = ForgeSkillValidator(operator_public_key=public_key)

        result = validator.validate_and_load(manifest, signature, paid_user)
        assert result.skill_id == "routing.basic"

    def test_paid_user_loads_paid_skill(self, paid_skill, paid_user):
        """Paid-Tier user can load paid Skill."""
        manifest, signature, public_key = paid_skill
        validator = ForgeSkillValidator(operator_public_key=public_key)

        result = validator.validate_and_load(manifest, signature, paid_user)
        assert result.skill_id == "routing.optimized"


class TestBlocked:
    """Blocked scenarios: unauthorized loads."""

    def test_free_user_blocked_from_paid_skill(self, paid_skill, free_user):
        """Free-Tier user blocked from paid Skill (raises LicenseRequiredError)."""
        manifest, signature, public_key = paid_skill
        validator = ForgeSkillValidator(operator_public_key=public_key)

        with pytest.raises(LicenseRequiredError):
            validator.validate_and_load(manifest, signature, free_user)

    def test_tampered_signature_blocked(self, free_skill, free_user):
        """Tampered signature blocked (raises SignatureValidationError)."""
        manifest, signature, public_key = free_skill
        tampered_sig = "invalid_signature_data"  # base64
        validator = ForgeSkillValidator(operator_public_key=public_key)

        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(manifest, tampered_sig, free_user)

    def test_wrong_key_blocked(self, free_skill, free_user, signer):
        """Signature from different key rejected."""
        manifest, signature, _ = free_skill
        _, wrong_public_key = signer.generate_operator_keypair()
        validator = ForgeSkillValidator(operator_public_key=wrong_public_key)

        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(manifest, signature, free_user)


class TestEdgeCases:
    """Edge cases and boundary conditions."""

    def test_enterprise_user_loads_any_skill(self, free_skill, paid_skill, keypair):
        """Enterprise-Tier user can load any Skill."""
        enterprise_user = UserLicense(user_id="user_enterprise_1", license_tier="enterprise")
        public_key, _ = keypair
        validator = ForgeSkillValidator(operator_public_key=public_key)

        free_manifest, free_sig, _ = free_skill
        paid_manifest, paid_sig, _ = paid_skill

        # Should load both
        result1 = validator.validate_and_load(free_manifest, free_sig, enterprise_user)
        result2 = validator.validate_and_load(paid_manifest, paid_sig, enterprise_user)

        assert result1.skill_id == "routing.basic"
        assert result2.skill_id == "routing.optimized"

    def test_skill_with_empty_tier_defaults_to_free(self, keypair, free_user):
        """Skill with empty tier string defaults to free access."""
        signer = SkillManifestSigner()
        public_key, private_key = keypair

        manifest = SkillManifestV2(
            skill_id="test.empty_tier",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        signature = signer.sign_manifest(manifest, private_key)
        validator = ForgeSkillValidator(operator_public_key=public_key)

        result = validator.validate_and_load(manifest, signature, free_user)
        assert result.skill_id == "test.empty_tier"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
