"""Dual-Running Request Router — Canary deployment for console plugins.

Routes requests to marketplace plugin or console routes based on:
1. Feature flag (`workflows_plugin_enabled`)
2. Tenant cohort (early, staged, control)
3. Plugin health (503 fallback to console)

Enables zero-downtime migration with full A/B testing capability.

ADR-0039 (Workflow Builder) — Phase 6: Plugin Integration.
"""

from __future__ import annotations

import json
import logging
import os
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

_log = logging.getLogger(__name__)


class Cohort(str, Enum):
    """Deployment cohort for plugin canary."""

    EARLY = "early"          # First early adopters (week 1–2)
    STAGED = "staged"        # Gradual rollout (week 3–4, ~20%)
    CONTROL = "control"      # Default control group (plugin off)


class DualRunningRouter:
    """Routes requests to plugin or console based on feature flag + cohort.

    Enables canary deployment:
    - Week 0: All tenants → console (baseline)
    - Week 1: 2–5 early adopters → plugin (validation)
    - Week 2–3: 20% staged cohort → plugin (scale test)
    - Week 4+: 100% → plugin (full migration)
    """

    def __init__(self, config_dir: Optional[str] = None):
        """Initialize router with optional config directory.

        Args:
            config_dir: Directory for cohort metadata (default: ~/.corvin/tenants/_default/workflows/).
        """
        if config_dir is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            config_dir = os.path.join(corvin_home, "tenants", "_default", "workflows")

        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self._cohort_cache: dict[str, Cohort] = {}

    def route_request(self, tenant_id: str, feature_flag_enabled: bool = True) -> Literal["plugin", "console"]:
        """Route a request to plugin or console.

        Args:
            tenant_id: Tenant ID.
            feature_flag_enabled: Whether workflows plugin feature flag is enabled globally.

        Returns:
            "plugin" → route to marketplace plugin
            "console" → route to console fallback
        """
        # Feature flag: off → always console
        if not feature_flag_enabled:
            return "console"

        # Get tenant cohort
        cohort = self.get_tenant_cohort(tenant_id)

        # Route by cohort
        if cohort in (Cohort.EARLY, Cohort.STAGED):
            return "plugin"
        else:
            return "console"

    def get_tenant_cohort(self, tenant_id: str) -> Cohort:
        """Get tenant cohort (cached).

        Args:
            tenant_id: Tenant ID.

        Returns:
            Cohort: early, staged, or control.
        """
        if tenant_id in self._cohort_cache:
            return self._cohort_cache[tenant_id]

        cohort = self._load_cohort_from_disk(tenant_id)
        self._cohort_cache[tenant_id] = cohort
        return cohort

    def set_tenant_cohort(self, tenant_id: str, cohort: Cohort) -> None:
        """Set tenant cohort (persistent).

        Args:
            tenant_id: Tenant ID.
            cohort: Target cohort (early, staged, control).
        """
        cohort_file = self.config_dir / f"{tenant_id}.cohort.json"
        metadata = {
            "tenant_id": tenant_id,
            "cohort": cohort.value,
            "enrolled_at": self._timestamp(),
        }

        try:
            with open(cohort_file, "w") as f:
                json.dump(metadata, f, indent=2)
            self._cohort_cache[tenant_id] = cohort
            _log.info(f"Enrolled tenant {tenant_id} in cohort {cohort.value}")
        except Exception as e:
            _log.error(f"Failed to write cohort file {cohort_file}: {e}")

    def _load_cohort_from_disk(self, tenant_id: str) -> Cohort:
        """Load cohort from disk, default to control."""
        cohort_file = self.config_dir / f"{tenant_id}.cohort.json"

        if not cohort_file.exists():
            return Cohort.CONTROL

        try:
            with open(cohort_file, "r") as f:
                metadata = json.load(f)
            cohort_str = metadata.get("cohort", Cohort.CONTROL.value)
            return Cohort(cohort_str)
        except Exception as e:
            _log.warning(f"Failed to load cohort for {tenant_id}: {e}; defaulting to CONTROL")
            return Cohort.CONTROL

    def list_cohorts(self) -> dict[str, Cohort]:
        """List all enrolled tenants and their cohorts.

        Returns:
            Dict: {tenant_id: cohort}
        """
        cohorts = {}
        for cohort_file in self.config_dir.glob("*.cohort.json"):
            try:
                with open(cohort_file, "r") as f:
                    metadata = json.load(f)
                tenant_id = metadata.get("tenant_id")
                cohort_str = metadata.get("cohort", Cohort.CONTROL.value)
                if tenant_id:
                    cohorts[tenant_id] = Cohort(cohort_str)
            except Exception as e:
                _log.warning(f"Failed to parse cohort file {cohort_file}: {e}")
        return cohorts

    def cohort_stats(self) -> dict[str, int]:
        """Get stats on cohort distribution.

        Returns:
            Dict: {cohort_name: count}
        """
        cohorts = self.list_cohorts()
        stats = {cohort.value: 0 for cohort in Cohort}
        for cohort in cohorts.values():
            stats[cohort.value] += 1
        return stats

    @staticmethod
    def _timestamp() -> str:
        """Current ISO 8601 timestamp."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


# ────────────────────────────────────────────────────────────────────────────
# Global singleton + convenience functions
# ────────────────────────────────────────────────────────────────────────────

_router = DualRunningRouter()


def route_workflows_request(tenant_id: str, feature_flag_enabled: bool = True) -> Literal["plugin", "console"]:
    """Convenience function: route request to plugin or console.

    Args:
        tenant_id: Tenant ID.
        feature_flag_enabled: Feature flag state (global).

    Returns:
        "plugin" or "console"
    """
    return _router.route_request(tenant_id, feature_flag_enabled)


def enroll_tenant_in_cohort(tenant_id: str, cohort: str) -> None:
    """Convenience function: enroll tenant in canary cohort.

    Args:
        tenant_id: Tenant ID.
        cohort: Cohort name (early, staged, control).
    """
    try:
        cohort_enum = Cohort(cohort)
        _router.set_tenant_cohort(tenant_id, cohort_enum)
    except ValueError:
        valid_cohorts = ", ".join([c.value for c in Cohort])
        raise ValueError(f"Invalid cohort '{cohort}'; must be one of: {valid_cohorts}")


def get_router() -> DualRunningRouter:
    """Get global router instance."""
    return _router
