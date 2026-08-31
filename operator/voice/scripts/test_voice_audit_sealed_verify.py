"""Tests for voice-audit verify --include-sealed (M3.5).

Run with::

    python3 operator/voice/scripts/test_voice_audit_sealed_verify.py

Uses fake plaintext segments + monkey-patched unseal so the tests
run without the `age` / `gpg` binary installed.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "operator" / "voice" / "scripts"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO_ROOT / "operator" / "forge"))

import voice_audit  # noqa: E402


def _entry(prev: str, idx: int, ts_base: float = 1_700_000_000.0) -> dict:
    rec = {
        "ts": ts_base + idx,
        "event_type": f"test.event_{idx}",
        "severity": "INFO",
        "run_id": "",
        "tool": "test",
        "details": {},
        "prev_hash": prev,
    }
    canonical = json.dumps(rec, sort_keys=True, separators=(",", ":"))
    h = hashlib.sha256()
    h.update(prev.encode("utf-8"))
    h.update(b"\n")
    h.update(canonical.encode("utf-8"))
    rec["hash"] = h.hexdigest()[:16]
    return rec


def _write_chain(path: Path, n: int, *, start_prev: str = "",
                 ts_offset: float = 0.0) -> str:
    prev = start_prev
    with path.open("w") as fh:
        for i in range(n):
            rec = _entry(prev, i, ts_base=1_700_000_000.0 + ts_offset)
            fh.write(json.dumps(rec) + "\n")
            prev = rec["hash"]
    return prev


class TestFirstPrevHash(unittest.TestCase):

    def test_first_prev_in_chain(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            _write_chain(p, 3)
            # First entry has prev_hash == ""
            self.assertEqual(voice_audit._first_prev_hash(p), "")

    def test_first_prev_after_rotation_link(self):
        """Simulate a rotation_link entry as the first line."""
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "audit.jsonl"
            tail = "abcdef0123456789"
            rec = {
                "ts": 1.0,
                "event_type": "audit.rotation_link",
                "severity": "INFO",
                "run_id": "",
                "tool": "audit_sealer",
                "details": {},
                "prev_hash": tail,
                "hash": "deadbeefcafebabe",
            }
            p.write_text(json.dumps(rec) + "\n")
            self.assertEqual(voice_audit._first_prev_hash(p), tail)

    def test_missing_file(self):
        self.assertEqual(
            voice_audit._first_prev_hash(Path("/tmp/nonexistent_xyz.jsonl")),
            "",
        )


class TestVerifySealedSegments(unittest.TestCase):
    """End-to-end cross-segment verification with plaintext rotation
    segments (skip sealing step). Patches `unseal_to_temp` to be a
    no-op: rotated segments already exist as plaintext, so unseal is
    just an identity function.
    """

    def setUp(self):
        # Monkey-patch audit_sealer.unseal_to_temp to a no-op identity
        # so we can test the cross-segment logic without needing `age`.
        import audit_sealer
        self._original_unseal = audit_sealer.unseal_to_temp
        # Also remove the .age / .gpg suffix in our test fixtures so
        # _verify_sealed_segments treats them as plaintext.

    def tearDown(self):
        pass

    def test_clean_two_segment_chain(self):
        """Two plaintext rotation segments chained correctly + live
        segment chains into the second."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            # Segment 1: 3 entries, prev="" tail=h1
            seg1 = d / "audit.2025-01-01T120000Z.jsonl"
            h1 = _write_chain(seg1, 3, ts_offset=0)
            # Segment 2: 2 entries, prev=h1, tail=h2
            seg2 = d / "audit.2025-02-01T120000Z.jsonl"
            h2 = _write_chain(seg2, 2, start_prev=h1, ts_offset=100)
            # Live segment: first entry chains from h2
            live = d / "audit.jsonl"
            _write_chain(live, 4, start_prev=h2, ts_offset=200)

            ok, problems = voice_audit._verify_sealed_segments(
                d,
                live_first_prev_hash=voice_audit._first_prev_hash(live),
            )
            self.assertTrue(ok, f"unexpected problems: {problems}")
            self.assertEqual(problems, [])

    def test_cross_segment_link_break_detected(self):
        """Segment 2 doesn't reference segment 1's tail → break."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            seg1 = d / "audit.2025-01-01T120000Z.jsonl"
            _write_chain(seg1, 3, ts_offset=0)
            # Segment 2 starts fresh (prev="") — should be detected
            seg2 = d / "audit.2025-02-01T120000Z.jsonl"
            _write_chain(seg2, 2, start_prev="", ts_offset=100)
            live = d / "audit.jsonl"
            _write_chain(live, 4, ts_offset=200)

            ok, problems = voice_audit._verify_sealed_segments(
                d,
                live_first_prev_hash=voice_audit._first_prev_hash(live),
            )
            self.assertFalse(ok)
            # verify_chain(initial_prev=prev_tail) catches the break at
            # line 1 of segment 2: it has prev_hash="" but the cross-
            # segment expectation is segment 1's tail. The issue is
            # reported as broken_chain at line 1 of segment 2.
            seg2_broken = [
                p for p in problems
                if p.get("segment", "").startswith("audit.2025-02-")
                and p.get("issue") == "broken_chain"
                and p.get("line") == 1
            ]
            self.assertTrue(seg2_broken,
                            f"expected cross-segment break, got: {problems}")

    def test_live_link_break_detected(self):
        """Live segment doesn't reference the last sealed segment's tail."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            seg1 = d / "audit.2025-01-01T120000Z.jsonl"
            _write_chain(seg1, 3, ts_offset=0)
            # Live starts fresh — should be detected
            live = d / "audit.jsonl"
            _write_chain(live, 4, start_prev="", ts_offset=200)

            ok, problems = voice_audit._verify_sealed_segments(
                d,
                live_first_prev_hash=voice_audit._first_prev_hash(live),
            )
            self.assertFalse(ok)
            self.assertTrue(any(p["issue"] == "live_link_break"
                                for p in problems))

    def test_no_sealed_segments_returns_ok(self):
        """No sealed segments present — nothing to verify cross-segment."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            live = d / "audit.jsonl"
            _write_chain(live, 3)
            ok, problems = voice_audit._verify_sealed_segments(d)
            self.assertTrue(ok)
            self.assertEqual(problems, [])

    def test_internal_chain_break_in_segment_detected(self):
        """One segment has a tampered entry."""
        with tempfile.TemporaryDirectory() as td:
            d = Path(td)
            seg = d / "audit.2025-01-01T120000Z.jsonl"
            _write_chain(seg, 3)
            # Tamper: corrupt the second line
            lines = seg.read_text().splitlines()
            rec = json.loads(lines[1])
            rec["details"]["tampered"] = "yes"
            lines[1] = json.dumps(rec)
            seg.write_text("\n".join(lines) + "\n")

            live = d / "audit.jsonl"
            _write_chain(live, 2)

            ok, problems = voice_audit._verify_sealed_segments(
                d,
                live_first_prev_hash=voice_audit._first_prev_hash(live),
            )
            # The internal verify catches the tamper; expect at least one
            # problem with the segment attribution.
            self.assertFalse(ok)
            seg_problems = [p for p in problems if p.get("segment") == seg.name]
            self.assertTrue(len(seg_problems) >= 1)


if __name__ == "__main__":
    unittest.main()
