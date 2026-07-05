"""Tests for CLAG — Chain-Locked Adaptive Gating (ADR-0133).

Proves:
  1. gate() succeeds on a clean chain and returns a valid CIT.
  2. gate() raises ChainIntegrityFailure when the hash-link is broken.
  3. Shadow hash mismatch is detected between consecutive gate() calls
     when the chain is modified externally.
  4. verify_cit() returns False for an expired CIT.
  5. CIT is tier-tied: a free-tier CIT fails verification against a paid seed.
  6. verify_last_k() returns no failures on a clean chain.
  7. write_epoch_anchor() writes an audit.epoch_anchor event.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

import pytest

# Make sibling modules importable directly (operator/forge/forge/ is the package dir).
import sys
_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from security_events import write_event  # type: ignore[import]
from clag import (  # type: ignore[import]
    ChainIntegrityFailure,
    ChainIntegrityToken,
    CIT_TTL_SECONDS,
    EPOCH_EVENTS,
    VERIFY_K_DEFAULT,
    _MAX_LAYER_CITS,
    clear_shadow_hashes,
    gate,
    verify_cit,
    verify_last_k,
    write_epoch_anchor,
    write_chain_anchor,
    verify_chain_anchor,
    _derive_cit_key,
    _layer_cits,
    _cit_lock,
)
from chain_dna import derive_seed_free, derive_seed_paid  # type: ignore[import]


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture()
def audit_path(tmp_path: Path) -> Path:
    path = tmp_path / "audit.jsonl"
    return path


@pytest.fixture(autouse=True)
def reset_shadows():
    """Clear shadow hashes before every test to prevent inter-test leakage."""
    clear_shadow_hashes()
    yield
    clear_shadow_hashes()


def _seed_chain(path: Path, n: int = 3) -> None:
    """Write n synthetic audit events to create a valid chain."""
    for i in range(n):
        write_event(path, "test.event", details={"seq": i})


# ── Test 1: clean chain → successful gate + valid CIT ─────────────────────────

def test_gate_clean_chain_returns_valid_cit(audit_path: Path) -> None:
    _seed_chain(audit_path, n=5)
    cit = gate(audit_path, "L22")
    assert isinstance(cit, ChainIntegrityToken)
    assert cit.layer_id == "L22"
    assert cit.is_fresh()
    assert len(cit.tail_hash) == 16
    assert len(cit.hmac_hex) == 64
    assert len(cit.fingerprint()) == 16


# ── Test 2: broken hash-link → ChainIntegrityFailure ──────────────────────────

def test_gate_raises_on_broken_hash_link(audit_path: Path) -> None:
    _seed_chain(audit_path, n=5)

    # Tamper: read all lines, corrupt the second-to-last event's hash
    lines = audit_path.read_text().splitlines()
    recs = [json.loads(ln) for ln in lines]
    recs[-2]["hash"] = "deadbeef0000dead"   # corrupt stored hash
    audit_path.write_text("\n".join(json.dumps(r) for r in recs) + "\n")

    with pytest.raises(ChainIntegrityFailure) as exc_info:
        gate(audit_path, "L22")
    assert exc_info.value.reason_code == "hash_link_broken"


# ── Test 3: shadow mismatch detected on chain modification between calls ───────

def test_gate_shadow_mismatch_on_external_write(audit_path: Path) -> None:
    _seed_chain(audit_path, n=3)

    # First gate() — establishes shadow for L29
    gate(audit_path, "L29")

    # External write that changes the tail (simulates another process or attacker)
    write_event(audit_path, "injected.event", details={"note": "external"})

    # Second gate() from the same layer — shadow says tail should be the CIT event,
    # but the chain now has an extra event. The new tail differs → shadow mismatch.
    with pytest.raises(ChainIntegrityFailure) as exc_info:
        gate(audit_path, "L29")
    assert exc_info.value.reason_code == "shadow_mismatch"


# ── Test 4: expired CIT is rejected by verify_cit() ──────────────────────────

def test_verify_cit_rejects_expired_token(audit_path: Path) -> None:
    _seed_chain(audit_path, n=2)
    cit = gate(audit_path, "L38", ttl=1)

    # Backdate the issue time so the TTL is exceeded
    cit.issued_at = int(time.time()) - 10

    assert not cit.is_fresh()
    assert not verify_cit(cit)


# ── Test 5: CIT is tier-tied (free vs paid seed) ──────────────────────────────

def test_cit_tier_tied_to_dna_seed(audit_path: Path) -> None:
    _seed_chain(audit_path, n=2)
    clear_shadow_hashes()

    free_seed = derive_seed_free()
    cit_free = gate(audit_path, "L10", dna_seed=free_seed)

    # Verifying the free-tier CIT with a paid seed should fail
    fake_paid = derive_seed_paid("fake.jwt.token", "inst-0000")
    assert not verify_cit(cit_free, dna_seed=fake_paid)

    # Verifying with the correct free seed must succeed
    assert verify_cit(cit_free, dna_seed=free_seed)


# ── Test 6: verify_last_k on a clean chain returns no failures ────────────────

def test_verify_last_k_clean_chain(audit_path: Path) -> None:
    _seed_chain(audit_path, n=15)
    failures = verify_last_k(audit_path, k=10)
    assert failures == []


# ── Test 7: write_epoch_anchor emits the correct event ────────────────────────

def test_write_epoch_anchor_event(audit_path: Path) -> None:
    _seed_chain(audit_path, n=2)
    write_epoch_anchor(audit_path)

    lines = audit_path.read_text().splitlines()
    events = [json.loads(ln) for ln in lines]
    anchor_events = [e for e in events if e.get("event_type") == "audit.epoch_anchor"]
    assert len(anchor_events) == 1
    d = anchor_events[0].get("details", {})
    assert "epoch" in d
    assert "tail_hash_prefix" in d
    assert "prev_epoch_tail_prefix" in d


# ── Test 8: empty chain — gate() succeeds without shadow check ────────────────

def test_gate_on_empty_chain(audit_path: Path) -> None:
    """Empty chain has no events to verify; gate() should succeed immediately."""
    cit = gate(audit_path, "L16")
    assert cit.is_fresh()


# ── Test 9: consecutive valid gates update shadow correctly ───────────────────

def test_consecutive_gate_calls_same_layer(audit_path: Path) -> None:
    _seed_chain(audit_path, n=3)
    # Two consecutive calls from L22 — no external modification — both succeed
    cit1 = gate(audit_path, "L22")
    cit2 = gate(audit_path, "L22")
    assert cit1.fingerprint() != cit2.fingerprint()  # different tails → different CITs


# ── Test 10: auto-seed from get_active_seed() ────────────────────────────────

def test_gate_auto_seed_from_active_seed(audit_path: Path) -> None:
    """gate() without dna_seed auto-resolves from set_chain_dna_seed().

    The issued CIT must verify with the active paid seed and must NOT verify
    with the free-tier seed — proving tier-coupling without explicit dna_seed
    at every call site.
    """
    from security_events import set_chain_dna_seed  # type: ignore[import]

    paid_seed = derive_seed_paid("fake.jwt.bytes.with.rsa-sig", "inst-abc123")
    set_chain_dna_seed(paid_seed)
    try:
        _seed_chain(audit_path, n=2)
        cit = gate(audit_path, "L22.auto_seed_test")
        assert cit.is_fresh()
        # Must verify with the paid seed (auto-resolved)
        assert verify_cit(cit, dna_seed=paid_seed), "CIT should verify with active paid seed"
        # Must NOT verify with the free-tier seed
        assert not verify_cit(cit, dna_seed=derive_seed_free()), (
            "CIT must not verify with free-tier seed when paid tier is active"
        )
    finally:
        # Reset to free-tier so other tests are not affected
        set_chain_dna_seed(derive_seed_free())


# ── Test 11: CIT self-verify detects seed tier drift ─────────────────────────

def test_gate_cit_self_verify_detects_seed_drift(audit_path: Path) -> None:
    """gate() detects LSAD seed drift via CIT self-verification.

    Scenario: first gate() uses a paid seed; the seed is then silently reset
    to None/free; the second gate() from the same layer should raise
    ChainIntegrityFailure with reason_code='cit_tampered'.
    """
    from security_events import set_chain_dna_seed, get_active_seed  # type: ignore[import]

    paid_seed = derive_seed_paid("another.fake.jwt", "inst-drift-test")
    set_chain_dna_seed(paid_seed)
    try:
        _seed_chain(audit_path, n=2)
        # First call — CIT issued with paid seed, cached in _layer_cits
        gate(audit_path, "L22.drift_test", dna_seed=paid_seed)

        # Simulate seed drift: reset to free-tier without updating _layer_cits
        free_seed = derive_seed_free()
        set_chain_dna_seed(free_seed)

        # Second call on the same layer — auto-resolved seed = free-tier.
        # CIT self-verify uses free-tier key but the cached CIT was signed with
        # the paid key → HMAC mismatch → ChainIntegrityFailure.
        with pytest.raises(ChainIntegrityFailure) as exc_info:
            gate(audit_path, "L22.drift_test")  # no explicit dna_seed → auto-resolves to free
        assert exc_info.value.reason_code == "cit_tampered", (
            f"Expected reason_code='cit_tampered', got {exc_info.value.reason_code!r}"
        )
    finally:
        set_chain_dna_seed(derive_seed_free())


# ── Test 12: VERIFY_K_DEFAULT matches EPOCH_EVENTS ───────────────────────────

def test_verify_k_equals_epoch_events() -> None:
    """K must equal EPOCH_EVENTS so the verification window covers one full epoch."""
    assert VERIFY_K_DEFAULT == EPOCH_EVENTS, (
        f"VERIFY_K_DEFAULT ({VERIFY_K_DEFAULT}) must equal EPOCH_EVENTS ({EPOCH_EVENTS}) "
        "to bound the tamper window to one epoch"
    )


# ── Test 13: _layer_cits bounded size prevents memory leak ───────────────────

def test_layer_cits_bounded_to_max_size(audit_path: "Path") -> None:
    """gate() must not grow _layer_cits beyond _MAX_LAYER_CITS entries.

    Per-spawn unique layer_ids (L22/L38) would otherwise cause unbounded growth
    in long-running adapters.  The FIFO eviction at step 8.5 caps the dict.
    """
    _seed_chain(audit_path, n=3)

    # Insert _MAX_LAYER_CITS + 10 unique layer_ids
    for i in range(_MAX_LAYER_CITS + 10):
        gate(audit_path, f"L22.spawn_test.{i:04x}")
        write_event(audit_path, "test.spacer", details={"i": i})

    with _cit_lock:
        size = len(_layer_cits)

    assert size <= _MAX_LAYER_CITS, (
        f"_layer_cits exceeded bound: {size} > {_MAX_LAYER_CITS}"
    )


# ── Test 14: clear_shadow_hashes also clears _layer_cits ─────────────────────

def test_clear_shadow_hashes_flushes_cit_cache(audit_path: "Path") -> None:
    """clear_shadow_hashes() must reset _layer_cits so license re-activation
    does not trigger false-positive cit_tampered on the next gate() call."""
    from security_events import set_chain_dna_seed  # type: ignore[import]

    paid_seed = derive_seed_paid("test.jwt", "inst-clear-test")
    set_chain_dna_seed(paid_seed)
    try:
        _seed_chain(audit_path, n=2)
        # Populate _layer_cits with a paid-tier CIT
        gate(audit_path, "L16.license_load", dna_seed=paid_seed)

        with _cit_lock:
            assert "L16.license_load" in _layer_cits

        # Simulate license re-activation: clear state then switch seed
        clear_shadow_hashes()

        with _cit_lock:
            assert "L16.license_load" not in _layer_cits, (
                "clear_shadow_hashes() must evict CIT cache to prevent "
                "false-positive cit_tampered after license re-activation"
            )

        # Next gate() on the same layer must succeed (no stale CIT to verify)
        free_seed = derive_seed_free()
        set_chain_dna_seed(free_seed)
        cit = gate(audit_path, "L16.license_load")
        assert cit.is_fresh()
    finally:
        set_chain_dna_seed(derive_seed_free())


# ── Test 15: move_to_end() keeps static layer CIT alive under spawn flood ─────

def test_static_layer_cit_survives_spawn_flood(audit_path: "Path") -> None:
    """A static layer (L16) must retain its CIT even when _MAX_LAYER_CITS unique
    spawn entries flood the dict.

    The key invariant: after gate("L16") the CIT must be at the TAIL of the
    OrderedDict so that FIFO eviction targets the oldest spawn entry, not L16.
    Without move_to_end(), re-assigning an existing key leaves it at its
    original insertion position and the next new-entry eviction removes it.

    Test structure:
      1. Fill the dict with _MAX_LAYER_CITS-1 spawn entries (unique layer_ids)
      2. Call gate("L16") — inserts L16 at tail (position _MAX_LAYER_CITS-1)
      3. Push one more unique spawn → len=_MAX_LAYER_CITS+1 → evict position 0
      4. L16 (at tail) must survive the eviction.
    """
    _seed_chain(audit_path, n=3)

    # Fill dict with _MAX_LAYER_CITS-1 unique spawn IDs
    for i in range(_MAX_LAYER_CITS - 1):
        gate(audit_path, f"L22.spawn_pre.{i:04x}")
        write_event(audit_path, "test.spacer", details={"i": i})

    # Static layer gate — enters at tail (newest position in a full dict)
    gate(audit_path, "L16.static_test")
    write_event(audit_path, "test.spacer", details={})

    # One more unique spawn triggers eviction of the true oldest (spawn_pre.0000)
    gate(audit_path, "L22.spawn_post.ffff")

    # Static layer must still be in the cache (tail position, not evicted)
    with _cit_lock:
        assert "L16.static_test" in _layer_cits, (
            "Static layer CIT must survive spawn flood — move_to_end() required "
            "after every gate() write to _layer_cits"
        )
    # The evicted entry must be the oldest spawn, not L16
    with _cit_lock:
        assert "L22.spawn_pre.0000" not in _layer_cits, (
            "Oldest spawn entry must have been evicted, not the static layer"
        )


# ── Test 16: non-JSON lines in tail window are reported as failures ───────────

def test_verify_last_k_non_json_line_reported(audit_path: "Path") -> None:
    """verify_last_k() must report non-JSON lines, not silently skip them.

    A silent skip enables a deletion+re-chain attack: an adversary removes
    event B, injects a non-JSON line in its place, and rewrites event C's hash
    to chain directly from A.  verify_last_k() would then see A→C as valid
    and report no failures.  The fix counts non-JSON lines and surfaces them.
    """
    _seed_chain(audit_path, n=5)

    # Inject a non-JSON line into the middle of the chain
    lines = audit_path.read_text().splitlines()
    lines.insert(len(lines) // 2, "this-is-not-json")
    audit_path.write_text("\n".join(lines) + "\n")

    failures = verify_last_k(audit_path, k=10)
    assert any("non-JSON" in f for f in failures), (
        f"Non-JSON line in tail window must produce a failure; got: {failures}"
    )


# ── Regression: mac-bearing records (ADR-0137 M2) must verify ─────────────────

def test_verify_accepts_mac_bearing_records(audit_path: Path, monkeypatch) -> None:
    """A chain whose records carry an ADR-0137 M2 ``mac`` field must verify.

    Regression for the CLAG fail-closed false-positive: ``write_event`` computes
    ``hash`` over the canonical record WITHOUT ``hash``/``mac`` and adds ``mac``
    afterwards. ``_verify_hash_link`` previously excluded only ``hash``, so every
    mac-bearing record (e.g. ``forge.tool_executed`` written by the forge MCP
    process, which holds the anchor key) failed verification and tripped the
    spawn gate even though the chain was intact.
    """
    import security_events as _se

    # Force an anchor key so write_event attaches a `mac` to every record.
    # CORVIN_AUDIT_ANCHOR_KEY is a PATH to the key file (not a hex value) — point
    # it at a temp key so the sentinel/key land in tmp, not the CWD/repo root.
    key_file = audit_path.parent / "audit_anchor.key"
    key_file.write_bytes(b"\x5a" * 32)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(key_file))
    monkeypatch.setattr(_se, "_ANCHOR_KEY", None, raising=False)
    monkeypatch.setattr(_se, "_ANCHOR_KEY_LOADED", False, raising=False)

    _seed_chain(audit_path, n=6)

    recs = [json.loads(ln) for ln in audit_path.read_text().splitlines()]
    assert all("mac" in r for r in recs), "test setup: every record must carry a mac"

    # The fix: mac-bearing records verify cleanly and the gate allows the spawn.
    assert verify_last_k(audit_path, VERIFY_K_DEFAULT) == []
    cit = gate(audit_path, "L22.engine_spawn.mactest")
    assert isinstance(cit, ChainIntegrityToken)


# ── Regression: instance_sig-bearing records (ADR-0153 M3) must verify ────────

def test_verify_accepts_instance_sig_bearing_records(audit_path: Path) -> None:
    """A chain whose records carry ADR-0153 M3 instance attestation must verify.

    Regression for the L22 engine-spawn fail-closed false-positive observed on a
    live Discord chain: ``write_event`` computes ``hash`` over the canonical
    record WITHOUT the post-hash fields, then appends ``instance_id`` and
    ``instance_sig`` (additive Ed25519 attestation, NOT chain state).
    ``_verify_hash_link`` previously excluded only ``hash``/``mac``, so every
    signed record — most notably ``session.reset`` written by the adapter where
    instance_identity is importable — failed verification and blocked the spawn
    with ``hash_link_broken`` even though the chain was intact.
    """
    import hashlib
    import security_events as _se

    _seed_chain(audit_path, n=3)

    # Append a record shaped exactly like the live writer's output: hash over the
    # canonical body, then instance_id/instance_sig added afterwards.
    lines = audit_path.read_text().splitlines()
    prev = json.loads(lines[-1])["hash"]
    rec = {
        "ts": 1782050865.46,
        "event_type": "session.reset",
        "severity": "INFO",
        "run_id": "",
        "tool": "",
        "details": {"channel": "discord", "chat_key": "42", "reason": "manual"},
        "prev_hash": prev,
    }
    canon = _se._canonical(rec).encode("utf-8")
    h = hashlib.sha256()
    h.update(prev.encode("utf-8"))
    h.update(b"\n")
    h.update(canon)
    rec["hash"] = h.hexdigest()[:16]
    # Post-hash additive attestation — must NOT affect the hash-link check.
    rec["instance_id"] = "1ac59c0d-b87f-4434-a96e-1e198ccb9e91"
    rec["instance_sig"] = "nuLSl15Qxv4Iak2m6E6e1MEjMkWUXHaSuOHI_SvYmzB9zDLC4X4"
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")

    assert "instance_sig" in json.loads(audit_path.read_text().splitlines()[-1])
    # The fix: the signed session.reset verifies and the spawn gate succeeds.
    assert verify_last_k(audit_path, VERIFY_K_DEFAULT) == []
    cit = gate(audit_path, "L22.engine_spawn.sigtest")
    assert isinstance(cit, ChainIntegrityToken)


# ── Regression: a hashless meta-event must not break the following link ───────

def test_verify_skips_hashless_meta_event(audit_path: Path) -> None:
    """A hashless event (e.g. CRITICAL ``audit.chain_gap_detected``) between two
    chained records must NOT false-positive a hash-link break on the next record.

    Regression for the second L22 fail-closed false-positive observed live: the
    self-test emits ``audit.chain_gap_detected`` WITHOUT a ``hash`` (so it cannot
    recurse into the chain it reports on). The writer chains the NEXT record's
    ``prev_hash`` past the hashless event to the previous hash-bearing tail.
    ``verify_last_k`` previously anchored on ``events[i-1].hash`` — which is ""
    for the hashless event — and reported a spurious ``hash_link_broken`` on the
    following record, blocking the spawn even though the chain is intact.
    """
    _seed_chain(audit_path, n=3)

    # Insert a valid-JSON event with NO hash field (mirrors the live writer's
    # hashless audit.chain_gap_detected), then continue the chain past it.
    lines = audit_path.read_text().splitlines()
    tail_before_gap = json.loads(lines[-1])["hash"]
    with audit_path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({
            "ts": 1782051456.4,
            "event_type": "audit.chain_gap_detected",
            "severity": "CRITICAL",
            "details": {"problem_count": 1},
        }) + "\n")
    # The next real event chains to the PRE-gap tail, not to the hashless event.
    write_event(audit_path, "test.after_gap", details={"seq": 99})
    after = json.loads(audit_path.read_text().splitlines()[-1])
    assert after["prev_hash"] == tail_before_gap, "writer must chain past the hashless event"

    # The fix: the hashless event is skipped and the following link verifies.
    assert verify_last_k(audit_path, VERIFY_K_DEFAULT) == []
    cit = gate(audit_path, "L22.engine_spawn.gaptest")
    assert isinstance(cit, ChainIntegrityToken)


# ── Anchor staleness (crash-loop) vs. real tampering ──────────────────────────

def test_stale_anchor_benign_growth_is_ok_not_critical(audit_path: Path, tmp_path: Path) -> None:
    """Crash-loop: the chain GROWS past a stale anchor. The anchored tail is a
    proven ancestor → verify returns 'ok' (audit.chain_anchor_stale), NOT a
    false CRITICAL tail_mismatch."""
    seed = derive_seed_free()
    anchor_path = tmp_path / "chain_anchor.json"
    _seed_chain(audit_path, n=3)
    write_chain_anchor(audit_path, anchor_path, dna_seed=seed)
    # More events arrive after the (now stale) anchor — pure append growth.
    _seed_chain(audit_path, n=4)
    status, detail = verify_chain_anchor(audit_path, anchor_path, dna_seed=seed, emit=False)
    assert status == "ok", detail


def test_forked_history_still_fails_closed(audit_path: Path, tmp_path: Path) -> None:
    """A non-ancestor tail (pre-anchor history rewritten/replaced) must STAY a
    CRITICAL failure even though the event count did not shrink."""
    seed = derive_seed_free()
    anchor_path = tmp_path / "chain_anchor.json"
    _seed_chain(audit_path, n=3)
    write_chain_anchor(audit_path, anchor_path, dna_seed=seed)
    # Replace the whole chain with a DIFFERENT 6-event chain: count 6 >= 3 but
    # the tail at position 3 no longer equals the anchored tail → not an ancestor.
    audit_path.unlink()
    _seed_chain(audit_path, n=6)
    status, _ = verify_chain_anchor(audit_path, anchor_path, dna_seed=seed, emit=False)
    assert status == "failed"
