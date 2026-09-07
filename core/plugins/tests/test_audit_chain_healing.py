"""ADR-0232/0233 boot tripwire — a broken tail is SEALED, never truncated (F-A13).

History: until 2026-09-07 ``audit_chain_intact`` "healed" a break inside the
tail window by deleting the broken records (``_heal_chain_at_line``) — a
boot-time rewrite of a GDPR Art. 30/32 trail. The replacement appends a
chained ``compliance.chain_discontinuity`` seam record after the broken tail
and counts the writer as sound iff that record verifies. These tests pin:

  * nothing is ever deleted;
  * exactly one seam record is written per break (re-boots do not stack);
  * the seam record itself chains (``verify_chain`` accepts it);
  * a whole-chain failure (lost anchor key) still refuses the boot;
  * a current-state anchor problem (tail truncation) still refuses the boot.
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
        se.write_event(path, "test.event", details={"count": i})


def _corrupt_tail(path: Path, n_records: int) -> None:
    """Flip one character in the MAC/hash of the last `n_records` lines."""
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
def _isolated(tmp_path, monkeypatch):
    # Anchor key + markers in the tmp dir: nothing lands beside the operator's key.
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    tripwire._verify_cache.clear()
    yield
    tripwire._verify_cache.clear()


def _point_tripwire_at(monkeypatch, chain_path: Path, tail_records: int):
    audit_mod = tripwire._audit_module()
    assert audit_mod is not None
    monkeypatch.setattr(audit_mod, "audit_path", lambda: chain_path)
    monkeypatch.setattr(tripwire, "_audit_module", lambda: audit_mod)
    monkeypatch.setattr(tripwire, "TAIL_RECORDS", tail_records)
    return audit_mod


def test_tail_break_is_sealed_not_truncated(tmp_path, monkeypatch):
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 20)
    _corrupt_tail(chain_path, 3)
    before = chain_path.read_text(encoding="utf-8")
    _point_tripwire_at(monkeypatch, chain_path, 3)

    result = tripwire.audit_chain_intact()

    assert result.ok, result.detail
    assert "sealed" in result.detail and "nothing truncated" in result.detail
    after = chain_path.read_text(encoding="utf-8")
    assert after.startswith(before), "no record may be removed or rewritten"
    lines = after.splitlines()
    assert len(lines) == 21
    seam = json.loads(lines[-1])
    assert seam["event_type"] == tripwire.SEAM_EVENT
    assert seam["details"]["seam"] is True
    assert seam["details"]["broken_records"] == 3
    assert (seam["details"]["first_break_line"], seam["details"]["last_break_line"]) == (18, 20)
    assert seam["details"]["total_records"] == 20
    assert "hash" in seam and seam["prev_hash"]  # chained, not an unchained marker
    # The seam record verifies; only the three historical breaks remain.
    ok, problems = se.verify_chain(chain_path)
    assert not ok
    assert sorted(int(p["line"]) for p in problems) == [18, 19, 20]


def test_reboot_does_not_stack_seam_records(tmp_path, monkeypatch):
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 20)
    _corrupt_tail(chain_path, 2)
    _point_tripwire_at(monkeypatch, chain_path, 5)

    assert tripwire.audit_chain_intact().ok
    n1 = len(chain_path.read_text().splitlines())
    tripwire._verify_cache.clear()
    r2 = tripwire.audit_chain_intact()
    assert r2.ok, r2.detail
    assert len(chain_path.read_text().splitlines()) == n1, "second boot must not add a second seam"


def test_whole_chain_failure_refuses_boot_and_deletes_nothing(tmp_path, monkeypatch):
    """A lost/rotated anchor key makes the ENTIRE chain verify as mac_tampered.
    Boot must refuse so the operator restores the KEY; nothing is touched."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 300)
    _corrupt_tail(chain_path, 300)
    before = chain_path.read_text(encoding="utf-8")
    _point_tripwire_at(monkeypatch, chain_path, 200)

    result = tripwire.audit_chain_intact()
    assert not result.ok
    assert "audit_anchor.key" in result.detail
    assert chain_path.read_text(encoding="utf-8") == before


def test_unsound_writer_is_detected_through_the_seam(tmp_path, monkeypatch):
    """If the seam record itself cannot chain (the writer is broken NOW), boot refuses."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 20)
    _corrupt_tail(chain_path, 1)
    audit_mod = _point_tripwire_at(monkeypatch, chain_path, 5)

    def _broken_writer(event_type, **kw):
        # A writer that appends an UNCHAINED line is not sound.
        with chain_path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": 0, "event_type": event_type, "details": kw.get("details", {})}) + "\n")

    monkeypatch.setattr(audit_mod, "audit_event", _broken_writer)
    result = tripwire.audit_chain_intact()
    assert not result.ok
    assert "not sound" in result.detail


def test_tail_truncation_is_a_current_state_failure(tmp_path, monkeypatch):
    """F-A12: deleting the last records leaves a self-consistent chain; the
    out-of-tree tail anchor exposes it and the boot refuses (no seam)."""
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 30)
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    chain_path.write_text("\n".join(lines[:-5]) + "\n", encoding="utf-8")
    _point_tripwire_at(monkeypatch, chain_path, 200)

    ok, problems = se.verify_chain(chain_path)
    assert not ok and any(p.get("issue") == "tail_truncated" for p in problems)
    result = tripwire.audit_chain_intact()
    assert not result.ok
    assert "tail_truncated" in result.detail
    assert len(chain_path.read_text().splitlines()) == 25, "no seam on a current-state failure"


def test_historical_break_outside_window_passes_without_a_seam(tmp_path, monkeypatch):
    chain_path = tmp_path / "audit.jsonl"
    _write_valid_chain(chain_path, 30)
    lines = chain_path.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[4])
    rec["hash"] = ("0" if rec["hash"][0] != "0" else "1") + rec["hash"][1:]
    lines[4] = json.dumps(rec)
    chain_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    _point_tripwire_at(monkeypatch, chain_path, 10)

    result = tripwire.audit_chain_intact()
    assert result.ok and "historical" in result.detail
    assert len(chain_path.read_text().splitlines()) == 30


def test_truncating_healer_is_gone():
    assert not hasattr(tripwire, "_heal_chain_at_line")
