"""Tests for CLAG M4 — A2A instruction gate (ADR-0133 L38)."""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import secrets
import sys
import tempfile
import time
import uuid
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_here = Path(__file__).resolve().parent
_forge_inner = _here.parents[1] / "forge" / "forge"
for _p in (_here, _forge_inner):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

try:
    from clag import ChainIntegrityFailure as _RealCIF, clear_shadow_hashes
    HAS_CLAG = True
except ImportError:
    HAS_CLAG = False

# ── Mock forge_se BEFORE importing rtr ───────────────────────────────────────
_mock_se = MagicMock()
_mock_se.write_event = MagicMock(return_value={"hash": "abc"})
patch("remote_trigger_receiver._forge_se", _mock_se).start()

import remote_trigger_receiver as rtr  # noqa: E402


# ── Constants ─────────────────────────────────────────────────────────────────
HMAC_KEY = "c4d5e6f7a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4"
RECV_KEY = "d5e6f7a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5f6a1b2c3d4e5"
ORIGIN_ID = "clag-m4-test-origin"


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_shadows():
    if HAS_CLAG:
        clear_shadow_hashes()
    yield
    if HAS_CLAG:
        clear_shadow_hashes()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _write_origin(tmpdir: Path, *, spawn_worker: bool = False) -> Path:
    cfg = {
        "origin_id": ORIGIN_ID,
        "hmac_key": HMAC_KEY,
        "recv_key": RECV_KEY,
        "enabled": True,
        "max_ttl_s": 300,
        "allowed_personas": ["assistant"],
        "spawn_worker": spawn_worker,
    }
    p = tmpdir / f"{ORIGIN_ID}.json"
    p.write_text(json.dumps(cfg))
    p.chmod(0o600)
    return p


def _build_envelope(*, instruction: str = "echo hello") -> dict:
    env: dict = {
        "task_id": str(uuid.uuid4()),
        "nonce": secrets.token_hex(32),
        "issued_at": time.time(),
        "origin_id": ORIGIN_ID,
        "instruction": instruction,
        "result_schema": {},
        "ttl_s": 60,
        "sender_instance_id": "test-sender-iid",
        "attachments": [],
        "signature": "",
    }
    payload = {k: v for k, v in env.items() if k != "signature"}
    sig = _hmac.new(
        bytes.fromhex(HMAC_KEY),
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode(),
        hashlib.sha256,
    ).hexdigest()
    env["signature"] = sig
    return env


def _make_receiver(tmpdir: Path) -> rtr.RemoteTriggerReceiver:
    return rtr.RemoteTriggerReceiver(origins_dir=tmpdir)


# ── Tests ─────────────────────────────────────────────────────────────────────

def test_clag_gate_intact_chain_passes():
    """receive() succeeds when _clag_gate_a2a returns normally (intact chain)."""
    with patch.object(rtr, "_clag_gate_a2a", return_value=None):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin(tmpdir)
            receiver = _make_receiver(tmpdir)
            resp = receiver.receive(_build_envelope())
    assert resp.status == "ok", f"expected 'ok', got '{resp.status}'"


def test_clag_gate_broken_chain_returns_rejected():
    """receive() returns 'rejected' when CLAG gate raises ChainIntegrityFailure."""

    class _FakeChainIntegrityFailure(Exception):
        pass

    def _fail_gate(layer_id: str) -> None:
        raise _FakeChainIntegrityFailure("simulated broken chain")

    audit_calls: list[tuple[str, dict]] = []

    def _capture_audit(self, event_type: str, severity: str, details: dict) -> None:
        audit_calls.append((event_type, dict(details)))

    with patch.object(rtr, "_clag_gate_a2a", side_effect=_fail_gate):
        with patch.object(rtr.RemoteTriggerReceiver, "_audit_best_effort", _capture_audit):
            with tempfile.TemporaryDirectory() as td:
                tmpdir = Path(td)
                _write_origin(tmpdir)
                receiver = _make_receiver(tmpdir)
                resp = receiver.receive(_build_envelope())

    assert resp.status == "rejected", f"expected 'rejected', got '{resp.status}'"
    rejection_events = [
        d for (et, d) in audit_calls if et == "A2A.request_rejected"
    ]
    assert rejection_events, "A2A.request_rejected audit event not emitted"
    assert any(d.get("reason") == "chain_integrity_failed" for d in rejection_events), (
        f"expected reason='chain_integrity_failed', got: {rejection_events}"
    )


def test_clag_gate_fails_open_when_not_importable():
    """When _clag_gate_a2a returns None (ImportError path), receive() proceeds."""
    with patch.object(rtr, "_clag_gate_a2a", return_value=None):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin(tmpdir)
            receiver = _make_receiver(tmpdir)
            resp = receiver.receive(_build_envelope())
    assert resp.status == "ok"


def test_clag_gate_uses_unique_layer_id_per_receive():
    """Each receive() call generates a distinct L38.a2a_instruction.* layer_id."""
    observed_ids: list[str] = []

    def _capture_gate(layer_id: str) -> None:
        observed_ids.append(layer_id)

    with patch.object(rtr, "_clag_gate_a2a", side_effect=_capture_gate):
        with tempfile.TemporaryDirectory() as td:
            tmpdir = Path(td)
            _write_origin(tmpdir)
            receiver = _make_receiver(tmpdir)
            for _ in range(3):
                receiver.receive(_build_envelope())

    assert len(observed_ids) == 3
    assert len(set(observed_ids)) == 3, (
        f"layer_ids must be unique per receive() call: {observed_ids}"
    )
    for lid in observed_ids:
        assert lid.startswith("L38.a2a_instruction."), f"unexpected layer_id: {lid!r}"


def test_clag_gate_a2a_fail_open_when_forge_genuinely_absent():
    """FND-17: forge GENUINELY absent (_forge_se is None) → fail-open (no raise)."""
    with patch.object(rtr, "_forge_se", None), \
         patch.dict("sys.modules", {"forge": None, "forge.clag": None}):
        # Must neither raise ImportError nor the gate-unavailable error.
        rtr._clag_gate_a2a("L38.a2a_instruction.test")


def test_clag_gate_a2a_fail_closed_when_forge_present_clag_broken():
    """FND-17: forge present (audits active, _forge_se set) but clag unimportable
    is a BROKEN gate → fail-CLOSED (raise), never silently fail-open."""
    with patch.object(rtr, "_forge_se", object()), \
         patch.dict("sys.modules", {"forge.clag": None}):
        with pytest.raises(rtr._ChainIntegrityFailureGateUnavailable):
            rtr._clag_gate_a2a("L38.a2a_instruction.test")
