"""Marketplace Origin Validator (Phase 3, ADR-0667).

Verifies that Skills come from authorized sources (operator, verified vendors).
"""

from __future__ import annotations

import logging
from typing import Optional

from core.skills.license_binding import UserLicense

logger = logging.getLogger(__name__)


class MarketplaceOriginValidator:
    """Validates Skill origin and publication permissions."""

    # Operator-controlled Skills (always authorized)
    OPERATOR_VERIFIED_SKILLS = {
        "os.delegation_router",
        "os.context_adapter",
        "os.workflow_optimizer",
        "os.security_orchestrator",
        "os.flow_guard",
        "routing.basic",
        "routing.optimized",
        "routing.adaptive",
        "routing.cost_optimizer",
    }

    # Free-Tier users cannot publish Skills
    # Paid-Tier users can publish to marketplace (but not as operator)

    def is_operator_verified(self, skill_id: str) -> bool:
        """Check if Skill is operator-verified.

        Args:
            skill_id: Skill identifier

        Returns:
            True if operator-verified
        """
        return skill_id in self.OPERATOR_VERIFIED_SKILLS

    def is_user_publishable(self, skill_id: str, user: UserLicense) -> bool:
        """Check if user can publish this Skill.

        Args:
            skill_id: Skill identifier
            user: User's license information

        Returns:
            True if user can publish
        """
        # Free-Tier cannot publish
        if user.license_tier == "free":
            return False

        # Paid-Tier and Enterprise can publish (but not override operator Skills)
        if self.is_operator_verified(skill_id):
            return False  # Can't override operator Skills

        return True

    def check_skill_origin(self, skill_id: str) -> str:
        """Determine Skill's origin.

        Args:
            skill_id: Skill identifier

        Returns:
            "operator" | "verified_vendor" | "unverified"
        """
        if self.is_operator_verified(skill_id):
            return "operator"

        # TODO: Check marketplace vendor verification
        # For now, default to unverified
        return "unverified"

    def is_origin_authorized(
        self,
        skill_id: str,
        origin: str,
        user: UserLicense
    ) -> bool:
        """Check if Skill origin is authorized for this user.

        Args:
            skill_id: Skill identifier
            origin: Skill origin ("operator" | "verified_vendor" | "unverified")
            user: User's license information

        Returns:
            True if authorized to load
        """
        # Operator Skills: always authorized
        if origin == "operator":
            return True

        # Verified Vendor: authorized for Paid-Tier and above
        if origin == "verified_vendor":
            return user.license_tier in ("paid", "enterprise")

        # Unverified: only authorized for Enterprise-Tier
        if origin == "unverified":
            return user.license_tier == "enterprise"

        return False

    def restrict_operator_overrides(self, skill_id: str) -> bool:
        """Prevent non-operator Skills from overriding operator Skills.

        Args:
            skill_id: Skill identifier

        Returns:
            True if override is prevented
        """
        return self.is_operator_verified(skill_id)
