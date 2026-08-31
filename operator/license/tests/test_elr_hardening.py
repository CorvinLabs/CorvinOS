"""ADR-0167 security-review hardening (2026-06-27).

Covers the two derivation/policy hardening fixes:
  H1  make_root_from_license_token binds the root to instance_id when supplied
      (a token lifted to another machine derives a DIFFERENT root); legacy
      (instance_id=None) keeps the pre-change value for back-compat.
  H2  EgressPaidPresetCapability default_action is fail-CLOSED ("deny"): a
      descriptor that omits the field must not silently open egress.
"""
from __future__ import annotations

import sys
from pathlib import Path

_LIC = Path(__file__).resolve().parents[1]
if str(_LIC) not in sys.path:
    sys.path.insert(0, str(_LIC))

from elr import make_root_from_license_token  # type: ignore
from elr_capabilities_m2 import EgressPaidPresetCapability  # type: ignore


_TOKEN = b"x" * 64  # ≥32 bytes


# ── H1 — instance-id binding ─────────────────────────────────────────
def test_root_differs_per_instance_id():
    a = make_root_from_license_token(_TOKEN, instance_id="instance-a")
    b = make_root_from_license_token(_TOKEN, instance_id="instance-b")
    assert a != b, "same token on two instances must derive different roots"


def test_root_bound_differs_from_legacy():
    legacy = make_root_from_license_token(_TOKEN)
    bound = make_root_from_license_token(_TOKEN, instance_id="instance-a")
    assert bound != legacy, "instance-bound root must differ from the unbound one"


def test_root_is_deterministic_per_instance():
    a1 = make_root_from_license_token(_TOKEN, instance_id="instance-a")
    a2 = make_root_from_license_token(_TOKEN, instance_id="instance-a")
    assert a1 == a2, "same token + same instance must be deterministic"


def test_legacy_root_unchanged():
    # Back-compat: instance_id=None must keep the exact pre-change derivation
    # (HKDF info == b"elr-root-v1"). Re-derive the legacy value independently.
    from sob_crypto import hkdf_derive  # type: ignore
    assert make_root_from_license_token(_TOKEN) == hkdf_derive(_TOKEN, b"elr-root-v1", length=32)


def test_root_length_32():
    assert len(make_root_from_license_token(_TOKEN, instance_id="i")) == 32


# ── H2 — fail-closed egress default ──────────────────────────────────
def test_default_action_is_deny():
    assert EgressPaidPresetCapability().default_action == "deny"


def test_from_dict_missing_default_action_is_deny():
    cap = EgressPaidPresetCapability.from_dict({"capability_id": "egress-paid-preset"})
    assert cap is not None
    assert cap.default_action == "deny", "a descriptor without default_action must fail closed"


def test_from_dict_explicit_allow_honored():
    cap = EgressPaidPresetCapability.from_dict({"default_action": "allow"})
    assert cap is not None and cap.default_action == "allow"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
