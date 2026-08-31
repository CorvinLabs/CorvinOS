"""
Feature Flags for Audit Durability — ADR-0299

Configuration for audit durability features (WAL, crash recovery, metrics).

CRITICAL: `audit_durability_enabled` is LOAD-BEARING for GDPR Art. 30/32 compliance.
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class AuditDurabilityFlags:
    """Audit durability feature flags."""

    # Core durability mechanism (CRITICAL, default OFF)
    audit_durability_enabled: bool = False

    # Write-Ahead Logging (default OFF, but recommended)
    enable_wal: bool = False

    # Crash recovery on boot (default OFF, but recommended)
    enable_crash_recovery: bool = False

    # Durability metrics tracking (default OFF)
    enable_durability_metrics: bool = False

    # Corrupt ion detection and auto-repair (default OFF)
    enable_corruption_detection: bool = False

    # Audit-of-audit logging (default OFF, advanced)
    enable_audit_of_audit: bool = False

    def validate(self) -> None:
        """Validate flag configuration.

        Raises:
            ValueError: If configuration is invalid
        """
        # audit_durability_enabled must be considered for GDPR compliance
        if not self.audit_durability_enabled:
            # This is a warning, not an error, but operators should be aware
            import logging

            logger = logging.getLogger(__name__)
            logger.warning(
                "CRITICAL: audit_durability_enabled is OFF. "
                "GDPR Art. 30/32 compliance requires this feature. "
                "Set to True in feature flags before production use."
            )

    @classmethod
    def from_config(cls, config: Optional[dict]) -> "AuditDurabilityFlags":
        """Create flags from configuration dict.

        Args:
            config: Dict of feature flags

        Returns:
            AuditDurabilityFlags instance
        """
        if not config:
            return cls()

        flags = cls(
            audit_durability_enabled=config.get(
                "audit_durability_enabled", False
            ),
            enable_wal=config.get("enable_wal", False),
            enable_crash_recovery=config.get("enable_crash_recovery", False),
            enable_durability_metrics=config.get(
                "enable_durability_metrics", False
            ),
            enable_corruption_detection=config.get(
                "enable_corruption_detection", False
            ),
            enable_audit_of_audit=config.get("enable_audit_of_audit", False),
        )

        flags.validate()
        return flags

    def to_dict(self) -> dict:
        """Convert to configuration dict.

        Returns:
            Dict of feature flags
        """
        return {
            "audit_durability_enabled": self.audit_durability_enabled,
            "enable_wal": self.enable_wal,
            "enable_crash_recovery": self.enable_crash_recovery,
            "enable_durability_metrics": self.enable_durability_metrics,
            "enable_corruption_detection": self.enable_corruption_detection,
            "enable_audit_of_audit": self.enable_audit_of_audit,
        }


# Default production flags (all enabled)
PRODUCTION_FLAGS = AuditDurabilityFlags(
    audit_durability_enabled=True,
    enable_wal=True,
    enable_crash_recovery=True,
    enable_durability_metrics=True,
    enable_corruption_detection=True,
    enable_audit_of_audit=False,  # Advanced, off by default
)

# Default development flags (basic only)
DEVELOPMENT_FLAGS = AuditDurabilityFlags(
    audit_durability_enabled=False,
    enable_wal=False,
    enable_crash_recovery=False,
    enable_durability_metrics=False,
    enable_corruption_detection=False,
    enable_audit_of_audit=False,
)

# Safe mode flags (all features, no auto-repair)
SAFE_MODE_FLAGS = AuditDurabilityFlags(
    audit_durability_enabled=True,
    enable_wal=True,
    enable_crash_recovery=True,
    enable_durability_metrics=True,
    enable_corruption_detection=True,  # Detection only, manual repair
    enable_audit_of_audit=True,
)
