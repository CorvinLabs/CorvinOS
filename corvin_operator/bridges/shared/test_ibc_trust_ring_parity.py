"""The IBC trust ring and the license validator's session key ring must stay identical.

The issuer signs IBCs with ``ibc-vN`` and session/license tokens with
``sess-vN``/``lic-vN`` from ONE keypair per N, so every ``sess-vN`` the
validator trusts must be in the IBC ring with the same key and vice versa.
A key added to only one of them makes that release reject every IBC (or every
license) signed with it — a fleet-wide A2A outage once IBCs are required
(ADR-2099 fact 10). Rotation rule: pin ``sess-v<N+1>`` in BOTH rings and ship
it at least one release before the issuer's CURRENT_SESSION_KID moves to it.
"""
import importlib.util
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import instance_identity  # noqa: E402


def _validator_ring() -> dict:
    path = HERE.parents[1] / "license" / "validator.py"
    spec = importlib.util.spec_from_file_location("_validator_for_ring_parity", path)
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(path.parent))
    spec.loader.exec_module(mod)
    return dict(mod.SESSION_SERVER_KEY_RING)


def test_ibc_ring_equals_validator_session_ring():
    validator = {k: v for k, v in _validator_ring().items() if k.startswith("sess-")}
    assert validator, "validator ring has no sess- keys"
    assert dict(instance_identity._IBC_TRUST_KEY_RING) == validator


if __name__ == "__main__":
    test_ibc_ring_equals_validator_session_ring()
    print("ok")
