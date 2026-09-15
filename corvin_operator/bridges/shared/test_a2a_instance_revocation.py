"""R3-1 regression: A2A instance-level revocation (manifest.revoked_instance_ids)
is enforced UNCONDITIONALLY post-HMAC in _validate — a revoked peer that OMITS
the network_attestation block must still be rejected (the prior in-branch check
left exactly that gap)."""
import hashlib
import hmac as _hmac
import json
import os
import secrets
import time
import uuid
from pathlib import Path

import pytest

import remote_trigger_receiver as rtr

_HMAC_KEY = "ab" * 32
_RECV_KEY = "cd" * 32
_ORIGIN = "peer1"


def _origin(tmp: Path):
    p = tmp / f"{_ORIGIN}.json"
    p.write_text(json.dumps({
        "origin_id": _ORIGIN, "hmac_key": _HMAC_KEY, "recv_key": _RECV_KEY,
        "enabled": True, "max_ttl_s": 300, "allowed_personas": ["assistant"],
    }))
    p.chmod(0o600)


def _envelope(sender_instance_id: str) -> dict:
    env = {
        "task_id": str(uuid.uuid4()), "nonce": secrets.token_hex(32),
        "issued_at": time.time(), "origin_id": _ORIGIN, "instruction": "echo hi",
        "result_schema": {}, "ttl_s": 60, "sender_instance_id": sender_instance_id,
        "attachments": [], "signature": "",
    }
    payload = {k: v for k, v in env.items() if k != "signature"}
    env["signature"] = _hmac.new(
        bytes.fromhex(_HMAC_KEY),
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    return env


class _FakeManifest:
    revoked_sest_fps: set = set()
    revoked_pairing_ids: set = set()
    revoked_instance_ids = {"BAD-INSTANCE"}
    attestation_mandatory_after = 9_999_999_999


@pytest.fixture(autouse=True)
def _isolate(monkeypatch, tmp_path):
    # No network_attestation block on the envelope; isolate the unconditional
    # post-HMAC instance check from the attestation membership flow.
    monkeypatch.setenv("CORVIN_A2A_ATTESTATION_DISABLED", "1")
    monkeypatch.setattr(rtr, "_load_a2a_manifest", lambda: _FakeManifest())
    _origin(tmp_path)


def test_revoked_instance_without_attestation_is_rejected(tmp_path):
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    with pytest.raises(rtr.ValidationError) as ei:
        rcv._validate(_envelope("BAD-INSTANCE"))
    assert ei.value.reason == "network_attestation_instance_revoked"


def test_non_revoked_instance_passes(tmp_path):
    rcv = rtr.RemoteTriggerReceiver(origins_dir=tmp_path)
    env, cfg = rcv._validate(_envelope("GOOD-INSTANCE"))   # must not raise
    assert env.sender_instance_id == "GOOD-INSTANCE"
