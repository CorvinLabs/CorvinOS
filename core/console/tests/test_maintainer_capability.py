"""ADR-0178 M3 — the maintainer.commit capability gate (deny-by-default).

A valid, signed, instance-bound, unexpired token enters Tier CONTRIBUTOR; every
other case (no token, wrong key, expired, instance mismatch, wrong cap, tampered)
is denied.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "core" / "console", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import pytest
from corvin_console.aco import maintainer_capability as MC  # type: ignore

ed = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.ed25519")


def _keypair():
    sk = ed.Ed25519PrivateKey.generate()
    from cryptography.hazmat.primitives import serialization
    raw_priv = sk.private_bytes(serialization.Encoding.Raw,
                                serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())
    raw_pub = sk.public_key().public_bytes(serialization.Encoding.Raw,
                                            serialization.PublicFormat.Raw)
    return raw_priv, raw_pub


def test_valid_capability_allows():
    priv, pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway", now=1000)
    v = MC.verify(tok, instance_id="inst-A", public_key_bytes=pub, now=1001)
    assert v.allowed and v.reason == "ok" and v.subject == "shumway"


def test_no_token_denied():
    assert MC.verify(None, instance_id="inst-A", public_key_bytes=b"x" * 32).allowed is False
    assert MC.verify("", instance_id="inst-A", public_key_bytes=b"x" * 32).reason == "no_token"


def test_no_pinned_pubkey_denied(monkeypatch):
    monkeypatch.delenv("CORVIN_MAINTAINER_PUBKEY", raising=False)
    priv, pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway")
    # public_key_bytes omitted + no env pin → deny-by-default
    assert MC.verify(tok, instance_id="inst-A").reason == "no_pubkey"


def test_wrong_key_denied():
    priv, _ = _keypair()
    _, other_pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway")
    v = MC.verify(tok, instance_id="inst-A", public_key_bytes=other_pub)
    assert v.allowed is False and v.reason == "bad_signature"


def test_expired_denied():
    priv, pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway", ttl_seconds=10, now=1000)
    v = MC.verify(tok, instance_id="inst-A", public_key_bytes=pub, now=2000)
    assert v.allowed is False and v.reason == "expired"


def test_instance_mismatch_denied():
    priv, pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway")
    v = MC.verify(tok, instance_id="inst-B", public_key_bytes=pub)
    assert v.allowed is False and v.reason == "wrong_instance"


def test_tampered_payload_denied():
    import base64, json
    priv, pub = _keypair()
    tok = MC.issue(priv, instance_id="inst-A", subject="shumway", now=1000)
    outer = json.loads(base64.b64decode(tok))
    outer["payload"]["subject"] = "attacker"  # tamper after signing
    bad = base64.b64encode(json.dumps(outer, separators=(",", ":")).encode()).decode()
    v = MC.verify(bad, instance_id="inst-A", public_key_bytes=pub)
    assert v.allowed is False and v.reason == "bad_signature"


def test_wrong_cap_name_denied():
    import base64, json
    priv, pub = _keypair()
    # hand-mint a token with a different cap name, correctly signed
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    payload = {"cap": "something.else", "instance_id": "inst-A", "subject": "x",
               "repo": "r", "iat": 1, "exp": 9999999999}
    sig = Ed25519PrivateKey.from_private_bytes(priv).sign(MC._canonical(payload))
    tok = base64.b64encode(MC._canonical(
        {"payload": payload, "sig": base64.b64encode(sig).decode()})).decode()
    v = MC.verify(tok, instance_id="inst-A", public_key_bytes=pub)
    assert v.allowed is False and v.reason == "wrong_cap"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
