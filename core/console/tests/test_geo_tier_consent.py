"""Tier 1/2/3 geo-tracking default-ON — ADR-0205 / ADR-0208.

As of ADR-0208, geo_tracking_tier is default-ON at Tier 3 (city + 10km grid).
Users opt-out by setting geo_tracking_tier: 1 or CORVIN_GEO_OPT_OUT=1 env var.
The old geo_tracking_consent_given flag is no longer a gate (backward-compat only).
"""
from __future__ import annotations

from pathlib import Path

from corvin_console.aco import htrace_consent as hc


def _make_home(tmp_path: Path) -> Path:
    home = tmp_path / ".corvin"
    (home / "aco" / "telemetry").mkdir(parents=True, exist_ok=True)
    return home


def _write_cfg(home: Path, yaml_text: str) -> None:
    cfg = hc._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text(yaml_text, encoding="utf-8")


def test_no_config_file_defaults_to_tier3(tmp_path):
    """Default is now Tier 3 (ADR-0208) — all instances send geo data by default."""
    home = _make_home(tmp_path)
    assert hc.geo_tracking_tier(home) == 3
    assert hc.geo_tracking_consent_given(home) is False  # Consent is now irrelevant
    assert hc.effective_geo_tier(home) == 3


def test_tier_configured_no_consent_gate_anymore(tmp_path):
    """Consent gate is removed (ADR-0208). Tier 2 without consent_given is now effective."""
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry:\n    geo_tracking_tier: 2\n")
    assert hc.geo_tracking_tier(home) == 2
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 2  # ← Changed: 1 → 2


def test_explicit_opt_out_to_tier1(tmp_path):
    """User can opt-out by setting geo_tracking_tier: 1."""
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry:\n    geo_tracking_tier: 1\n")
    assert hc.geo_tracking_tier(home) == 1
    assert hc.effective_geo_tier(home) == 1


def test_tier2_is_effective(tmp_path):
    """User can set tier 2 explicitly; no consent required."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 2\n",
    )
    assert hc.effective_geo_tier(home) == 2


def test_tier3_is_effective(tmp_path):
    """User can set tier 3 explicitly; no consent required."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 3\n",
    )
    assert hc.effective_geo_tier(home) == 3


def test_out_of_range_tier_falls_back_to_3(tmp_path):
    """Invalid tier values fall back to default Tier 3."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 99\n",
    )
    assert hc.geo_tracking_tier(home) == 3
    assert hc.effective_geo_tier(home) == 3


def test_geo_tracking_consent_given_still_parsed_but_not_gated(tmp_path):
    """The consent flag is parsed for backward-compat but no longer used as a gate."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 2\n    geo_tracking_consent_given: \"yes\"\n",
    )
    assert hc.geo_tracking_consent_given(home) is False  # String "yes" is not literal bool
    assert hc.effective_geo_tier(home) == 2  # ← But tier 2 is still effective


def test_broken_yaml_fails_closed_to_tier3(tmp_path):
    """Broken YAML falls back to Tier 3 (default-ON, fail-open is safe here)."""
    home = _make_home(tmp_path)
    cfg = hc._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("not: valid: yaml: [[[", encoding="utf-8")
    assert hc.geo_tracking_tier(home) == 3
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 3


def test_heartbeat_sends_geo_header_at_default_tier3(tmp_path, monkeypatch):
    """send_heartbeat() should attach X-HTrace-Geo-Tier: 3 by default (ADR-0208)."""
    from corvin_console.aco import heartbeat as hb

    home = _make_home(tmp_path)
    captured = {}

    class _FakeResp:
        def getcode(self):
            return 200

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    def _fake_request(url, data=None, method=None, headers=None):
        captured["headers"] = headers
        return object()

    monkeypatch.setattr(hb, "_load_telemetry_token", lambda h: "tok")
    monkeypatch.setattr(hb, "_load_instance_token", lambda h: "itok")
    monkeypatch.setattr(hb, "load_or_create_instance_id", lambda h: "iid")
    monkeypatch.setattr(hb.urllib.request, "Request", _fake_request)
    monkeypatch.setattr(hb, "_open_no_redirect", lambda req, timeout: _FakeCtx())

    assert hb.send_heartbeat(home) is True
    assert captured["headers"]["X-HTrace-Geo-Tier"] == "3"  # ← Changed: omitted → "3"


def test_heartbeat_respects_explicit_tier_downgrade(tmp_path, monkeypatch):
    """User can opt-out by setting tier 1; heartbeat should omit geo header."""
    from corvin_console.aco import heartbeat as hb

    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 1\n",
    )
    captured = {}

    class _FakeResp:
        def getcode(self):
            return 200

    class _FakeCtx:
        def __enter__(self):
            return _FakeResp()

        def __exit__(self, *a):
            return False

    def _fake_request(url, data=None, method=None, headers=None):
        captured["headers"] = headers
        return object()

    monkeypatch.setattr(hb, "_load_telemetry_token", lambda h: "tok")
    monkeypatch.setattr(hb, "_load_instance_token", lambda h: "itok")
    monkeypatch.setattr(hb, "load_or_create_instance_id", lambda h: "iid")
    monkeypatch.setattr(hb.urllib.request, "Request", _fake_request)
    monkeypatch.setattr(hb, "_open_no_redirect", lambda req, timeout: _FakeCtx())

    assert hb.send_heartbeat(home) is True
    assert "X-HTrace-Geo-Tier" not in captured["headers"]  # Tier 1 = no header
