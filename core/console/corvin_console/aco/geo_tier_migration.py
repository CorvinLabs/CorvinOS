"""Auto-migration: geo_tracking_tier default-on (ADR-0208).

When updating from v0.10.56 to v0.10.58+, if a user has the old default
(geo_tracking_tier: 1) but did NOT explicitly set it, this migration
upgrades them to Tier 3 automatically. This enables real instance tracking
without user friction.

Heuristic: If the config file exists AND geo_tracking_tier == 1 AND there's
no geo_tracking_consent_given flag (sign of old auto-generated config),
then upgrade to Tier 3.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


def _tenant_cfg_path(home: Path) -> Path:
    """Resolve tenant.corvin.yaml path — same as htrace_consent.py."""
    from . import htrace_consent as hc
    return hc._tenant_cfg_path(home)


def should_migrate_geo_tier(home: Path) -> bool:
    """Return True if config needs geo_tier migration.

    Conditions:
    1. Config file exists
    2. geo_tracking_tier is explicitly 1 (old default)
    3. geo_tracking_consent_given is NOT present (sign of old auto-config)
    4. We haven't already migrated (can re-run safely)
    """
    cfg_path = _tenant_cfg_path(home)
    if not cfg_path.exists():
        return False  # No config = already on new default

    try:
        text = cfg_path.read_text(encoding="utf-8")
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        return False  # Unreadable = don't migrate

    if not isinstance(data, dict):
        return False
    spec = data.get("spec", data)
    if not isinstance(spec, dict):
        return False
    tele = spec.get("telemetry", {})
    if not isinstance(tele, dict):
        return False

    tier = tele.get("geo_tracking_tier")
    has_consent_flag = "geo_tracking_consent_given" in tele

    # Migrate if: tier is 1, but consent flag absent (auto-generated, not user-set)
    return tier == 1 and not has_consent_flag


def migrate_geo_tier_to_default(home: Path) -> bool:
    """Upgrade geo_tracking_tier from 1 → 3 automatically.

    Returns True if migration was performed, False otherwise.
    """
    if not should_migrate_geo_tier(home):
        return False

    cfg_path = _tenant_cfg_path(home)
    try:
        text = cfg_path.read_text(encoding="utf-8")
        import yaml  # noqa: PLC0415
        data = yaml.safe_load(text)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"geo_tier migration: failed to read config: {e}")
        return False

    if not isinstance(data, dict):
        data = {"spec": {}}
    spec = data.get("spec")
    if not isinstance(spec, dict):
        spec = {}
        data["spec"] = spec
    tele = spec.get("telemetry", {})
    if not isinstance(tele, dict):
        tele = {}
        spec["telemetry"] = tele

    old_tier = tele.get("geo_tracking_tier", 1)
    tele["geo_tracking_tier"] = 3

    try:
        # Use safe_dump to preserve formatting
        new_text = yaml.dump(data, default_flow_style=False, sort_keys=False)
        cfg_path.write_text(new_text, encoding="utf-8")
        logger.info(
            f"geo_tier migration: upgraded {old_tier} → 3 in {cfg_path}"
        )
        return True
    except Exception as e:  # noqa: BLE001
        logger.error(f"geo_tier migration: failed to write config: {e}")
        return False
