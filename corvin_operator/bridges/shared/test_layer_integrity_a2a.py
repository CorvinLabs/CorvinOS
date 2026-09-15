"""Per-subtask E2E — ADR-0141 Tier 2: A2A layer_integrity_hash attestation.

Covers the sender hash computation, the receiver step-6.85 check, and the
Protocol-v7 grace / per-origin enforcement — all without standing up a real
HTTP exchange (the hash path is HMAC-bound by the existing envelope machinery,
already covered by the A2A crypto E2E).

Runnable standalone:
    python3 operator/bridges/shared/test_layer_integrity_a2a.py
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


import layer_integrity as li  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402
from remote_trigger_sender import RemoteTriggerSender  # noqa: E402


def _make_env(network_attestation):
    return rtr.TaskEnvelope(
        task_id="t1", nonce="n1", issued_at=0.0, origin_id="peer-a",
        instruction="hi", result_schema={}, ttl_s=60,
        sender_instance_id="iid-1", attachments=[], signature="sig",
        network_attestation=network_attestation,
    )


def _fake_receiver():
    rcv = rtr.RemoteTriggerReceiver.__new__(rtr.RemoteTriggerReceiver)
    rcv._audit_best_effort = lambda *a, **k: None  # type: ignore[attr-defined]
    return rcv


def _install_manifest(monkey_hashes: dict | None, *, mandatory_after=None, sig_ok=True):
    """Patch layer_integrity to present a controlled, signature-valid manifest."""
    manifest = None
    if monkey_hashes is not None:
        manifest = {
            "schema_version": 1, "issued_at": 1,
            "mandatory_after": mandatory_after,
            "mandatory_layers": monkey_hashes,
            "manifest_sig": "x",
        }
    li.load_manifest = lambda root=None: manifest  # type: ignore[assignment]
    li.verify_manifest_signature = lambda m, root=None: sig_ok  # type: ignore[assignment]


def test_sender_hash_matches_module() -> None:
    h = RemoteTriggerSender._compute_layer_integrity_hash()
    t("sender hash == module aggregate",
      h == li.compute_layer_integrity_hash(), detail=str(h)[:24])


def test_attestation_block_has_v7_fields() -> None:
    # _build_network_attestation needs a SesT; if none is available the block is
    # None — in that case the v7 fields are simply absent (free instance). We
    # only assert the hash-folding contract when a block is produced.
    block = RemoteTriggerSender._build_network_attestation({"pairing_id": "p1"})
    if block is None:
        t("no SesT -> no attestation block (free instance, acceptable)", True)
        return
    t("block carries layer_integrity_hash", "layer_integrity_hash" in block)
    t("block marks protocol_version 7", block.get("protocol_version") == 7)


def test_receiver_matching_hash_passes() -> None:
    real = li.compute_layer_hashes()
    _install_manifest(real)
    expected = li.compute_layer_integrity_hash()
    rcv = _fake_receiver()
    env = _make_env({"layer_integrity_hash": expected})
    ok = True
    try:
        rcv._check_layer_integrity_attestation(env, {}, b"k")
    except rtr.ValidationError:
        ok = False
    t("matching hash accepted", ok)


def test_receiver_mismatch_rejected() -> None:
    real = li.compute_layer_hashes()
    _install_manifest(real)
    rcv = _fake_receiver()
    env = _make_env({"layer_integrity_hash": "sha256:deadbeef"})
    reason = None
    try:
        rcv._check_layer_integrity_attestation(env, {}, b"k")
    except rtr.ValidationError as e:
        reason = e.reason
    t("mismatched hash rejected", reason == "layer_integrity_mismatch", detail=str(reason))


def test_receiver_absent_in_grace_allowed() -> None:
    real = li.compute_layer_hashes()
    _install_manifest(real, mandatory_after=None)
    rcv = _fake_receiver()
    env = _make_env({"sest_fp": "x"})  # no layer_integrity_hash
    ok = True
    try:
        rcv._check_layer_integrity_attestation(env, {}, b"k")
    except rtr.ValidationError:
        ok = False
    t("absent hash in grace -> allowed", ok)


def test_receiver_absent_required_by_origin() -> None:
    real = li.compute_layer_hashes()
    _install_manifest(real, mandatory_after=None)
    rcv = _fake_receiver()
    env = _make_env({"sest_fp": "x"})
    reason = None
    try:
        rcv._check_layer_integrity_attestation(env, {"require_layer_integrity": True}, b"k")
    except rtr.ValidationError as e:
        reason = e.reason
    t("absent + require_layer_integrity -> rejected",
      reason == "layer_integrity_required", detail=str(reason))


def test_receiver_absent_past_deadline() -> None:
    real = li.compute_layer_hashes()
    _install_manifest(real, mandatory_after=1)  # 1970 -> long past
    rcv = _fake_receiver()
    env = _make_env({"sest_fp": "x"})
    reason = None
    try:
        rcv._check_layer_integrity_attestation(env, {}, b"k")
    except rtr.ValidationError as e:
        reason = e.reason
    t("absent past mandatory_after -> rejected",
      reason == "layer_integrity_required", detail=str(reason))


def test_receiver_no_manifest_cannot_enforce() -> None:
    _install_manifest(None)  # receiver has no signed manifest
    rcv = _fake_receiver()
    env = _make_env({"layer_integrity_hash": "sha256:deadbeef"})  # would mismatch
    ok = True
    try:
        rcv._check_layer_integrity_attestation(env, {}, b"k")
    except rtr.ValidationError:
        ok = False
    t("no receiver manifest -> grace (cannot enforce)", ok)


def main() -> int:
    test_sender_hash_matches_module()
    test_attestation_block_has_v7_fields()
    test_receiver_matching_hash_passes()
    test_receiver_mismatch_rejected()
    test_receiver_absent_in_grace_allowed()
    test_receiver_absent_required_by_origin()
    test_receiver_absent_past_deadline()
    test_receiver_no_manifest_cannot_enforce()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
