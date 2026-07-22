"""Test geo_tier_migration — auto-upgrade on v0.10.57 → v0.10.58+ (ADR-0208)."""
from __future__ import annotations

from pathlib import Path

from corvin_console.aco import geo_tier_migration as gtm
from corvin_console.aco import htrace_consent as hc


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)
    return home


def _write_cfg(home: Path, yaml_text: str) -> None:
    cfg = hc._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml_text, encoding="utf-8")


def test_migration_skipped_if_no_config(tmp_path):
    """No config file = already on new default, skip migration."""
    home = _make_home(tmp_path)
    assert gtm.should_migrate_geo_tier(home) is False
    assert gtm.migrate_geo_tier_to_default(home) is False


def test_migration_skipped_if_tier_already_3(tmp_path):
    """Config with tier 3 = no migration needed."""
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry:\n    geo_tracking_tier: 3\n")
    assert gtm.should_migrate_geo_tier(home) is False


def test_migration_skipped_if_tier_1_but_has_consent_flag(tmp_path):
    """Tier 1 + consent flag = user explicitly configured, don't migrate."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n    geo_tracking_consent_given: false\n",
    )
    assert gtm.should_migrate_geo_tier(home) is False


def test_migration_triggers_on_old_default(tmp_path):
    """Tier 1 + no consent flag = old auto-config, migrate to 3."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )
    assert gtm.should_migrate_geo_tier(home) is True
    assert gtm.migrate_geo_tier_to_default(home) is True

    # Verify result
    assert hc.geo_tracking_tier(home) == 3


def test_migration_updates_config_file(tmp_path):
    """Config file is actually updated with new tier."""
    home = _make_home(tmp_path)
    cfg_path = hc._tenant_cfg_path(home)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )

    old_text = cfg_path.read_text()
    assert "geo_tracking_tier: 1" in old_text

    gtm.migrate_geo_tier_to_default(home)

    new_text = cfg_path.read_text()
    assert "geo_tracking_tier: 3" in new_text
    assert "geo_tracking_tier: 1" not in new_text


def test_migration_idempotent(tmp_path):
    """Running migration twice should be safe."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )

    # First migration
    assert gtm.migrate_geo_tier_to_default(home) is True
    assert hc.geo_tracking_tier(home) == 3

    # Second migration (should_migrate should be False now)
    assert gtm.should_migrate_geo_tier(home) is False
    assert gtm.migrate_geo_tier_to_default(home) is False

    # Result unchanged
    assert hc.geo_tracking_tier(home) == 3


def test_effective_geo_tier_triggers_migration(tmp_path):
    """effective_geo_tier() auto-runs migration on first call."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )

    # Before: tier 1 (old default)
    assert hc.geo_tracking_tier(home) == 1

    # Call effective_geo_tier — should migrate
    tier = hc.effective_geo_tier(home)

    # After: tier 3 (new default)
    assert tier == 3
    assert hc.geo_tracking_tier(home) == 3
