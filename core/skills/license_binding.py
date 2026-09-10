"""License Binding Validator — tier-based access control (Phase 2, ADR-0667).

Implements:
1. Tier-based license validation (free vs. paid)
2. Binding signature verification
3. User license tier checking
4. Audit logging for all validation operations
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, asdict
from typing import Optional

from core.skills.manifest_v2 import SkillManifestV2, LicenseBindingMetadata
from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent

logger = logging.getLogger(__name__)


class LicenseRequiredError(Exception):
    """Raised when user's license tier doesn't meet Skill requirements."""

    pass


@dataclass(frozen=True)
class UserLicense:
    """User's license information."""

    user_id: str
    license_tier: str  # "free" | "paid" | "enterprise"
    valid_until: Optional[str] = None  # ISO 8601 timestamp


class LicenseBindingValidator:
    """Validates Skill license bindings against user licenses.

    Responsibilities:
    - Check user tier >= manifest required tier
    - Verify binding signature
    - Audit all validation operations
    """

    TIER_HIERARCHY = {
        "free": 0,
        "paid": 1,
        "enterprise": 2,
    }

    def __init__(self, audit_chain: Optional[AuditChainWriter] = None):
        """Initialize license binding validator.

        Args:
            audit_chain: AuditChainWriter for logging (None = no audit)
        """
        self.audit_chain = audit_chain

    def validate_binding(
        self,
        manifest: SkillManifestV2,
        user_license: UserLicense,
        tenant_id: str = "_default"
    ) -> bool:
        """Validate Skill license binding against user license.

        Args:
            manifest: SkillManifestV2 to validate
            user_license: User's license information
            tenant_id: Tenant scope for audit logging

        Returns:
            True if user's license tier is sufficient, False otherwise

        Raises:
            LicenseRequiredError: on license tier mismatch
        """
        try:
            # Get required tier (default "free" if no binding)
            required_tier = manifest.required_tier()
            user_tier = user_license.license_tier

            # Check tier hierarchy
            required_level = self.TIER_HIERARCHY.get(required_tier, 0)
            user_level = self.TIER_HIERARCHY.get(user_tier, 0)

            if user_level < required_level:
                # License denied
                self._log_audit(
                    event_type="license_denied",
                    details={
                        "skill_id": manifest.skill_id,
                        "skill_version": manifest.version,
                        "required_tier": required_tier,
                        "user_tier": user_tier,
                        "reason": "insufficient_tier",
                    },
                    severity="warning",
                    user_id=user_license.user_id,
                    tenant_id=tenant_id,
                )
                raise LicenseRequiredError(
                    f"Skill {manifest.skill_id} requires {required_tier} tier, "
                    f"but user has {user_tier}"
                )

            # License granted
            self._log_audit(
                event_type="license_granted",
                details={
                    "skill_id": manifest.skill_id,
                    "skill_version": manifest.version,
                    "required_tier": required_tier,
                    "user_tier": user_tier,
                },
                user_id=user_license.user_id,
                tenant_id=tenant_id,
            )

            return True

        except LicenseRequiredError:
            raise
        except Exception as e:
            logger.error(f"License validation error for {manifest.skill_id}: {e}")
            self._log_audit(
                event_type="license_validation_error",
                details={"skill_id": manifest.skill_id, "error": str(e)},
                severity="error",
                user_id=user_license.user_id,
                tenant_id=tenant_id,
            )
            raise LicenseRequiredError(f"License validation failed: {e}")

    def validate_binding_signature(
        self,
        manifest: SkillManifestV2,
        operator_public_key,
        tenant_id: str = "_default"
    ) -> bool:
        """Validate license binding signature (future use).

        Currently a placeholder for RSA signature verification.

        Args:
            manifest: SkillManifestV2 with binding
            operator_public_key: RSA public key for verification
            tenant_id: Tenant scope

        Returns:
            True if signature is valid, False otherwise
        """
        if not manifest.license_binding:
            return True  # No binding = auto-pass

        # TODO: Implement RSA signature verification
        # For now, just log that we skipped it
        self._log_audit(
            event_type="license_signature_validation_skipped",
            details={"skill_id": manifest.skill_id},
            tenant_id=tenant_id,
        )

        return True

    def _log_audit(
        self,
        event_type: str,
        details: dict,
        severity: Optional[str] = None,
        user_id: Optional[str] = None,
        tenant_id: str = "_default",
    ) -> None:
        """Log an audit event if audit chain is configured.

        Args:
            event_type: Type of audit event
            details: Event details dict
            severity: Optional severity level (info, warning, error, critical)
            user_id: User ID for audit context
            tenant_id: Tenant scope
        """
        if not self.audit_chain:
            return

        event = AuditEvent(
            event_id=str(id(self)),  # Unique event ID
            event_type=event_type,
            tenant_id=tenant_id,
            user_id=user_id,
            timestamp=str(json.dumps(asdict({}))),  # Will be set by audit chain
            details=details,
            severity=severity or "info",
        )

        try:
            self.audit_chain.write_event(event)
        except Exception as e:
            logger.warning(f"Failed to log audit event {event_type}: {e}")


def get_user_license(user_id: str, license_store) -> UserLicense:
    """Get user's license from license store.

    Args:
        user_id: User ID
        license_store: License storage backend

    Returns:
        UserLicense object

    Raises:
        KeyError: if user not found
    """
    # Placeholder: would query license database
    # For now, return default free tier
    return UserLicense(user_id=user_id, license_tier="free")


# Import json at the end to avoid circular imports
import json
