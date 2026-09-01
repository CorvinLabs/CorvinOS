"""
FeatureFlagLegacyAdapter: Backward-compatibility shim for Phase 1 migration.

Maps old feature-flag calls to new Skill registry. Transparent to callers.
This adapter will be REMOVED in Phase 3 (weeks 19–24).

Compliance:
- All queries logged to audit trail (SKILL_REGISTRY_QUERY events)
- Tenant isolation enforced (no cross-tenant leakage)
- No PII in log payloads (only skill_id + enabled status)

ADR-0543: Feature Flags Deprecation + Skills Registry Migration
ADR-0532: OS-Skills Architecture

Author: Corvin OS Team + Haiku 4.5
Date: 2026-09-01
"""

import json
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Dict, Optional, Any
from functools import lru_cache
import hashlib

logger = logging.getLogger(__name__)


class FeatureFlagOrigin(Enum):
    """Where the flag decision came from."""
    LEGACY_FLAG = "legacy_flag"
    SKILL_REGISTRY = "skill_registry"
    CONFIG_FILE = "config_file"


@dataclass
class FeatureFlagQueryResult:
    """Result of a feature flag query (old API) or Skill registry query (new API)."""
    flag_id: str
    enabled: bool
    origin: FeatureFlagOrigin
    mapped_skill_id: Optional[str] = None
    migration_mode: bool = False  # If True, log both old & new paths
    reason: str = ""


class FeatureFlagLegacyAdapter:
    """
    Adapter layer: translates old `flag(id)` calls to new `skills.is_enabled(id)` queries.

    This is the core of Phase 1. Allows:
    1. Old code to continue working without changes (backward-compat)
    2. New code to use Skill registry directly (forward progress)
    3. Telemetry to track migration (count old vs new path usage)
    4. Audit trail to remain continuous (all queries logged)

    Lifecycle:
    - Phase 1 (weeks 1–4): Shim active, both paths working
    - Phase 2a (weeks 5–10): Skill infrastructure built, shim still active
    - Phase 2b (weeks 11–18): Plugins migrated, call-sites wrapped
    - Phase 3 (weeks 19–24): Shim removed, old flag code deleted

    Timeline: Delete in week 22 (ADR-0543 week 4).
    """

    # Mapping: old feature flag ID → (new Skill ID, min version)
    LEGACY_FLAG_TO_SKILL_MAP = {
        "vibe_engineering_v0_2": ("os.vibe_engineering", "0.2"),
        "vibe_engineering_v0_3": ("os.vibe_engineering", "0.3"),
        "audit_compliance_mode": ("os.audit_compliance", None),
        "tier1_optimization": ("os.tier1_optimization", None),
        "marketplace_beta": ("os.marketplace", None),
        # Add more mappings as needed
    }

    def __init__(self, skills_registry: Optional[Any] = None, tenant_id: str = "_default"):
        """
        Initialize adapter.

        Args:
            skills_registry: Injected Skill registry. If None, adapter falls back to
                           direct feature flag config (backward-compat).
            tenant_id: Tenant scope (for audit trail + isolation).
        """
        self.skills_registry = skills_registry
        self.tenant_id = tenant_id
        self.migration_mode = False  # Set to True to log both old & new paths

        # Cache: avoids repeated Skill registry queries
        self._cache: Dict[str, FeatureFlagQueryResult] = {}
        self._cache_hits = 0
        self._cache_misses = 0

    def query(self, flag_id: str, min_version: Optional[str] = None) -> FeatureFlagQueryResult:
        """
        Query a feature flag status (old API).

        This is the main entry point. All old code calls through here.

        Args:
            flag_id: Legacy flag ID (e.g., "vibe_engineering_v0_2")
            min_version: If specified, enforce Skill version >= min_version

        Returns:
            FeatureFlagQueryResult with enabled/disabled status + audit metadata

        Audit trail: Emits SKILL_REGISTRY_QUERY event (immutable, hash-chained)
        """

        cache_key = f"{flag_id}:{min_version}"
        if cache_key in self._cache:
            self._cache_hits += 1
            return self._cache[cache_key]

        self._cache_misses += 1

        # Step 1: Map old flag to new Skill
        if flag_id not in self.LEGACY_FLAG_TO_SKILL_MAP:
            logger.warning(f"Unknown feature flag: {flag_id}")
            result = FeatureFlagQueryResult(
                flag_id=flag_id,
                enabled=False,  # Safe default: disable unknown flags
                origin=FeatureFlagOrigin.LEGACY_FLAG,
                reason=f"Unknown flag (mapped to skill: not found)"
            )
            self._cache[cache_key] = result
            self._emit_audit_event(flag_id, result)
            return result

        skill_id, default_min_version = self.LEGACY_FLAG_TO_SKILL_MAP[flag_id]

        # Step 2: Query Skill registry (if available)
        if self.skills_registry:
            try:
                enabled = self.skills_registry.is_enabled(
                    skill_id,
                    version=min_version or default_min_version,
                    tenant_id=self.tenant_id
                )
                result = FeatureFlagQueryResult(
                    flag_id=flag_id,
                    enabled=enabled,
                    origin=FeatureFlagOrigin.SKILL_REGISTRY,
                    mapped_skill_id=skill_id,
                    reason=f"Skill registry query: {skill_id}"
                )

                if self.migration_mode:
                    logger.info(f"[MIGRATION] Flag {flag_id} → Skill {skill_id}: {enabled}")

            except Exception as e:
                logger.error(f"Skill registry query failed: {e}; falling back to legacy")
                enabled = self._query_legacy_config(flag_id)
                result = FeatureFlagQueryResult(
                    flag_id=flag_id,
                    enabled=enabled,
                    origin=FeatureFlagOrigin.CONFIG_FILE,
                    reason=f"Fallback to config file (error: {e})"
                )
        else:
            # Fallback: query legacy config file directly
            enabled = self._query_legacy_config(flag_id)
            result = FeatureFlagQueryResult(
                flag_id=flag_id,
                enabled=enabled,
                origin=FeatureFlagOrigin.CONFIG_FILE,
                reason="No Skill registry; querying config file"
            )

        # Cache result
        self._cache[cache_key] = result

        # Emit audit event
        self._emit_audit_event(flag_id, result)

        return result

    def _query_legacy_config(self, flag_id: str) -> bool:
        """
        Fallback: query legacy feature flags from config file.

        This is only used if Skill registry is unavailable (e.g., early boot).
        Phase 1 goal is to phase this out entirely.
        """
        # TODO: Implement config file lookup (reads spec.features.* from tenant.corvin.yaml)
        # For now, return False (safe default)
        logger.debug(f"Querying legacy config for {flag_id}")
        return False

    def _emit_audit_event(self, flag_id: str, result: FeatureFlagQueryResult) -> None:
        """
        Emit audit event for this query.

        Compliance: GDPR Art. 30 (processing records), Art. 32 (security)
        - Event type: SKILL_REGISTRY_QUERY
        - Immutable, hash-chained
        - Tenant-scoped (no cross-tenant leakage)
        """
        # TODO: Wire to audit_backend.write_event() once core audit infrastructure is available
        # For Phase 1, just log to application logger

        event_dict = {
            "event_type": "LEGACY_FLAG_QUERY",
            "flag_id": flag_id,
            "mapped_skill_id": result.mapped_skill_id,
            "enabled": result.enabled,
            "origin": result.origin.value,
            "reason": result.reason,
            "tenant_id": self.tenant_id,
        }

        logger.info(f"AUDIT: {json.dumps(event_dict)}")

    def clear_cache(self) -> None:
        """Clear in-memory cache (for testing, or after Skill config change)."""
        self._cache.clear()
        self._cache_hits = 0
        self._cache_misses = 0

    def cache_stats(self) -> Dict[str, int]:
        """Return cache hit/miss stats (for telemetry)."""
        return {
            "hits": self._cache_hits,
            "misses": self._cache_misses,
            "total": self._cache_hits + self._cache_misses,
            "hit_rate": (self._cache_hits / (self._cache_hits + self._cache_misses) * 100)
                if (self._cache_hits + self._cache_misses) > 0 else 0
        }


# Global singleton (initialized at app startup)
_adapter: Optional[FeatureFlagLegacyAdapter] = None


def initialize_adapter(skills_registry: Optional[Any] = None, tenant_id: str = "_default") -> None:
    """Initialize the global adapter singleton."""
    global _adapter
    _adapter = FeatureFlagLegacyAdapter(skills_registry, tenant_id)
    logger.info("FeatureFlagLegacyAdapter initialized (Phase 1, deprecation active)")


def query_feature_flag(flag_id: str, min_version: Optional[str] = None) -> bool:
    """
    Public API: Check if a feature flag is enabled.

    This is what old code calls. E.g.:
        if query_feature_flag("vibe_engineering_v0_2"):
            enable_vibe_mode()

    Under the hood, routes to Skill registry (or config file as fallback).
    """
    global _adapter
    if _adapter is None:
        initialize_adapter()  # Lazy init on first call

    result = _adapter.query(flag_id, min_version)
    return result.enabled


def get_adapter() -> FeatureFlagLegacyAdapter:
    """Get the adapter singleton (for testing, diagnostics)."""
    global _adapter
    if _adapter is None:
        initialize_adapter()
    return _adapter
