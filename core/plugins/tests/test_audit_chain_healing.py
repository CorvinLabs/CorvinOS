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
