"""ADR-0167 — ELR descriptor issuer + shared ratchet builder (production activation).

Proves the issuer and the live-consumer ratchet builders agree (a stored
descriptor unwraps), that the consumer helper is fail-open-to-static, and that
the tenant-config writer round-trips without clobbering other keys.
"""
from __future__ import annotations

import base64
import sys
import tempfile
from pathlib import Path

import pytest

_LIC = Path(__file__).resolve().parents[1]
if str(_LIC) not in sys.path:
    sys.path.insert(0, str(_LIC))

import elr_issuer as ISS  # type: ignore
from elr import CapabilityEnvelope, CapabilityRegistry  # type: ignore
from elr_capabilities_m2 import EgressPaidPresetCapability  # type: ignore

_TOKEN = b"z" * 80
_INST = "instance-xyz"


def _policy() -> dict:
    return EgressPaidPresetCapability(
        allowed_hosts=["ollama.lan"], forbidden_hosts=["api.anthropic.com"],
        default_action="deny", expires_at_epoch_k=0,
    ).to_dict()


def test_stable_anchor_deterministic_and_per_instance():
    assert ISS.stable_anchor(_INST) == ISS.stable_anchor(_INST)
    assert ISS.stable_anchor("a") != ISS.stable_anchor("b")
    assert len(ISS.stable_anchor(_INST)) == 32


def test_issue_then_consumer_unwraps_same_tile():
    # Issuer wraps; the SHARED build_egress_ratchet reproduces the tile; unwrap OK.
    b64, commitment = ISS.issue_egress_descriptor(_TOKEN, _INST, _policy())
    assert commitment and len(commitment) == 64
    wrapped = ISS  # placeholder for clarity
    from elr import WrappedCapabilityDescriptor  # type: ignore
    wd = WrappedCapabilityDescriptor.from_bytes(base64.b64decode(b64))
    tile = ISS.build_egress_ratchet(_TOKEN, _INST).derive_tile(ISS.EGRESS_LABEL)
    plaintext = CapabilityEnvelope.unwrap(wd, tile)
    assert plaintext is not None
    assert plaintext["allowed_hosts"] == ["ollama.lan"]
    assert plaintext["default_action"] == "deny"


def test_wrong_instance_cannot_unwrap():
    b64, _ = ISS.issue_egress_descriptor(_TOKEN, _INST, _policy())
    from elr import WrappedCapabilityDescriptor  # type: ignore
    wd = WrappedCapabilityDescriptor.from_bytes(base64.b64decode(b64))
    other_tile = ISS.build_egress_ratchet(_TOKEN, "different-instance").derive_tile(ISS.EGRESS_LABEL)
    assert CapabilityEnvelope.unwrap(wd, other_tile) is None  # fail-closed


def test_consumer_helper_fail_open_when_no_license(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: None)
    cfg = {"spec": {"elr": {"capabilities": {ISS.EGRESS_LABEL: {"wrapped_bytes_b64": "x", "version": 1}}}}}
    assert ISS.build_egress_registry_and_ratchet_for_tenant(cfg) is None


def test_consumer_helper_fail_open_when_no_descriptor(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: (_TOKEN, _INST))
    assert ISS.build_egress_registry_and_ratchet_for_tenant({"spec": {}}) is None


def test_consumer_helper_builds_when_descriptor_present(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: (_TOKEN, _INST))
    b64, _ = ISS.issue_egress_descriptor(_TOKEN, _INST, _policy())
    cfg = {"spec": {"elr": {"capabilities": {ISS.EGRESS_LABEL: {"wrapped_bytes_b64": b64, "version": 1}}}}}
    result = ISS.build_egress_registry_and_ratchet_for_tenant(cfg)
    assert result is not None
    ratchet, registry = result
    # the consumer can unwrap the issued descriptor with its own ratchet
    wd = registry.get_descriptor(ISS.EGRESS_LABEL)
    pt = CapabilityEnvelope.unwrap(wd, ratchet.derive_tile(ISS.EGRESS_LABEL))
    assert pt is not None and pt["forbidden_hosts"] == ["api.anthropic.com"]


def test_active_license_material_handles_frozen_proxy():
    # Regression for the CRITICAL: _ACTIVE_LICENSE is a recursive MappingProxyType;
    # active_license_material MUST un-proxy before json.dumps or it silently
    # returns None (permanent fail-open). Set a REAL frozen license the same way
    # the validator does and assert we get usable material — NOT None.
    try:
        from license import validator as V  # type: ignore
    except Exception:  # noqa: BLE001
        import validator as V  # type: ignore
    if not hasattr(V, "_set_active_license"):
        pytest.skip("validator._set_active_license unavailable")
    raw = {"tier": "personal", "exp": 9999999999,
           "limits": {"instance_id_bound": "inst-real-1", "engines_allowed": ["claude_code"]}}
    V._set_active_license(raw)
    try:
        import types as _t
        assert isinstance(V._ACTIVE_LICENSE, _t.MappingProxyType)  # really frozen
        mat = ISS.active_license_material()
        assert mat is not None, "frozen MappingProxyType license must serialize, not fail-open to None"
        token, instance_id = mat
        assert instance_id == "inst-real-1"
        assert isinstance(token, bytes) and len(token) >= 32
    finally:
        V._set_active_license(None)


def test_write_descriptor_preserves_other_keys():
    pytest.importorskip("yaml")
    with tempfile.TemporaryDirectory() as td:
        cfg = Path(td) / "tenant.corvin.yaml"
        cfg.write_text("spec:\n  egress:\n    enabled: true\n  other: keep-me\n", encoding="utf-8")
        ISS.write_descriptor_to_tenant_config(cfg, ISS.EGRESS_LABEL, "AAAA", version=2)
        import yaml  # type: ignore
        out = yaml.safe_load(cfg.read_text())
        assert out["spec"]["other"] == "keep-me"
        assert out["spec"]["egress"]["enabled"] is True
        assert out["spec"]["elr"]["capabilities"][ISS.EGRESS_LABEL]["wrapped_bytes_b64"] == "AAAA"
        assert out["spec"]["elr"]["capabilities"][ISS.EGRESS_LABEL]["version"] == 2


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
