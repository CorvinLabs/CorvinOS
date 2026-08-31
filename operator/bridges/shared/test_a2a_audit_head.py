"""Per-subtask E2E — ADR-0141 Tier 4: Audit Chain Transparency.

Covers read_audit_head (head/count from a JSONL chain), build_audit_head
(unsigned + HMAC-signed against an origin recv_key), and the sender-side
anomaly streak logic in check_peer_audit_head.

Runnable standalone: ``python3 operator/bridges/shared/test_a2a_audit_head.py``
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(REPO / "operator" / "forge"))

PASS = 0
FAIL = 0


def t(label: str, ok: bool, *, detail: str = "") -> None:
    global PASS, FAIL
    suffix = f" — {detail}" if detail else ""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}{suffix}")
    if ok:
        PASS += 1
    else:
        FAIL += 1


import a2a_audit_head as ah  # noqa: E402


def _write_chain(path: Path, n: int) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    last_hash = ""
    with open(path, "w", encoding="utf-8") as fh:
        for i in range(n):
            last_hash = f"hash{i:04d}"
            fh.write(json.dumps({"event_type": "x", "hash": last_hash, "ts": 100 + i}) + "\n")
    return last_hash


def test_read_audit_head_empty() -> None:
    with tempfile.TemporaryDirectory() as d:
        head = ah.read_audit_head(Path(d) / "missing.jsonl")
        t("missing chain -> count 0, head ''",
          head["event_count"] == 0 and head["chain_head"] == "")


def test_read_audit_head_populated() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "audit.jsonl"
        last = _write_chain(p, 5)
        head = ah.read_audit_head(p)
        t("populated chain count", head["event_count"] == 5, detail=str(head["event_count"]))
        t("populated chain head", head["chain_head"] == last, detail=head["chain_head"])
        t("populated chain ts", head["latest_ts"] == 104.0)


def test_build_audit_head_unsigned() -> None:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "audit.jsonl"
        _write_chain(p, 3)
        body = ah.build_audit_head(origin_id="", instance_id="iid-x", audit_path=p)
        t("unsigned: signature empty", body["signature"] == "")
        t("unsigned: carries instance_id", body["instance_id"] == "iid-x")
        t("unsigned: carries count", body["event_count"] == 3)


def test_build_audit_head_signed() -> None:
    with tempfile.TemporaryDirectory() as d:
        root = Path(d)
        origins = root / "origins"
        origins.mkdir(parents=True)
        key_hex = "ab" * 32
        of = origins / "peer-x.json"
        # OriginRegistry.load() (remote_trigger_receiver, the R1 key-material
        # validation) requires a COMPLETE origin file: both hmac_key (inbound
        # TaskEnvelope auth) and recv_key (outbound ResponseEnvelope signing)
        # must be present and hex, else it raises origin_key_malformed and
        # _origin_recv_key() falls back to None (empty signature). The fixture
        # previously wrote only recv_key, so the signed path never fired. Write
        # a valid, complete ephemeral origin so the HMAC signing is exercised
        # for real. recv_key is what build_audit_head signs with.
        of.write_text(json.dumps({
            "enabled": True,
            "hmac_key": "cd" * 32,
            "recv_key": key_hex,
        }))
        of.chmod(0o600)
        p = root / "audit.jsonl"
        _write_chain(p, 2)

        old = os.environ.get("REMOTE_ORIGINS_DIR")
        os.environ["REMOTE_ORIGINS_DIR"] = str(origins)
        try:
            body = ah.build_audit_head(origin_id="peer-x", instance_id="iid-y", audit_path=p)
        finally:
            if old is None:
                os.environ.pop("REMOTE_ORIGINS_DIR", None)
            else:
                os.environ["REMOTE_ORIGINS_DIR"] = old

        t("signed: signature present", len(body["signature"]) == 64)
        # Verify the HMAC over the canonical body (minus signature).
        canon_body = {k: v for k, v in body.items() if k != "signature"}
        canonical = json.dumps(canon_body, sort_keys=True, separators=(",", ":"),
                               ensure_ascii=True).encode()
        expect = hmac.new(bytes.fromhex(key_hex), canonical, hashlib.sha256).hexdigest()
        t("signed: HMAC verifies with recv_key", body["signature"] == expect)


def test_anomaly_streak() -> None:
    with tempfile.TemporaryDirectory() as d:
        os.environ["CORVIN_HOME"] = d  # redirect peer-state + audit path
        try:
            # First observation: no prior -> no anomaly.
            a0 = ah.check_peer_audit_head("ep1", {"event_count": 10, "chain_head": "h10"}, emit=False)
            t("first observation -> no anomaly", a0 is None)
            # Same head (no advance) once -> streak 1, still below threshold.
            a1 = ah.check_peer_audit_head("ep1", {"event_count": 10, "chain_head": "h10"}, emit=False)
            t("one stall -> no anomaly yet", a1 is None)
            # Second stall -> streak 2 -> anomaly.
            a2 = ah.check_peer_audit_head("ep1", {"event_count": 10, "chain_head": "h10"}, emit=False)
            t("two stalls -> anomaly", a2 is not None and a2["reason"] == "chain_not_advancing")
            # Advancing resets.
            a3 = ah.check_peer_audit_head("ep1", {"event_count": 11, "chain_head": "h11"}, emit=False)
            t("advance resets -> no anomaly", a3 is None)
        finally:
            os.environ.pop("CORVIN_HOME", None)


def main() -> int:
    test_read_audit_head_empty()
    test_read_audit_head_populated()
    test_build_audit_head_unsigned()
    test_build_audit_head_signed()
    test_anomaly_streak()
    print(f"\n{PASS} passed, {FAIL} failed")
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())
