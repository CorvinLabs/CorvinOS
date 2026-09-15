"""Tests for chain_dna.py — LSAD (ADR-0132) DNA verification.

Covers:
  1. Backward-compat: legacy 16-char DNA events still verify correctly.
  2. Mixed chain (16→32 char): transition verifies cleanly.
  3. Free-tier seam accepted when verifying without a paid seed.
  4. Paid-tier seam without paid seed → DnaMismatch (unverifiable).
  5. Pure paid chain from genesis verifies cleanly with paid seed.
  6. Seam resets DNA tracking; post-seam events verify against new DNA.
  7. Tampered DNA mid-chain is detected.
  8. DNA_PREFIX_LEN is 32 (128-bit entropy).
"""
from __future__ import annotations

import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from chain_dna import (  # type: ignore[import]
    DNA_PREFIX_LEN,
    DnaMismatch,
    derive_seed_free,
    derive_seed_paid,
    evolve,
    is_free_tier,
    verify_chain_dna,
)


# ── helpers ───────────────────────────────────────────────────────────────────

def _make_event(event_type: str, dna: str, prev_hash: str) -> dict:
    """Build a minimal chain event with computed SHA-256 hash."""
    import hashlib
    import json
    rec: dict = {"event_type": event_type, "details": {"chain_dna": dna}}
    h = hashlib.sha256()
    h.update(prev_hash.encode())
    h.update(b"\n")
    h.update(json.dumps(rec, sort_keys=True, separators=(",", ":")).encode())
    rec["hash"] = h.hexdigest()[:16]
    return rec


def _build_chain(seed: str, n: int, *, dna_len: int = DNA_PREFIX_LEN) -> tuple[list[dict], str]:
    """Build n valid events starting from seed.

    Returns (events, last_hash).  The seed is stored as the first DNA value
    (genesis); subsequent events evolve from there.
    """
    events = []
    prev_hash = "00000000"
    current_dna = seed[:dna_len]
    for i in range(n):
        ev = _make_event(f"test.evt.{i}", current_dna, prev_hash)
        events.append(ev)
        prev_hash = ev["hash"]
        current_dna = evolve(current_dna, prev_hash)[:dna_len]
    return events, prev_hash


# ── Test 1: legacy 16-char DNA backward-compat ───────────────────────────────

def test_verify_chain_dna_legacy_16char_events() -> None:
    """Events with 16-char DNA (pre-upgrade) still verify cleanly."""
    free_seed = derive_seed_free()
    events, _ = _build_chain(free_seed, 5, dna_len=16)

    mismatches = verify_chain_dna(events)
    assert mismatches == [], f"Legacy chain should verify: {mismatches}"


# ── Test 2: mixed chain (16-char then 32-char) backward-compat ───────────────

def test_verify_chain_dna_mixed_legacy_and_new() -> None:
    """Chain transitioning from 16-char to 32-char DNA verifies cleanly.

    Key: the 32-char event's DNA is evolved from the 16-char stored value
    in the previous event (not re-evolved from a further truncated value).
    """
    free_seed = derive_seed_free()
    events = []
    prev_hash = "00000000"
    dna = free_seed[:16]

    # Two legacy events storing 16-char DNA
    for i in range(2):
        ev = _make_event(f"legacy.{i}", dna, prev_hash)
        events.append(ev)
        prev_hash = ev["hash"]
        dna = evolve(dna, prev_hash)[:16]
    # After the loop: `dna` = DNA[2] as 16-char, `prev_hash` = hash[1]

    # Transition to 32-char.  Compute DNA[2] as 32 chars from the STORED
    # DNA[1] (events[-1].chain_dna) and hash[1] (events[-1].hash).
    dna_1 = events[-1]["details"]["chain_dna"]   # DNA[1], 16-char stored value
    hash_1 = events[-1]["hash"]                   # hash of ev1
    dna_32 = evolve(dna_1, hash_1)[:32]           # DNA[2] in 32-char format
    prev_hash = hash_1                             # still hash[1]

    # Two new-format events storing 32-char DNA
    for i in range(2):
        ev = _make_event(f"new.{i}", dna_32, prev_hash)
        events.append(ev)
        prev_hash = ev["hash"]
        dna_32 = evolve(dna_32, prev_hash)[:32]

    mismatches = verify_chain_dna(events)
    assert mismatches == [], f"Mixed chain should verify: {mismatches}"


# ── Test 3: free-tier seam accepted when seed=None ────────────────────────────

def test_verify_chain_dna_free_tier_seam_accepted() -> None:
    """A license.chain_dna_seeded event with free-tier DNA is accepted (seed=None)."""
    free_seed = derive_seed_free()
    events, prev_hash = _build_chain(free_seed, 2)

    seam_dna = free_seed[:DNA_PREFIX_LEN]
    seam_ev = _make_event("license.chain_dna_seeded", seam_dna, prev_hash)
    events.append(seam_ev)
    prev_hash = seam_ev["hash"]

    # Post-seam evolution
    dna = evolve(seam_dna, prev_hash)[:DNA_PREFIX_LEN]
    for i in range(2):
        ev = _make_event(f"post_seam.{i}", dna, prev_hash)
        events.append(ev)
        prev_hash = ev["hash"]
        dna = evolve(dna, prev_hash)[:DNA_PREFIX_LEN]

    mismatches = verify_chain_dna(events, seed=None)
    assert mismatches == [], f"Free-tier seam chain should be clean: {mismatches}"


# ── Test 4: paid seam without paid seed → flagged ─────────────────────────────

def test_verify_chain_dna_paid_seam_without_seed_flagged() -> None:
    """Paid-tier seam claim without paid seed → exactly one DnaMismatch."""
    free_seed = derive_seed_free()
    paid_seed = derive_seed_paid("fake.jwt", "inst-1")
    events, prev_hash = _build_chain(free_seed, 2)

    seam_dna = paid_seed[:DNA_PREFIX_LEN]  # claims paid tier
    seam_ev = _make_event("license.chain_dna_seeded", seam_dna, prev_hash)
    events.append(seam_ev)

    mismatches = verify_chain_dna(events, seed=None)
    assert len(mismatches) == 1, f"Expected 1 mismatch for unverifiable paid seam, got: {mismatches}"
    assert mismatches[0].event_type == "license.chain_dna_seeded"


# ── Test 5: seam verified with paid seed → accepted ───────────────────────────

def test_verify_chain_dna_paid_seam_verified_with_matching_seed() -> None:
    """A chain with a paid-tier seam verifies the seam when seed=paid_seed.

    CorvinLabs audit: pass seed=paid_seed to validate that the seam event
    carries the expected paid-tier DNA prefix.  Pre-seam free-tier events
    will fail (they're not paid-tier), but the seam itself must NOT produce
    a DnaMismatch.
    """
    free_seed = derive_seed_free()
    paid_seed = derive_seed_paid("audited.jwt.token", "inst-corvinlabs-audit")
    events, prev_hash = _build_chain(free_seed, 2)

    # Seam with paid-tier DNA
    seam_dna = paid_seed[:DNA_PREFIX_LEN]
    seam_ev = _make_event("license.chain_dna_seeded", seam_dna, prev_hash)
    events.append(seam_ev)

    mismatches = verify_chain_dna(events, seed=paid_seed)
    seam_mismatches = [m for m in mismatches if m.event_type == "license.chain_dna_seeded"]
    # The seam must NOT be flagged — it carries the correct paid-tier prefix
    assert seam_mismatches == [], (
        f"Seam event with matching paid seed must be accepted: {seam_mismatches}"
    )


# ── Test 6: seam resets tracking; post-seam events verify ─────────────────────

def test_verify_chain_dna_seam_resets_tracking() -> None:
    """After a seam event, post-seam DNA evolution is tracked from the new seed.

    With seed=None: pre-seam (free) events are clean, paid seam is flagged,
    but post-seam events evolve from the seam DNA (verified clean).
    We confirm that NO post-seam mismatch is reported.
    """
    free_seed = derive_seed_free()
    paid_seed = derive_seed_paid("upgrade.jwt", "inst-upgrade")
    events, prev_hash = _build_chain(free_seed, 2)

    seam_dna = paid_seed[:DNA_PREFIX_LEN]
    seam_ev = _make_event("license.chain_dna_seeded", seam_dna, prev_hash)
    events.append(seam_ev)
    prev_hash = seam_ev["hash"]

    # Post-seam events evolve from the paid seed
    dna = evolve(seam_dna, prev_hash)[:DNA_PREFIX_LEN]
    for i in range(3):
        ev = _make_event(f"paid.{i}", dna, prev_hash)
        events.append(ev)
        prev_hash = ev["hash"]
        dna = evolve(dna, prev_hash)[:DNA_PREFIX_LEN]

    mismatches = verify_chain_dna(events, seed=None)
    # Only the seam event should be flagged (unverifiable paid claim without seed)
    seam_mismatches = [m for m in mismatches if m.event_type == "license.chain_dna_seeded"]
    post_seam_mismatches = [m for m in mismatches if "paid." in m.event_type]
    assert len(seam_mismatches) == 1, "Exactly one seam mismatch expected"
    assert post_seam_mismatches == [], (
        f"Post-seam events should verify cleanly after seam reset: {post_seam_mismatches}"
    )


# ── Test 7: tampered DNA is detected ─────────────────────────────────────────

def test_verify_chain_dna_tampered_event_detected() -> None:
    """Corrupting a DNA value mid-chain produces a DnaMismatch."""
    free_seed = derive_seed_free()
    events, _ = _build_chain(free_seed, 5)

    # Corrupt event 3's DNA
    events[3]["details"]["chain_dna"] = "d" * DNA_PREFIX_LEN

    mismatches = verify_chain_dna(events)
    assert len(mismatches) >= 1
    assert any(m.event_type == "test.evt.3" for m in mismatches)


# ── Test 8: DNA_PREFIX_LEN is 32 ─────────────────────────────────────────────

def test_dna_prefix_len_is_128_bit() -> None:
    """DNA_PREFIX_LEN must be 32 hex chars (128-bit entropy) after security upgrade."""
    assert DNA_PREFIX_LEN == 32, (
        f"DNA_PREFIX_LEN should be 32 (128-bit); got {DNA_PREFIX_LEN}"
    )


# ── Test 9: is_free_tier() handles legacy 16-char chain prefixes ──────────────

def test_is_free_tier_accepts_legacy_16char_prefix() -> None:
    """is_free_tier() must return True for a 16-char legacy chain prefix.

    Before the DNA_PREFIX_LEN=32 upgrade, chain events stored 16-char DNA.
    A naive comparison seed[:32] == derive_seed_free()[:32] would return False
    for a 16-char input because Python string equality on different-length
    strings is always False.  The length-aware comparison fixes this.
    """
    free_seed = derive_seed_free()
    legacy_prefix = free_seed[:16]  # simulates a chain-stored prefix pre-upgrade
    assert is_free_tier(legacy_prefix), (
        "is_free_tier() must recognize a 16-char legacy free-tier prefix"
    )


def test_is_free_tier_rejects_paid_seed() -> None:
    """is_free_tier() must return False for a paid-tier seed."""
    paid_seed = derive_seed_paid("audit.jwt.token", "inst-test")
    assert not is_free_tier(paid_seed)
    assert not is_free_tier(paid_seed[:16])  # also works for legacy paid prefix
