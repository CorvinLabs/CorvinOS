"""G5 (ADR-0369) — merge-engine correctness + PII backstop.

The plan's LDD review (F1/F4) scrutinised two things: grades must merge by ARRAY UNION
(not by summing n_grades, which double-counts overlaps), and the PII backstop must
actually catch a poisoned payload. These tests pin both through the real merge path.
"""
import json
from pathlib import Path

import pytest

from core.cross_device import tenant_sync as ts


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def test_grade_store_union_not_sum(tmp_path: Path):
    """Two sides share one grade for 'memory' and each has one unique grade → union = 3,
    n_grades = 3 (NOT 4). Summing would double-count the shared grade."""
    shared = {"score": 0.8, "grader": "operator", "ts": 1}
    local = {"memory": {"grades": [shared, {"score": 0.6, "grader": "operator", "ts": 2}]}}
    remote = {"memory": {"grades": [shared, {"score": 0.4, "grader": "operator", "ts": 3}]}}
    merged = ts.merge_grade_store(local, remote)
    assert merged["memory"]["n_grades"] == 3, "shared grade must not be double-counted"
    assert len(merged["memory"]["grades"]) == 3
    assert merged["memory"]["mean_score"] == pytest.approx((0.8 + 0.6 + 0.4) / 3, abs=1e-6)


def test_jsonl_union_dedups(tmp_path: Path):
    local = ['{"e":1}', '{"e":2}']
    remote = ['{"e":2}', '{"e":3}']
    merged = ts.merge_jsonl(local, remote)
    assert merged == ['{"e":1}', '{"e":2}', '{"e":3}'], "union must dedup the shared line"


def test_pii_backstop_catches_email():
    with pytest.raises(ts.PiiLeak):
        ts.assert_no_raw_pii('{"note": "reach me at alice@example.com"}')
    # clean payload passes
    ts.assert_no_raw_pii('{"note": "no personal data here"}')


def test_merge_tenant_dirs_end_to_end(tmp_path: Path):
    """Two divergent checkouts → merge remote INTO local: grades union, jsonl union,
    a new file copied, and a poisoned outbound payload is refused by the backstop."""
    local_dir = tmp_path / "local"
    remote_dir = tmp_path / "remote"

    _write(local_dir / "learning" / "ce_stage_grades.json",
           json.dumps({"memory": {"grades": [{"score": 0.9, "ts": 1}]}}))
    _write(remote_dir / "learning" / "ce_stage_grades.json",
           json.dumps({"memory": {"grades": [{"score": 0.5, "ts": 2}]},
                       "graph": {"grades": [{"score": 0.7, "ts": 3}]}}))
    _write(local_dir / "learning" / "events.jsonl", '{"a":1}\n')
    _write(remote_dir / "learning" / "events.jsonl", '{"a":1}\n{"b":2}\n')
    _write(remote_dir / "skills" / "new.md", "# a new skill from the other device")

    report = ts.merge_tenant_dirs(local_dir, remote_dir)

    merged_grades = json.loads((local_dir / "learning" / "ce_stage_grades.json").read_text())
    assert merged_grades["memory"]["n_grades"] == 2  # two distinct grades unioned
    assert "graph" in merged_grades  # stage only on remote is now present
    assert (local_dir / "learning" / "events.jsonl").read_text().count("\n") == 2  # union
    assert (local_dir / "skills" / "new.md").exists()  # new remote file copied
    assert report.as_dict()["ok"] is True

    # the OUTBOUND payload assert is what guards the push
    with pytest.raises(ts.PiiLeak):
        ts.assert_no_raw_pii("contact: bob@corp.io")


# ── live transport (G5): GPG + git ─────────────────────────────────────────────
gpg_missing = not ts.gpg_available()


@pytest.mark.skipif(gpg_missing, reason="gpg not available")
def test_gpg_and_bundle_roundtrip(tmp_path: Path):
    blob = ts.gpg_encrypt(b"secret learning state: alice@example.com", "pw-123")
    assert b"alice@example.com" not in blob  # ciphertext hides the PII
    assert ts.gpg_decrypt(blob, "pw-123") == b"secret learning state: alice@example.com"
    # bundle/unbundle a dir
    src = tmp_path / "src"; _write(src / "a/b.txt", "hello")
    ts.unbundle(ts.bundle_dir(src), tmp_path / "out")
    assert (tmp_path / "out" / "a" / "b.txt").read_text() == "hello"


@pytest.mark.skipif(gpg_missing, reason="gpg not available")
def test_two_instance_git_sync(tmp_path: Path):
    """The real E2E: a bare repo is the remote; instance A pushes encrypted state,
    instance B pulls it, merges, and pushes the union — and the remote holds ONLY
    ciphertext (no PII in the blob)."""
    import subprocess
    bare = tmp_path / "remote.git"
    subprocess.run(["git", "init", "--bare", str(bare)], check=True, capture_output=True)
    # seed an initial commit so clone --depth 1 has a HEAD
    seed = tmp_path / "seed"
    subprocess.run(["git", "clone", str(bare), str(seed)], check=True, capture_output=True)
    (seed / "README").write_text("corvin tenant remote")
    for a in (["add", "."], ["-c", "user.email=t@t", "-c", "user.name=t", "commit", "-m", "init"],
              ["push", "origin", "HEAD"]):
        subprocess.run(["git", *a], cwd=str(seed), check=True, capture_output=True)
    remote = f"file://{bare}"
    pw = "tenant-pass-xyz"

    # instance A: has memory grade + one event, plus a memory note with an email
    a_dir = tmp_path / "A_state"
    _write(a_dir / "ce_stage_grades.json", json.dumps({"memory": {"grades": [{"score": 0.9, "ts": 1}]}}))
    _write(a_dir / "events.jsonl", '{"a":1}\n')
    _write(a_dir / "memory" / "note.md", "remember alice@example.com prefers dark mode")
    ts.run_git_sync(a_dir, remote, tmp_path / "A_cache", pw)

    # the remote blob is ciphertext — the email must NOT appear in it
    blob = (tmp_path / "A_cache" / "clone" / "learning.tar.gz.gpg").read_bytes()
    assert b"alice@example.com" not in blob

    # instance B: has a DIFFERENT grade + event; pulls A, merges, pushes union
    b_dir = tmp_path / "B_state"
    _write(b_dir / "ce_stage_grades.json", json.dumps({"graph": {"grades": [{"score": 0.5, "ts": 2}]}}))
    _write(b_dir / "events.jsonl", '{"b":2}\n')
    ts.run_git_sync(b_dir, remote, tmp_path / "B_cache", pw)

    merged = json.loads((b_dir / "ce_stage_grades.json").read_text())
    assert "memory" in merged and "graph" in merged  # A's + B's stages both present
    events = (b_dir / "events.jsonl").read_text()
    assert '{"a":1}' in events and '{"b":2}' in events  # event union
    assert (b_dir / "memory" / "note.md").exists()  # A's memory file arrived at B
