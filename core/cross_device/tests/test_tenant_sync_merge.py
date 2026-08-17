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
