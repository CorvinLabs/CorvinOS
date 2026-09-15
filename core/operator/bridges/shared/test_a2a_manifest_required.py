"""A2A-ATT-06 (ADR-0146): per-origin ``a2a_manifest_required`` fail-closed flag.

When set, a request is rejected whenever no AUTHENTIC (signature-verified) A2A
manifest is available — closing the network-blockable revocation fail-open for
operators who need revocation to be load-bearing. Default-off leaves the
permissive baseline unchanged.
"""
import hashlib
import hmac as _hmac
import json
import secrets
import time
import uuid
from pathlib import Path

import pytest

import remote_trigger_receiver as rtr

_HMAC_KEY = "ab" * 32
_RECV_KEY = "cd" * 32
_ORIGIN = "peer-req"


def _origin(tmp: Path, *, manifest_required: bool):
    p = tmp / f"{_ORIGIN}.json"
    p.write_text(json.dumps({
        "origin_id": _ORIGIN, "hmac_key": _HMAC_KEY, "recv_key": _RECV_KEY,
        "enabled": True, "max_ttl_s": 300, "allowed_personas": ["assistant"],
        "a2a_manifest_required": manifest_required,
    }))
    p.chmod(0o600)


def _envelope() -> dict:
    env = {
        "task_id": str(uuid.uuid4()), "nonce": secrets.token_hex(32),
        "issued_at": time.time(), "origin_id": _ORIGIN, "instruction": "echo hi",
        "result_schema": {}, "ttl_s": 60, "sender_instance_id": "GOOD-INSTANCE",
        "attachments": [], "signature": "",
    }
    payload = {k: v for k, v in env.items() if k != "signature"}
    env["signature"] = _hmac.new(
        bytes.fromhex(_HMAC_KEY),
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    return env


class _EmptyManifest:
    """Permissive manifest the loader returns when no signed manifest is usable."""
    revoked_sest_fps: set = set()
    revoked_pairing_ids: set = set()
    revoked_instance_ids: set = set()
    attestation_mandatory_after = 9_999_999_999
    sig_verified = False


class _AuthenticManifest(_EmptyManifest):
    sig_verified = True


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    # Isolate from the network-attestation membership flow.
    monkeypatch.setenv("CORVIN_A2A_ATTESTATION_DISABLED", "1")


def test_manifest_required_rejects_when_no_authentic_manifest(monkeypatch, tmp_path):
    _origin(tmp_path, manifest_required=True)
    monkeypatch.setattr(rtr, "_load_a2a_manifest", lambda: _EmptyManifest())
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    with pytest.raises(rtr.ValidationError) as ei:
        rcv._validate(_envelope())
    assert ei.value.reason == "a2a_manifest_required_unavailable"


def test_manifest_required_rejects_when_manifest_loader_raises(monkeypatch, tmp_path):
    _origin(tmp_path, manifest_required=True)
    def _boom():
        raise RuntimeError("network blocked")
    monkeypatch.setattr(rtr, "_load_a2a_manifest", _boom)
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    with pytest.raises(rtr.ValidationError) as ei:
        rcv._validate(_envelope())
    assert ei.value.reason == "a2a_manifest_required_unavailable"


def test_manifest_required_passes_with_authentic_manifest(monkeypatch, tmp_path):
    _origin(tmp_path, manifest_required=True)
    monkeypatch.setattr(rtr, "_load_a2a_manifest", lambda: _AuthenticManifest())
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    env, cfg = rcv._validate(_envelope())  # must not raise
    assert env.sender_instance_id == "GOOD-INSTANCE"


def test_default_off_is_permissive_without_authentic_manifest(monkeypatch, tmp_path):
    _origin(tmp_path, manifest_required=False)
    monkeypatch.setattr(rtr, "_load_a2a_manifest", lambda: _EmptyManifest())
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    env, cfg = rcv._validate(_envelope())  # default baseline unchanged → no raise
    assert env.sender_instance_id == "GOOD-INSTANCE"
