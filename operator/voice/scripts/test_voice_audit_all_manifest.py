"""FND-16 regression: `verify --all` (the nightly systemd-timer path) must run
the SAME signed segment-manifest continuity check that plain `verify` runs.

Before the fix, ``cmd_verify_all`` only ran ``verify_audit()`` per live chain and
NEVER called ``_verify_segment_manifest``. A deleted/swapped L37-sealed (rotated)
segment therefore escaped the nightly timer entirely: the live chain resets to a
fresh, internally-valid segment on rotation and verifies clean. The systemd unit
runs ``voice_audit.py verify --all`` (→ ``cmd_verify_all``), so the manifest
check the code comment promised runs "on EVERY verify (incl. the daily timer)"
was in fact skipped on the actual timer path.

These tests drive the real CLI entry point (``main([...])``) so the process exit
code — not just a helper return — is asserted.

NOTE on test construction: the live chain here is a clean genesis chain
(prev_hash="") so ``verify_audit`` passes, and the manifest is a minimal valid
manifest whose recorded segment exists on disk. This isolates the segment
existence / sha256 continuity check (the thing FND-16 is about) from the live
rotation-link check, which requires a rotation_link line-1 record that
``verify_audit`` (initial_prev="") independently flags — a separate pre-existing
property unrelated to this wiring fix.
"""
import hashlib
import json
import tempfile
from pathlib import Path

import pytest


def _load_modules():
    import sys
    scripts = Path(__file__).resolve().parent
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    # Importing voice_audit first runs its module-level sys.path insert for
    # operator/bridges/shared, which makes audit_sealer importable.
    import voice_audit as V
    import audit_sealer as S
    return V, S


def _write_genesis_chain(path: Path, n: int = 2) -> None:
    """Write a clean genesis hash chain (prev_hash starts at "") using the
    canonical algorithm verify_chain expects, so verify_audit() passes."""
    prev = ""
    with path.open("w") as fh:
        for i in range(n):
            rec = {"ts": 1_700_000_000.0 + i, "event_type": f"e{i}",
                   "severity": "INFO", "run_id": "", "tool": "t",
                   "details": {}, "prev_hash": prev}
            canon = json.dumps(rec, sort_keys=True, separators=(",", ":"))
            rec["hash"] = hashlib.sha256(
                (prev + "\n" + canon).encode()).hexdigest()[:16]
            prev = rec["hash"]
            fh.write(json.dumps(rec) + "\n")


@pytest.fixture
def tree():
    """A safe `.corvin` walk root: a clean genesis live chain + one rotated
    (plaintext) sealed segment + a valid segment manifest recording it."""
    V, S = _load_modules()
    root = Path(tempfile.mkdtemp()) / ".corvin"   # name==.corvin → safe walk root
    root.mkdir()

    live = root / "audit.jsonl"
    _write_genesis_chain(live, 2)

    # A rotated/sealed segment (plaintext .jsonl matching the segment name RE).
    seg_name = "audit.2026-07-01T120000Z.jsonl"
    seg = root / seg_name
    seg.write_text('{"ts": 1.0, "event_type": "sealed", "prev_hash": "", '
                   '"hash": "aaaaaaaaaaaaaaaa"}\n')
    seg_sha = S._sha256_file(seg)

    # A minimal valid manifest recording that segment. last_hash="" keeps the
    # live-link check inert (newest_tail falsy) so a genesis live chain is a
    # legitimately-linked baseline — isolating the segment-existence check.
    manifest = S.segment_manifest_path(root)
    manifest.write_text(json.dumps({
        "segment": seg_name, "on_disk": seg_name,
        "sha256": seg_sha, "first_prev_hash": "", "last_hash": "",
    }) + "\n")
    return V, S, root, live, seg


def test_verify_all_intact_exits_zero(tree):
    """Baseline: genesis live chain + on-disk segment + matching manifest → 0."""
    V, S, root, live, seg = tree
    rc = V.main(["--path", str(root), "verify", "--all"])
    assert rc == 0


def test_verify_all_deleted_sealed_segment_is_nonzero(tree, capsys):
    """FND-16: delete a recorded sealed segment. The live chain still verifies
    clean, so verify_audit() alone would pass — but the manifest continuity
    check now runs on the --all path and must force a NON-ZERO exit and report
    the failure. `sealed_segment_missing` can ONLY be produced by
    _verify_segment_manifest, which pre-fix cmd_verify_all never invoked."""
    V, S, root, live, seg = tree
    assert V.main(["--path", str(root), "verify", "--all"]) == 0   # sanity
    capsys.readouterr()

    seg.unlink()   # delete a sealed segment the manifest records

    rc = V.main(["--path", str(root), "verify", "--all"])
    assert rc != 0, "deleted sealed segment must break `verify --all`"
    err = capsys.readouterr().err
    assert "BROKEN" in err
    assert "sealed_segment_missing" in err


def test_verify_all_swapped_sealed_segment_is_nonzero(tree, capsys):
    """Swap (tamper) the sealed segment's bytes: sha256 no longer matches the
    manifest entry → sealed_segment_tampered → non-zero exit on the --all path."""
    V, S, root, live, seg = tree
    assert V.main(["--path", str(root), "verify", "--all"]) == 0   # sanity
    capsys.readouterr()

    with seg.open("a") as fh:
        fh.write('{"ts": 9.0, "event_type": "forged"}\n')

    rc = V.main(["--path", str(root), "verify", "--all"])
    assert rc != 0, "tampered sealed segment must break `verify --all`"
    err = capsys.readouterr().err
    assert "sealed_segment_tampered" in err


def test_manifest_check_helper_discriminates(tree):
    """Direct helper-level check that the wired-in continuity check passes on an
    intact manifest and fails on a deleted segment — the discrimination the
    --all aggregation now depends on."""
    V, S, root, live, seg = tree
    ok, problems, _ = V._verify_segment_manifest(root, V._first_prev_hash(live))
    assert ok, "intact manifest must verify"
    seg.unlink()
    ok2, problems2, _ = V._verify_segment_manifest(root, V._first_prev_hash(live))
    assert not ok2 and any(p["issue"] == "sealed_segment_missing" for p in problems2)
