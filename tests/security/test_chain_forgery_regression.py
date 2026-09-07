"""Adversarial regression for the audit chain (2026-09-07 review, F-A1/A4/A6/A12/A14).

Reuses the reviewer's attacks (``forge_attack.py``, ``floor_attack.py``,
``keyperm.py``) as pytest cases against the real writer/verifier through the
bridge wrapper (``audit.audit_event``) and the chokepoint
(``security_events.write_event``).
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "operator" / "bridges" / "shared", _REPO / "operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

import audit  # noqa: E402
from forge import security_events as se  # noqa: E402


@pytest.fixture
def chain(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(home / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    p = audit.audit_path()
    for i in range(5):
        audit.audit_event("message.received", channel="d", details={"count": i})
    assert se.verify_chain(p) == (True, [])
    return p


def _forged() -> str:
    return json.dumps({"ts": 1.0, "event_type": "consent.granted", "severity": "INFO",
                       "run_id": "", "tool": "", "details": {"uid": "victim", "granted_by": "attacker"}})


def test_appended_forged_unchained_record_is_flagged(chain):
    with open(chain, "a") as fh:
        fh.write(_forged() + "\n")
    ok, problems = se.verify_chain(chain)
    assert not ok
    assert problems == [{"line": 6, "issue": "unchained_record", "event_type": "consent.granted"}]


def test_mid_inserted_forged_record_is_flagged(chain):
    lines = chain.read_text().splitlines()
    lines.insert(2, _forged())
    chain.write_text("\n".join(lines) + "\n")
    ok, problems = se.verify_chain(chain)
    assert not ok
    assert any(p["issue"] == "unchained_record" and p["line"] == 3 for p in problems)


def test_gap_marker_is_bound_to_the_tail(chain):
    """The one admissible hash-less record carries prev_hash + mac (F-A1)."""
    rec = se.write_event(chain, se.CHAIN_GAP_EVENT, severity="CRITICAL",
                         details={"problem_count": 1}, hash_chain=False)
    assert "hash" not in rec and rec["prev_hash"] and "mac" in rec
    assert se.verify_chain(chain) == (True, [])
    # a legacy-shaped gap marker (no prev_hash) — or one moved elsewhere — is refused
    lines = chain.read_text().splitlines()
    gap = json.loads(lines[-1])
    del gap["prev_hash"]
    lines[-1] = json.dumps(gap)
    chain.write_text("\n".join(lines) + "\n")
    ok, problems = se.verify_chain(chain)
    assert not ok and problems[0]["issue"] == "unchained_record"
    lines = chain.read_text().splitlines()
    lines[-1] = json.dumps(rec)
    lines.insert(1, lines.pop())  # moved: its prev_hash no longer matches the tail there
    chain.write_text("\n".join(lines) + "\n")
    ok, problems = se.verify_chain(chain)
    assert not ok and problems[0]["issue"] == "unchained_record" and problems[0]["line"] == 2


def test_hash_chain_false_refused_for_ordinary_events(chain):
    with pytest.raises(ValueError):
        se.write_event(chain, "message.received", details={"count": 1}, hash_chain=False)


def test_pii_by_value_under_innocuous_keys_dropped(chain):
    audit.audit_event("message.received", channel="d", details={
        "note": "alice@example.com", "free": "summarise my medical file for Dr Meier",
        "tel": "+49 170 1234567", "cc": "4111 1111 1111 1111", "reason": "bob@example.com",
        "count": 3,
    })
    d = json.loads(chain.read_text().splitlines()[-1])["details"]
    assert d["count"] == 3
    assert sorted(d["_dropped_fields"]) == ["cc", "free", "note", "reason", "tel"]
    text = chain.read_text()
    for leak in ("alice@example.com", "medical", "1234567", "4111", "bob@example.com"):
        assert leak not in text


def test_tenant_spoof_refused_at_wrapper_and_chokepoint(chain):
    n = len(chain.read_text().splitlines())
    audit.audit_event("message.received", tenant_id="evil-tenant", details={"count": 1})
    with pytest.raises(se.AuditTenantMismatch):
        se.write_event(chain, "message.received",
                       details={"tenant_id": "evil-tenant", "channel": "c", "user": "u"})
    recs = [json.loads(l) for l in chain.read_text().splitlines()[n:]]
    assert [r["event_type"] for r in recs] == ["audit.tenant_mismatch"] * 2
    for r in recs:
        assert r["details"]["tenant_id"] == "_default"
        assert r["details"]["dropped_event_type"] == "message.received"
        assert "evil-tenant" not in json.dumps(r)
    assert se.verify_chain(chain) == (True, [])


def test_unfiltered_flag_is_visible_and_cannot_be_forged(chain):
    se.write_event(chain, "message.received", details={"count": 1}, unfiltered=True)
    d = json.loads(chain.read_text().splitlines()[-1])["details"]
    assert d["_unfiltered"] is True
    se.write_event(chain, "message.received", details={"count": 1, "_unfiltered": True})
    d = json.loads(chain.read_text().splitlines()[-1])["details"]
    assert "_unfiltered" not in d


def test_tail_truncation_detected(chain):
    lines = chain.read_text().splitlines()
    chain.write_text("\n".join(lines[:-1]) + "\n")
    ok, problems = se.verify_chain(chain)
    assert not ok and problems[0]["issue"] == "tail_truncated"


def test_group_readable_anchor_key_is_refused(tmp_path, monkeypatch):
    if os.name == "nt":
        pytest.skip("POSIX mode bits")
    home = tmp_path / "home"
    home.mkdir()
    key = home / "anchor.key"
    key.write_bytes(b"A" * 32)
    os.chmod(key, 0o644)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(home / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(key))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    p = audit.audit_path()
    audit.audit_event("message.received", details={"count": 1})
    assert "mac" not in json.loads(p.read_text().splitlines()[-1])
    ok, problems = se.verify_chain(p)
    assert not ok and problems[0]["issue"] == "anchor_key_insecure_mode"
    os.chmod(key, 0o600)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    assert se._anchor_key() == b"A" * 32


def test_tmp_chain_writes_no_marker_beside_a_real_key(tmp_path, monkeypatch):
    """F-A14: a throwaway chain must not litter the operator's key directory."""
    real_key_dir = tmp_path / "not-tmp-config"  # stands in for ~/.config/corvin-voice
    real_key_dir.mkdir()
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(real_key_dir / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_is_tmp_path", lambda p: "scratch-chain" in str(p))
    chain = tmp_path / "scratch-chain" / "audit.jsonl"
    se.write_event(chain, "message.received", details={"count": 1})
    assert "mac" in json.loads(chain.read_text().splitlines()[-1])
    assert not (real_key_dir / "mac_active_chains").exists()
    assert not (real_key_dir / "chain_tails").exists()
