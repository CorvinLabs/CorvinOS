"""ADR-0167 Rec-1 — the egress ratchet seam is now LIVE (import fix).

Before 2026-06-27, ``_try_ratchet_policy_check`` imported ``operator.license.elr``
— ``operator`` is the stdlib module, so that import ALWAYS raised and the ratchet
could never engage (dead crypto). These tests construct a real ratchet, wrap an
egress capability descriptor, register it, attach it to an EgressGate, and prove
the ratchet-DERIVED policy now decides — overriding a deliberately-permissive
static policy. They would FAIL with the old broken import (static 'allow' would
leak a forbidden host).

Note: full production activation also needs the live spawn path to construct the
ratchet + registry and a descriptor issued into tenant.corvin.yaml
(spec.elr.capabilities). Those are operator/issuer steps; here we prove the
consumer seam itself is functional.
"""
from __future__ import annotations

import base64
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent
_LIC = _SHARED.parents[1] / "license"
for _p in (str(_SHARED), str(_LIC)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from egress_gate import EgressGate, EgressPolicy  # type: ignore
from elr import (  # type: ignore
    CapabilityEnvelope,
    CapabilityRegistry,
    EntangledRatchet,
    make_root_from_license_token,
)
from elr_capabilities_m2 import EgressPaidPresetCapability  # type: ignore

_LABEL = "egress-paid-preset"


def _gate_with_ratchet(*, wrong_tile: bool = False) -> EgressGate:
    token = b"x" * 64
    head = b"h" * 32
    root = make_root_from_license_token(token, instance_id="inst-1")
    ratchet = EntangledRatchet(root, head)
    tile_k = ratchet.derive_tile(_LABEL)

    cap = EgressPaidPresetCapability(
        allowed_hosts=["ollama.lan"],
        forbidden_hosts=["api.anthropic.com"],
        default_action="deny",
        expires_at_epoch_k=99,
    )
    # Optionally seal with a DIFFERENT key so unwrap fails (fail-closed→fallback).
    seal_key = (make_root_from_license_token(b"y" * 64, instance_id="other")
                if wrong_tile else tile_k)
    if wrong_tile:
        # derive a tile from the wrong root so the AEAD tag won't verify
        seal_key = EntangledRatchet(seal_key, head).derive_tile(_LABEL)
    wrapped = CapabilityEnvelope.wrap(cap.to_dict(), seal_key)
    b64 = base64.b64encode(wrapped.to_bytes()).decode()

    registry = CapabilityRegistry({"spec": {"elr": {"capabilities": {
        _LABEL: {"wrapped_bytes_b64": b64, "version": 1}}}}})

    # Static policy is deliberately PERMISSIVE so any 'block' must come from the
    # ratchet-derived policy, not the static one.
    static = EgressPolicy(enabled=True, allowed_hosts=[], forbidden_hosts=[],
                          default_action="allow")
    gate = EgressGate(policy=static, ratchet=ratchet, capability_label=_LABEL)
    gate.set_capability_registry(registry)
    return gate


def test_ratchet_blocks_forbidden_host_over_permissive_static():
    gate = _gate_with_ratchet()
    d = gate.validate("api.anthropic.com")
    assert d.allowed is False, "ratchet-derived forbidden host must be blocked"


def test_ratchet_allows_listed_host():
    gate = _gate_with_ratchet()
    d = gate.validate("ollama.lan")
    assert d.allowed is True


def test_ratchet_default_deny_blocks_unlisted_host():
    gate = _gate_with_ratchet()
    d = gate.validate("random-unlisted.example")
    assert d.allowed is False, "ratchet default_action=deny must block unlisted host"


def test_wrong_tile_fails_closed_to_static_fallback():
    # Descriptor sealed with the wrong key → unwrap returns None → fall back to
    # the static policy (here permissive 'allow'). Proves the failure mode is
    # fallback, never a crash, never a silent ratchet 'allow'.
    gate = _gate_with_ratchet(wrong_tile=True)
    d = gate.validate("api.anthropic.com")
    assert d.allowed is True  # static fallback (allow); ratchet did not engage


def test_no_ratchet_uses_static_policy():
    # Baseline: without a ratchet (the pre-fix world), the forbidden host is
    # allowed by the permissive static policy — exactly the gap the fix closes.
    static = EgressPolicy(enabled=True, allowed_hosts=[], forbidden_hosts=[],
                          default_action="allow")
    gate = EgressGate(policy=static)
    assert gate.validate("api.anthropic.com").allowed is True


def test_static_forbidden_wins_over_ratchet_allow():
    # F6 (security review 2026-06-27): a host on the operator's static
    # forbidden_hosts list must be denied even when the ratchet-derived
    # capability would ALLOW it. Explicit forbid is a hard deny that the
    # ratchet can never re-permit.
    token = b"x" * 64
    head = b"h" * 32
    root = make_root_from_license_token(token, instance_id="inst-1")
    ratchet = EntangledRatchet(root, head)
    tile_k = ratchet.derive_tile(_LABEL)
    cap = EgressPaidPresetCapability(
        allowed_hosts=["ollama.lan"],          # ratchet would ALLOW this host
        forbidden_hosts=[],
        default_action="deny",
        expires_at_epoch_k=99,
    )
    wrapped = CapabilityEnvelope.wrap(cap.to_dict(), tile_k)
    b64 = base64.b64encode(wrapped.to_bytes()).decode()
    registry = CapabilityRegistry({"spec": {"elr": {"capabilities": {
        _LABEL: {"wrapped_bytes_b64": b64, "version": 1}}}}})
    # …but the operator statically forbids the very same host.
    static = EgressPolicy(enabled=True, allowed_hosts=[],
                          forbidden_hosts=["ollama.lan"], default_action="allow")
    gate = EgressGate(policy=static, ratchet=ratchet, capability_label=_LABEL)
    gate.set_capability_registry(registry)

    d = gate.validate("ollama.lan")
    assert d.allowed is False, "static forbidden_hosts must win over ratchet allow"
    assert d.matched_rule == "forbidden_explicit"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
