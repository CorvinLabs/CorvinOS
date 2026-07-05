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
