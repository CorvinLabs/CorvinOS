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


def test_migration_retired_explicit_tier1_is_respected(tmp_path):
    """RETIRED migration (v0.10.59, adversarial review 2026-07-22).

    ``geo_tracking_tier: 1`` without a consent flag is indistinguishable from
    the documented ADR-0208 opt-out (hand-writing exactly that line), so the
    old "tier 1 + no consent flag ⇒ old auto-config, upgrade to 3" heuristic
    silently overrode explicit user opt-outs — a violation of the
    load-bearing opt-out invariant (GDPR Art. 21). The migration already ran
    fleet-wide via v0.10.58's 5-minute heartbeat, so retiring it loses
    nothing: absent keys still default to tier 3.
    """
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )
    assert gtm.should_migrate_geo_tier(home) is False
    assert gtm.migrate_geo_tier_to_default(home) is False

    # The explicit opt-out sticks — on disk and effectively.
    assert hc.geo_tracking_tier(home) == 1


def test_migration_never_rewrites_config_file(tmp_path):
    """The config file is left byte-identical — no silent rewrite."""
    home = _make_home(tmp_path)
    cfg_path = hc._tenant_cfg_path(home)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )

    old_text = cfg_path.read_text()
    gtm.migrate_geo_tier_to_default(home)
    assert cfg_path.read_text() == old_text


def test_effective_geo_tier_respects_explicit_opt_out(tmp_path):
    """effective_geo_tier() must honor an explicit tier-1 opt-out (no
    migration may fire behind the user's back)."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )

    assert hc.geo_tracking_tier(home) == 1
    assert hc.effective_geo_tier(home) == 1
    # Still 1 on disk afterwards.
    assert hc.geo_tracking_tier(home) == 1


def test_absent_key_still_defaults_to_tier3(tmp_path):
    """Retiring the migration must NOT change the default-ON posture:
    a config without the key stays tier 3 (ADR-0208)."""
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry: {}\n")
    assert hc.geo_tracking_tier(home) == 3
    assert hc.effective_geo_tier(home) == 3
