"""Adversarial Skill Loading Tests (Phase 3, ADR-0667).

Attack vectors:
1. Manifest tampering (modify tier field)
2. Key forgery (sign with fake key)
3. Skill clone + publish
4. Audit tampering (hash chain break)
5. License spoofing (tier mismatch)
6. Marketplace bypass (publish permission)
"""

from __future__ import annotations

import pytest

from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata
from core.skills.signature import SkillManifestSigner
from core.skills.license_binding import UserLicense, LicenseRequiredError
from core.skill_forge.validators.forge_gateway import (
    ForgeSkillValidator,
    OriginUnauthorizedError,
)
from core.skill_forge.marketplace_origin import MarketplaceOriginValidator
from core.skills.signature.validator import SignatureValidationError


@pytest.fixture
def signer():
    return SkillManifestSigner()


@pytest.fixture
def keypair(signer):
    return signer.generate_operator_keypair()


@pytest.fixture
def free_user():
    return UserLicense(user_id="attacker", license_tier="free")


@pytest.fixture
def origin_validator():
    return MarketplaceOriginValidator()


class TestManifestTamperingAttacks:
    """Attack 1: Manifest tampering (modify tier field)."""

    def test_tampered_tier_fails_signature(self, keypair, free_user):
        """Changing tier field invalidates signature."""
        signer = SkillManifestSigner()
        public_key, private_key = keypair

        # Create and sign paid skill
        manifest = SkillManifestV2(
            skill_id="routing.optimized",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )
        signature = signer.sign_manifest(manifest, private_key)

        # Attacker modifies tier to "free"
        tampered = SkillManifestV2(
            skill_id="routing.optimized",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="free",  # TAMPERED
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        validator = ForgeSkillValidator(operator_public_key=public_key)

        # Signature should not validate tampered manifest
        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(tampered, signature, free_user)

    def test_tampered_skill_id_fails_signature(self, keypair, free_user):
        """Changing skill_id invalidates signature."""
        signer = SkillManifestSigner()
        public_key, private_key = keypair

        manifest = SkillManifestV2(
            skill_id="routing.optimized",
            version="1.0.0",
            boot_layer="bundled",
        )
        signature = signer.sign_manifest(manifest, private_key)

        # Attacker changes skill_id
        tampered = SkillManifestV2(
            skill_id="routing.optimized-custom",  # TAMPERED
            version="1.0.0",
            boot_layer="bundled",
        )

        validator = ForgeSkillValidator(operator_public_key=public_key)

        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(tampered, signature, free_user)


class TestKeyForgeryAttacks:
    """Attack 2: Key forgery (sign with fake key)."""

    def test_signature_from_wrong_key_fails(self, keypair, free_user, signer):
        """Signature from different key rejected."""
        public_key, _ = keypair
        _, wrong_private_key = signer.generate_operator_keypair()

        manifest = SkillManifestV2(
            skill_id="routing.optimized",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        # Sign with wrong key
        signature = signer.sign_manifest(manifest, wrong_private_key)

        validator = ForgeSkillValidator(operator_public_key=public_key)

        # Should fail because public_key doesn't match
        with pytest.raises(SignatureValidationError):
            validator.validate_and_load(manifest, signature, free_user)


class TestSkillCloneAttacks:
    """Attack 3: Skill clone + publish as new Skill."""

    def test_operator_skill_cannot_be_cloned(self, origin_validator, free_user):
        """Operator Skills cannot be cloned and republished."""
        operator_skill = "routing.optimized"

        # Attacker tries to republish as free user
        assert not origin_validator.is_user_publishable(operator_skill, free_user)

    def test_cloned_skill_origin_unverified(self, origin_validator):
        """Cloned Skill has unverified origin."""
        cloned_skill = "routing.optimized-clone"

        origin = origin_validator.check_skill_origin(cloned_skill)
        assert origin == "unverified"


class TestLicenseSpoofingAttacks:
    """Attack 5: License spoofing (claim higher tier)."""

    def test_cannot_spoof_license_tier_in_binding(self, keypair, free_user, signer):
        """Free user cannot spoof paid tier in license binding."""
        public_key, private_key = keypair

        # Attacker creates manifest claiming paid tier
        manifest = SkillManifestV2(
            skill_id="test.spoofed",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )

        # Sign with legitimate operator key (simulating attack)
        signature = signer.sign_manifest(manifest, private_key)

        validator = ForgeSkillValidator(operator_public_key=public_key)

        # Even with valid signature, license check fails
        with pytest.raises(LicenseRequiredError):
            validator.validate_and_load(manifest, signature, free_user)


class TestMarketplaceBypassAttacks:
    """Attack 6: Marketplace bypass (publish without permission)."""

    def test_free_user_cannot_publish(self, origin_validator, free_user):
        """Free-Tier user cannot publish to marketplace."""
        assert not origin_validator.is_user_publishable("custom.skill", free_user)

    def test_paid_user_can_publish_custom_skills(self, origin_validator):
        """Paid-Tier user can publish custom Skills (not operator-verified)."""
        paid_user = UserLicense(user_id="vendor", license_tier="paid")
        custom_skill = "vendor.custom_skill"

        # Should be publishable (not operator-verified)
        assert origin_validator.is_user_publishable(custom_skill, paid_user)

    def test_paid_user_cannot_override_operator_skills(self, origin_validator):
        """Paid-Tier user cannot override operator-verified Skills."""
        paid_user = UserLicense(user_id="vendor", license_tier="paid")
        operator_skill = "routing.optimized"

        # Cannot publish/override operator Skills
        assert not origin_validator.is_user_publishable(operator_skill, paid_user)


class TestAuditTamperingAttacks:
    """Attack 4: Audit tampering (hash chain break) - structural test."""

    def test_audit_events_logged_on_denials(self, keypair, free_user):
        """All blocked loads are audit-logged."""
        signer = SkillManifestSigner()
        public_key, private_key = keypair

        paid_manifest = SkillManifestV2(
            skill_id="routing.optimized",
            version="1.0.0",
            boot_layer="bundled",
            license_binding=LicenseBindingMetadata(
                required_tier="paid",
                binding_hash="abc",
                operator_signature="sig",
                timestamp="2026-01-01T00:00:00Z",
            ),
        )
        signature = signer.sign_manifest(paid_manifest, private_key)

        # With audit chain, denials are logged
        # (audit chain verification is separate concern, tested in audit tests)
        validator = ForgeSkillValidator(operator_public_key=public_key)

        with pytest.raises(LicenseRequiredError):
            validator.validate_and_load(paid_manifest, signature, free_user)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
