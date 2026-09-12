"""Unit tests for routes/healing_config — the console Settings telemetry toggles.

Covers the three telemetry opt-out flags (all default-ON) plus the ACO flags,
and the merge-write into tenant.corvin.yaml (spec wrapper preserved).
"""
from __future__ import annotations

import yaml

import corvin_console.routes.healing_config as hc


def _patch_path(monkeypatch, tmp_path):
    p = tmp_path / "tenant.corvin.yaml"
    monkeypatch.setattr(hc, "_config_path", lambda tid: p)
    return p


def test_defaults_all_telemetry_on(monkeypatch, tmp_path):
    _patch_path(monkeypatch, tmp_path)
    flags = hc._read_flags("_default")
    assert flags["ping_enabled"] is True
    assert flags["error_enabled"] is True
    assert flags["telemetry_enabled"] is True     # healing traces
    assert flags["healing_enabled"] is True
    assert flags["risky_enabled"] is False


def test_write_opt_out_ping_and_error(monkeypatch, tmp_path):
    p = _patch_path(monkeypatch, tmp_path)
    hc._write_flags("_default", {"ping_enabled": False, "error_enabled": False})
    flags = hc._read_flags("_default")
    assert flags["ping_enabled"] is False
    assert flags["error_enabled"] is False
    assert flags["telemetry_enabled"] is True     # untouched → still on
    # Written under spec.telemetry with the runtime-read key names.
    doc = yaml.safe_load(p.read_text())
    tel = doc.get("spec", doc)["telemetry"]
    assert tel["ping_enabled"] is False
    assert tel["error_traces"] is False


def test_write_preserves_other_keys_and_spec(monkeypatch, tmp_path):
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(yaml.safe_dump({
        "apiVersion": "corvin/v1", "kind": "Tenant",
        "spec": {"telemetry": {"healing_traces": True}, "engine": {"id": "hermes"}},
    }))
    hc._write_flags("_default", {"ping_enabled": False})
    doc = yaml.safe_load(p.read_text())
    assert doc["apiVersion"] == "corvin/v1"            # header preserved
    assert doc["spec"]["engine"]["id"] == "hermes"     # unrelated key preserved
    assert doc["spec"]["telemetry"]["ping_enabled"] is False
    assert doc["spec"]["telemetry"]["healing_traces"] is True


def test_false_like_string_reads_as_off(monkeypatch, tmp_path):
    p = _patch_path(monkeypatch, tmp_path)
    p.write_text(yaml.safe_dump({"spec": {"telemetry": {"ping_enabled": "off"}}}))
    assert hc._read_flags("_default")["ping_enabled"] is False


def test_get_active_tier_returns_free_on_error(monkeypatch, tmp_path):
    """When license module is unavailable, _get_active_tier() returns 'free' (fail-close)."""
    # Mock the license import to raise an exception
    def mock_import(*args, **kwargs):
        raise ImportError("license module not found")

    monkeypatch.setattr("builtins.__import__", mock_import, raising=False)
    tier = hc._get_active_tier()
    assert tier == "free"


def test_patch_telemetry_rejected_for_free_tier(monkeypatch, tmp_path):
    """Free tier users cannot change telemetry settings."""
    from fastapi import HTTPException

    _patch_path(monkeypatch, tmp_path)
    # Mock _get_active_tier to return "free"
    monkeypatch.setattr(hc, "_get_active_tier", lambda: "free")

    # Create a fake SessionRecord
    from corvin_console.auth import SessionRecord
    rec = SessionRecord(
        sid="a" * 43,
        sid_fingerprint="test",
        tier="owner",
        tenant_id="_default",
        token_fingerprint="",
        csrf_secret="test_secret",
        created_at=0.0,
        last_seen_at=0.0,
        expires_at=9999999999.0,
    )

    req = hc.HealingConfigRequest(ping_enabled=False)

    # Should raise HTTPException with 403 status
    try:
        hc.patch_healing_config(req, rec)
        assert False, "Expected HTTPException"
    except HTTPException as e:
        assert e.status_code == 403
        assert "telemetry_locked" in e.detail


def test_patch_telemetry_allowed_for_member_tier(monkeypatch, tmp_path):
    """Member tier users can change telemetry settings."""
    p = _patch_path(monkeypatch, tmp_path)
    # Mock _get_active_tier to return "member"
    monkeypatch.setattr(hc, "_get_active_tier", lambda: "member")

    # Create a fake SessionRecord
    from corvin_console.auth import SessionRecord
    rec = SessionRecord(
        sid="a" * 43,
        sid_fingerprint="test",
        tier="owner",
        tenant_id="_default",
        token_fingerprint="",
        csrf_secret="test_secret",
        created_at=0.0,
        last_seen_at=0.0,
        expires_at=9999999999.0,
    )

    req = hc.HealingConfigRequest(ping_enabled=False)

    # Should succeed (not raise)
    result = hc.patch_healing_config(req, rec)
    assert result["ping_enabled"] is False

    # Verify the file was written
    doc = yaml.safe_load(p.read_text())
    assert doc["spec"]["telemetry"]["ping_enabled"] is False


def test_patch_non_telemetry_allowed_for_free_tier(monkeypatch, tmp_path):
    """Free tier users can change non-telemetry settings (healing, risky)."""
    p = _patch_path(monkeypatch, tmp_path)
    # Mock _get_active_tier to return "free"
    monkeypatch.setattr(hc, "_get_active_tier", lambda: "free")

    # Create a fake SessionRecord
    from corvin_console.auth import SessionRecord
    rec = SessionRecord(
        sid="a" * 43,
        sid_fingerprint="test",
        tier="owner",
        tenant_id="_default",
        token_fingerprint="",
        csrf_secret="test_secret",
        created_at=0.0,
        last_seen_at=0.0,
        expires_at=9999999999.0,
    )

    # Non-telemetry settings should work
    req = hc.HealingConfigRequest(healing_enabled=False)
    result = hc.patch_healing_config(req, rec)
    assert result["healing_enabled"] is False

    # Verify the file was written
    doc = yaml.safe_load(p.read_text())
    assert doc["spec"]["aco"]["l5_enabled"] is False
