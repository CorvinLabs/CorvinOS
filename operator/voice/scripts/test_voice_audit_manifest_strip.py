"""R1 regression: segment-manifest verify catches whole-manifest deletion and a
non-dict manifest line (both previously slipped past with exit 0)."""
import json
import os
import tempfile
import hashlib
from pathlib import Path

import pytest


@pytest.fixture
def sealed_env(monkeypatch):
    import sys
    shared = Path(__file__).resolve().parents[2] / "bridges" / "shared"
    if str(shared) not in sys.path:
        sys.path.insert(0, str(shared))
    key = Path(tempfile.mkdtemp()) / "anchor.key"
    key.write_bytes(os.urandom(32))
    os.chmod(key, 0o600)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(key))
    import audit_sealer as S
    import voice_audit as V
    d = Path(tempfile.mkdtemp())
    live = d / "audit.jsonl"

    def append_chain(p, n, ev, start_prev=None):
        prev = start_prev if start_prev is not None else S.last_hash_of_segment(p)
        with p.open("a") as fh:
            for i in range(n):
                r = {"ts": 1.0 + i, "event_type": ev, "severity": "INFO",
                     "run_id": "", "tool": "t", "details": {}, "prev_hash": prev}
                c = json.dumps(r, sort_keys=True, separators=(",", ":"))
                r["hash"] = hashlib.sha256((prev + "\n" + c).encode()).hexdigest()[:16]
                prev = r["hash"]
                fh.write(json.dumps(r) + "\n")

    live.write_text("")
    append_chain(live, 2, "e", start_prev="")
    pol = S.AuditPolicy(rotation=S.RotationPolicy(max_size_mb=0, max_age_days=0),
                        encryption=S.EncryptionConfig(enabled=False),
                        retention=S.RetentionPolicy())
    S.rotate_and_seal(live, pol, now=100.0)
    fp = next(json.loads(l).get("prev_hash") for l in live.open())
    return S, V, d, live, fp


def test_whole_manifest_deletion_detected(sealed_env):
    S, V, d, live, fp = sealed_env
    ok, _, _ = V._verify_segment_manifest(d, fp)
    assert ok, "baseline manifest must verify"
    S.segment_manifest_path(d).unlink()   # delete the WHOLE manifest
    ok, problems, _ = V._verify_segment_manifest(d, fp)
    assert not ok and any(p["issue"] == "manifest_full_strip" for p in problems)


def test_non_dict_manifest_line_flagged(sealed_env):
    S, V, d, live, fp = sealed_env
    S.segment_manifest_path(d).write_text("[1,2,3]\n")   # parses, not a dict
    ok, problems, _ = V._verify_segment_manifest(d, fp)
    assert not ok and any(p["issue"] in ("manifest_corrupt_line", "manifest_full_strip")
                          for p in problems)


def test_genesis_chain_replacement_detected(sealed_env):
    """R3: replacing the live chain with a fresh-genesis chain (prev_hash="")
    orphans all sealed history — must be flagged live_unlinked_from_sealed."""
    S, V, d, live, fp = sealed_env
    ok, _, _ = V._verify_segment_manifest(d, fp)
    assert ok, "baseline must link to sealed tail"
    # Attacker drops in an internally-valid GENESIS chain.
    import json, hashlib
    live.write_text("")
    prev = ""
    with live.open("a") as fh:
        for i in range(2):
            r = {"ts": 1.0 + i, "event_type": "e", "severity": "INFO",
                 "run_id": "", "tool": "t", "details": {}, "prev_hash": prev}
            c = json.dumps(r, sort_keys=True, separators=(",", ":"))
            r["hash"] = hashlib.sha256((prev + "\n" + c).encode()).hexdigest()[:16]
            prev = r["hash"]
            fh.write(json.dumps(r) + "\n")
    fp2 = V._first_prev_hash(live)
    assert fp2 == ""  # genesis
    ok2, problems, _ = V._verify_segment_manifest(d, fp2)
    assert not ok2 and any(p["issue"] == "live_unlinked_from_sealed" for p in problems)
