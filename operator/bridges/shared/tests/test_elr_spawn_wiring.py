"""ADR-0167 — live spawn-path ratchet wiring (point a) end-to-end.

Proves that once a tenant has ISSUED an egress descriptor and an instance-bound
license is active, spawn_gates.check_l35 enforces the RATCHET-derived policy —
blocking a host the permissive static policy would allow. And that a tenant
WITHOUT a descriptor is byte-identical to the pre-ADR-0167 static behaviour
(fail-open-to-static).
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parents[1]
_LIC = _SHARED.parents[1] / "license"
for _p in (str(_SHARED), str(_LIC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

pytest.importorskip("yaml")
import yaml  # type: ignore

import elr_issuer as ISS  # type: ignore
import spawn_gates as SG  # type: ignore

_TOKEN = b"w" * 96
_INST = "inst-wiring-test"
_ENGINE = "claude_code"          # → api.anthropic.com per DEFAULT_ENGINE_HOSTS
_HOST = "api.anthropic.com"


def _write_tenant_config(home: Path, *, with_descriptor: bool) -> None:
    cfg = {"spec": {"egress": {
        "enabled": True,
        "default_action": "allow",   # PERMISSIVE static policy
        "allowed_hosts": [],
        "forbidden_hosts": [],
    }}}
    if with_descriptor:
        # Issue a descriptor that FORBIDS the engine host, with the same
        # token+instance the consumer will use.
        from elr_capabilities_m2 import EgressPaidPresetCapability  # type: ignore
        policy = EgressPaidPresetCapability(
            allowed_hosts=["localhost"], forbidden_hosts=[_HOST],
            default_action="deny", expires_at_epoch_k=0).to_dict()
        b64, _ = ISS.issue_egress_descriptor(_TOKEN, _INST, policy)
        cfg["spec"]["elr"] = {"capabilities": {
            ISS.EGRESS_LABEL: {"wrapped_bytes_b64": b64, "version": 1}}}
    p = home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(yaml.safe_dump(cfg), encoding="utf-8")


def test_ratchet_enforces_when_descriptor_issued(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: (_TOKEN, _INST))
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "corvin"
        _write_tenant_config(home, with_descriptor=True)
        SG.invalidate_cache()
        msg = SG.check_l35(_ENGINE, "_default", corvin_home=home)
        assert msg is not None, "ratchet-derived forbid must block the spawn"
        assert _HOST in msg


def test_fail_open_to_static_without_descriptor(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: (_TOKEN, _INST))
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "corvin"
        _write_tenant_config(home, with_descriptor=False)
        SG.invalidate_cache()
        msg = SG.check_l35(_ENGINE, "_default", corvin_home=home)
        assert msg is None, "no descriptor → permissive static policy (allow), unchanged"


def test_fail_open_to_static_without_license(monkeypatch):
    monkeypatch.setattr(ISS, "active_license_material", lambda: None)
    with tempfile.TemporaryDirectory() as td:
        home = Path(td) / "corvin"
        _write_tenant_config(home, with_descriptor=True)
        SG.invalidate_cache()
        msg = SG.check_l35(_ENGINE, "_default", corvin_home=home)
        assert msg is None, "no active license → static fallback (allow), never a crash"


def test_wiring_license_dir_anchor_is_correct():
    # Regression for the HIGH: the wiring resolves operator/license as parents[2]
    # of spawn_gates (parents[1] == operator/bridges has no license/ — that
    # import-fail would silently cache a ratchet-less gate).
    sg = Path(SG.__file__).resolve()
    good = sg.parents[2] / "license"
    bad = sg.parents[1] / "license"
    assert good.is_dir() and (good / "elr_issuer.py").is_file(), good
    assert not bad.is_dir(), f"unexpected {bad} — anchor must be parents[2]"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
