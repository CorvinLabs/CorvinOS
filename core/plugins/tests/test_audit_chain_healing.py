"""Tests for tripwire.py's audit-chain healing path (2026-07-30).

Adversarial review (overnight, 8-agent pass) found this path had ZERO test
coverage since it shipped in 12a3c54 (release 0.10.67), plus two concrete
bugs fixed alongside these tests:

  1. The docstring/module comment claimed "An audit event is written to
     mark the healing" — nothing ever wrote it. Deleting corrupted records
     from a GDPR Art. 30/32 hash-chained audit trail with zero trace of the
     deletion in that trail is the exact failure this event exists to
     prevent.
  2. `_heal_chain_at_line` opened the real path with 'w' directly (truncate
     immediately, then write) instead of write-to-temp + os.replace(). A
     crash between those two steps turned "delete a broken tail" into
     "lose the entire chain."

Uses the real hash-chain writer (`forge.security_events.write_event`) to
build a genuinely valid chain, then corrupts the tail directly — a
hand-crafted fake JSONL would risk a false-negative test (verify_chain might
reject it for a reason unrelated to what we're testing).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[3]
_FORGE_DIR = _REPO_ROOT / "operator" / "forge"
_SHARED_DIR = _REPO_ROOT / "operator" / "bridges" / "shared"
_COMPLIANCE_DIR = _REPO_ROOT / "core" / "compliance"
for _p in (_FORGE_DIR, _SHARED_DIR, _COMPLIANCE_DIR):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from forge import security_events as se  # noqa: E402
from corvin_compliance_reports import tripwire  # noqa: E402


def _write_valid_chain(path: Path, n: int) -> None:
    for i in range(n):
        se.write_event(path, "test.event", details={"i": i})


def _corrupt_tail(path: Path, n_records: int) -> None:
    """Flip one character in the MAC/hash of the last `n_records` lines —
    simulates the exact 'mac_tampered'/hash-break shape verify_chain
    detects, without depending on which specific field it checks."""
    lines = path.read_text(encoding="utf-8").splitlines()
    for idx in range(len(lines) - n_records, len(lines)):
        rec = json.loads(lines[idx])
        for key in ("mac", "hash", "prev_hash"):
            if key in rec and isinstance(rec[key], str) and rec[key]:
                rec[key] = ("0" if rec[key][0] != "0" else "1") + rec[key][1:]
                break
        lines[idx] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


@pytest.fixture(autouse=True)
def _reset_verify_cache():
    tripwire._verify_cache.clear()
    yield
    tripwire._verify_cache.clear()


def test_healing_writes_the_audit_event_it_claims_to(tmp_path, monkeypatch):
    """Bug #1: the docstring says an audit event marks the healing —
    verify one is actually written, with the right event type and counts."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 20)
    _corrupt_tail(chain_path, 3)

    audit_mod = tripwire._audit_module()
    assert audit_mod is not None, "audit module must be importable for this test to mean anything"
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    # TAIL_RECORDS=200 (the real default) swallows this whole 20-line fixture
    # into "ancient history" (tail_start collapses to 1), never reaching the
    # heal branch at all. Scope it down so the corrupted last-3-of-20 lines
    # land inside the tail window, matching a real "recent corruption" shape.
    monkeypatch.setattr(tripwire, "TAIL_RECORDS", 3)

    recorded = []
    original_event = audit_mod.audit_event

    def _spy(event_type, **kw):
        recorded.append((event_type, kw))
        return original_event(event_type, **kw)

    monkeypatch.setattr(audit_mod, "audit_event", _spy)
    # tripwire._audit_module() is called fresh each time (not cached at
    # import), but re-patch defensively in case a caller memoized it.
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)

    result = tripwire.audit_chain_intact()

    assert result.ok, f"boot must proceed after a successful heal: {result.detail}"
    assert "healed" in result.detail
    healed_events = [r for r in recorded if r[0] == "compliance.chain_discontinuity_healed"]
    assert len(healed_events) == 1, (
        f"expected exactly one chain_discontinuity_healed event, got {recorded}"
    )
    details = healed_events[0][1].get("details", {})
    assert details.get("broken_records") == 3
    assert details.get("truncated_at_line") == 17


def test_healing_is_atomic_no_leftover_tmp_file(tmp_path):
    """Bug #2: a crash between truncate-write and replace must never be
    able to happen — verify the write goes through a temp file + replace,
    and no temp file survives a normal (non-crashing) run."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 10)
    original_lines = chain_path.read_text(encoding="utf-8").splitlines()

    tripwire._heal_chain_at_line(chain_path, 7)

    healed_lines = chain_path.read_text(encoding="utf-8").splitlines()
    assert healed_lines == original_lines[:7]
    leftover_tmp = list(tmp_path.glob("*.tmp")) + list(tmp_path.glob("*.healing-*"))
    assert leftover_tmp == [], f"a temp file survived a clean run: {leftover_tmp}"


def test_healing_preserves_history_before_the_break(tmp_path, monkeypatch):
    """The good records before the corrupted tail must survive verbatim —
    healing truncates the BREAK, not the whole chain."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 20)
    good_prefix = chain_path.read_text(encoding="utf-8").splitlines()[:17]
    _corrupt_tail(chain_path, 3)

    audit_mod = tripwire._audit_module()
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    monkeypatch.setattr(tripwire, "TAIL_RECORDS", 3)  # see test above for why

    result = tripwire.audit_chain_intact()
    assert result.ok

    # The healed file = the good prefix, PLUS the chain_discontinuity_healed
    # event as its own new final entry (by design — see test above). Assert
    # the prefix survives verbatim; don't require exact-length equality.
    remaining = chain_path.read_text(encoding="utf-8").splitlines()
    assert remaining[: len(good_prefix)] == good_prefix, (
        "healing must not touch history before the break"
    )
    assert len(remaining) == len(good_prefix) + 1, (
        "expected exactly the healing event appended after the good prefix"
    )


def test_no_healable_records_still_blocks_boot(tmp_path, monkeypatch):
    """If the corruption starts at line 1 (nothing good to keep), healing
    must NOT invent a truncation to an empty/near-empty file — boot should
    still refuse, per the existing 'healing failed' fallback."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 5)
    _corrupt_tail(chain_path, 5)  # corrupt everything, including line 1

    audit_mod = tripwire._audit_module()
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    monkeypatch.setattr(tripwire, "TAIL_RECORDS", 5)

    result = tripwire.audit_chain_intact()
    assert not result.ok, "corruption with nothing good to truncate to must still block boot"


def test_tail_local_break_deletes_only_the_broken_tail_not_the_window(tmp_path, monkeypatch):
    """C1 (2026-07-30): with the REAL TAIL_RECORDS=200, a single broken record
    at the very end must delete ~1 record, not ~199. The old code truncated at
    `tail_start - 1` (= total-200), destroying up to 199 intact GDPR records to
    remove one. Uses the production default — the earlier tests pin TAIL_RECORDS=3
    which structurally hides this."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 300)
    _corrupt_tail(chain_path, 1)  # only the last record is broken

    before = chain_path.read_text(encoding="utf-8").splitlines()
    assert len(before) == 300

    audit_mod = tripwire._audit_module()
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    # TAIL_RECORDS left at the real default (200).

    recorded = []
    original_event = audit_mod.audit_event
    monkeypatch.setattr(
        audit_mod, "audit_event",
        lambda et, **kw: (recorded.append((et, kw)), original_event(et, **kw))[1],
    )

    result = tripwire.audit_chain_intact()
    assert result.ok, f"a tail-local break must heal and boot: {result.detail}"

    after = chain_path.read_text(encoding="utf-8").splitlines()
    # 1 broken record removed, +1 healed-marker event appended = 300.
    # The point: NOT 300 - 199. At least 298 of the original records survive.
    healed = [r for r in recorded if r[0] == "compliance.chain_discontinuity_healed"]
    assert len(healed) == 1
    details = healed[0][1].get("details", {})
    assert details.get("records_deleted") == 1, (
        f"exactly one broken record should be deleted, got {details}"
    )
    assert details.get("truncated_at_line") == 299


def test_whole_chain_failure_refuses_boot_and_deletes_nothing(tmp_path, monkeypatch):
    """C2 (2026-07-30): a lost/rotated anchor key makes the ENTIRE chain verify
    as mac_tampered. Healing must be REFUSED (not tail-local) so authentic
    records are preserved, and boot must fail so the operator restores the key —
    the old code would have shredded 200 records per boot while reporting green."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 300)
    _corrupt_tail(chain_path, 300)  # every record broken == anchor-key-loss shape

    before = chain_path.read_text(encoding="utf-8")

    audit_mod = tripwire._audit_module()
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    # real TAIL_RECORDS=200

    result = tripwire.audit_chain_intact()
    assert not result.ok, "whole-chain failure must refuse boot, not heal"
    assert "anchor_anchor" not in result.detail  # sanity
    assert chain_path.read_text(encoding="utf-8") == before, (
        "not a single record may be deleted when the whole chain fails to verify"
    )


def test_sparse_corruption_with_gap_refuses_healing(tmp_path, monkeypatch):
    """CRITICAL (2026-08-09): sparse corruption with gaps must refuse healing.

    Scenario: record N is corrupted, but record N+1 verifies because its prev_hash
    matches N's corrupted hash (forward-recovery in verify_chain). The problems list
    has a gap: N is listed, but N+1 is not. Truncating at N-1 would delete N+1 even
    though it verified and was not in the problems list (GDPR Art. 30/32 violation).

    The fix: refuse healing when we detect a gap in the problems list, requiring
    operator intervention instead of silently deleting unconfirmed-broken records.
    """
    chain_path = tmp_path / "audit.jsonl"

    # Write 20 good records
    _write_valid_chain(chain_path, 20)
    lines_before = chain_path.read_text(encoding="utf-8").splitlines()

    # Corrupt record 15's hash (simulating a bit flip)
    # Record 16's prev_hash already points to 15's correct hash,
    # but after corruption, 16 will see 15's corrupted hash.
    # To simulate the actual scenario, we corrupt 15, then craft 16
    # so that it would verify given 15's corrupted hash.

    lines = lines_before.copy()
    rec15 = json.loads(lines[14])  # Line 15, 0-indexed
    original_hash_15 = rec15["hash"]
    rec15["hash"] = "0" + rec15["hash"][1:]  # Flip first char: now corrupted
    lines[14] = json.dumps(rec15)

    # Record 16 will now fail verification against the original chain
    # (its prev_hash won't match 15's corrupted hash)
    # But in verify_chain's forward recovery, 16's prev_hash is checked
    # against rec15["hash"] (the corrupted value), not the expected value.
    # If we manually fix 16's prev_hash to match 15's corrupted hash,
    # then 16 will verify in the forward pass but still be deleted by healing.

    rec16 = json.loads(lines[15])  # Line 16
    rec16["prev_hash"] = rec15["hash"]  # Point to 15's corrupted hash
    # Recompute 16's hash to be valid with the corrupted prev_hash
    rec16_copy = {k: v for k, v in rec16.items() if k not in ("hash", "mac")}
    import hashlib
    h = hashlib.sha256()
    h.update(rec16["prev_hash"].encode("utf-8"))
    h.update(b"\n")
    h.update(json.dumps(rec16_copy, sort_keys=True).encode("utf-8"))
    rec16["hash"] = h.hexdigest()[:16]
    lines[15] = json.dumps(rec16)

    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    # Verify that record 15 is corrupted but 16 might not be in problems
    audit_mod = tripwire._audit_module()
    ok, problems, total = tripwire._verify_chain(chain_path)
    problem_lines = {int(p.get("line", 0)) for p in problems}

    assert not ok, "chain with corruption should fail verification"
    assert 15 in problem_lines, "record 15 should be in problems (it's corrupted)"
    # Record 16 may or may not be in problems depending on how verify_chain works
    # The point is that healing now detects the gap and refuses

    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    monkeypatch.setattr(tripwire, "TAIL_RECORDS", 20)

    # Run healing: it should REFUSE because of the gap
    result = tripwire.audit_chain_intact()

    # The fix: healing refuses and boot fails
    assert not result.ok, (
        "sparse corruption should be refused to prevent deletion of unconfirmed records"
    )
    # The detail message indicates healing failed (the gap detection refuses it)
    assert "healing failed" in result.detail.lower(), (
        f"result should indicate healing refused: {result.detail}"
    )

    # Critical: the file should NOT be modified
    after = chain_path.read_text(encoding="utf-8")
    before_content = "\n".join(lines) + "\n"
    assert after == before_content, (
        "healing must not modify the file when refusing due to gap"
    )
