"""Tier 2/3 geo-tracking consent gate — ADR-0205/0206.

Unlike ping_enabled()/healing_traces (default-ON, opt-out), geo_tracking_tier
>= 2 requires BOTH a configured tier AND explicit geo_tracking_consent_given.
Every fail path (absent config, unreadable, broken YAML, missing keys) must
resolve to the privacy-preserving state: tier 1, no consent.
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


def test_no_config_file_defaults_to_tier1_no_consent(tmp_path):
    home = _make_home(tmp_path)
    assert hc.geo_tracking_tier(home) == 1
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 1


def test_tier_configured_without_consent_stays_tier1(tmp_path):
    """The most important guard: configuring tier=2 alone (e.g. copy-pasted
    from docs) must NOT be enough — consent is a separate, required flag."""
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry:\n    geo_tracking_tier: 2\n")
    assert hc.geo_tracking_tier(home) == 2
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 1


def test_consent_without_tier_configured_stays_tier1(tmp_path):
    home = _make_home(tmp_path)
    _write_cfg(home, "spec:\n  telemetry:\n    geo_tracking_consent_given: true\n")
    assert hc.geo_tracking_tier(home) == 1
    assert hc.geo_tracking_consent_given(home) is True
    assert hc.effective_geo_tier(home) == 1


def test_tier2_with_consent_is_effective(tmp_path):
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 2\n    geo_tracking_consent_given: true\n",
    )
    assert hc.effective_geo_tier(home) == 2


def test_tier3_with_consent_is_effective(tmp_path):
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 3\n    geo_tracking_consent_given: true\n",
    )
    assert hc.effective_geo_tier(home) == 3


def test_out_of_range_tier_falls_back_to_1(tmp_path):
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 99\n    geo_tracking_consent_given: true\n",
    )
    assert hc.geo_tracking_tier(home) == 1
    assert hc.effective_geo_tier(home) == 1


def test_consent_must_be_exact_boolean_true(tmp_path):
    """A truthy-looking string ('yes', '1') must NOT count as consent — only
    the literal YAML boolean true. This mirrors the strict Art. 6(1)(a)
    posture: ambiguous config never grants consent."""
    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 2\n    geo_tracking_consent_given: \"yes\"\n",
    )
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 1


def test_broken_yaml_fails_closed_to_tier1_no_consent(tmp_path):
    home = _make_home(tmp_path)
    cfg = hc._tenant_cfg_path(home)
    cfg.parent.mkdir(parents=True, exist_ok=True)
    cfg.write_text("not: valid: yaml: [[[", encoding="utf-8")
    assert hc.geo_tracking_tier(home) == 1
    assert hc.geo_tracking_consent_given(home) is False
    assert hc.effective_geo_tier(home) == 1


def test_heartbeat_omits_geo_header_at_tier1(tmp_path, monkeypatch):
    """send_heartbeat() must not attach X-HTrace-Geo-Tier at all when the
    effective tier is 1 — the server should never see the header for the
    overwhelming majority of (non-consenting) instances."""
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
    assert "X-HTrace-Geo-Tier" not in captured["headers"]


def test_heartbeat_sends_geo_header_when_consented(tmp_path, monkeypatch):
    from corvin_console.aco import heartbeat as hb

    home = _make_home(tmp_path)
    _write_cfg(
        home,
        "spec:\n  telemetry:\n    geo_tracking_tier: 3\n    geo_tracking_consent_given: true\n",
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
    assert captured["headers"]["X-HTrace-Geo-Tier"] == "3"
