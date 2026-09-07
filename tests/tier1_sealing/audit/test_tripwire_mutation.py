"""Tier-1 Layer 16: Audit Tripwire (Hash-Chain Integrity) — Mutation Tests (GDPR Art. 32).

Rewritten 2026-09-07 (F-A18) against the REAL API: the previous version imported
a ``verify_hash_chain`` that never existed and called ``assert_all(audit_file=)``,
so the whole module failed at collection and the Tier-1 sealing gate never ran.

Every test drives the canonical writer/verifier (``forge.security_events``) and
the boot tripwire (``corvin_compliance_reports.tripwire.assert_all``) against a
chain in a temporary CORVIN_HOME, exactly as a host boot would.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "operator" / "forge", _REPO / "operator" / "bridges" / "shared",
           _REPO / "core" / "compliance"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))

from forge import security_events as se  # noqa: E402
from corvin_compliance_reports import tripwire  # noqa: E402


@pytest.fixture
def chain(tmp_path, monkeypatch):
    """A 5-record chain under a temp CORVIN_HOME that the tripwire resolves."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    monkeypatch.setattr(se, "_ANCHOR_KEY_REFUSED", None)
    tripwire._verify_cache.clear()
    audit = tripwire._audit_module()
    assert audit is not None
    path = Path(audit.audit_path())
    assert str(home) in str(path), path
    for i in range(5):
        se.write_event(path, "test.event", details={"count": i})
    ok, problems = se.verify_chain(path)
    assert ok, problems
    yield path
    tripwire._verify_cache.clear()


def _rewrite(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestVerifierMutations:
    def test_valid_chain_passes(self, chain):
        assert se.verify_chain(chain) == (True, [])

    def test_mutated_record_fails(self, chain):
        lines = chain.read_text().splitlines()
        lines[1] = lines[1].replace('"count": 1', '"count": 99')
        _rewrite(chain, lines)
        ok, problems = se.verify_chain(chain)
        assert not ok and any(p["issue"] in ("tampered", "mac_tampered") for p in problems)

    def test_deleted_middle_record_fails(self, chain):
        lines = chain.read_text().splitlines()
        del lines[2]
        _rewrite(chain, lines)
        ok, problems = se.verify_chain(chain)
        assert not ok and any(p["issue"] == "broken_chain" for p in problems)

    def test_reordered_records_fail(self, chain):
        lines = chain.read_text().splitlines()
        lines[3], lines[4] = lines[4], lines[3]
        _rewrite(chain, lines)
        ok, _ = se.verify_chain(chain)
        assert not ok

    def test_truncated_hash_fails(self, chain):
        lines = chain.read_text().splitlines()
        rec = json.loads(lines[0])
        rec["hash"] = rec["hash"][:8]
        lines[0] = json.dumps(rec)
        _rewrite(chain, lines)
        ok, _ = se.verify_chain(chain)
        assert not ok

    def test_forged_unchained_record_fails(self, chain):
        """F-A1: a hash-less record is accepted nowhere but as a tail-bound gap marker."""
        forged = json.dumps({"ts": 1.0, "event_type": "consent.granted", "severity": "INFO",
                             "run_id": "", "tool": "", "details": {"granted": True}})
        lines = chain.read_text().splitlines()
        lines.insert(2, forged)
        _rewrite(chain, lines)
        ok, problems = se.verify_chain(chain)
        assert not ok
        assert [p["issue"] for p in problems] == ["unchained_record"]
        assert problems[0]["line"] == 3

    def test_deleted_tail_records_fail(self, chain):
        """F-A12: the out-of-tree tail anchor exposes a self-consistent truncated chain."""
        lines = chain.read_text().splitlines()
        _rewrite(chain, lines[:-2])
        ok, problems = se.verify_chain(chain)
        assert not ok and any(p["issue"] == "tail_truncated" for p in problems)


class TestBootTripwire:
    def test_intact_chain_boots(self, chain):
        results = tripwire.assert_all()
        assert any(r.name == "audit_chain_intact" and r.ok for r in results)

    def test_whole_chain_tamper_blocks_boot(self, chain):
        """Every record broken == lost-anchor-key shape: boot refuses, nothing is deleted."""
        lines = chain.read_text().splitlines()
        for i, line in enumerate(lines):
            rec = json.loads(line)
            rec["hash"] = ("0" if rec["hash"][0] != "0" else "1") + rec["hash"][1:]
            lines[i] = json.dumps(rec)
        _rewrite(chain, lines)
        before = chain.read_text()
        with pytest.raises(tripwire.TripwireError) as exc:
            tripwire.assert_all()
        assert "audit_chain_intact" in str(exc.value)
        # Nothing deleted or rewritten (other gates may APPEND their own records).
        assert chain.read_text().startswith(before)

    def test_truncated_tail_blocks_boot(self, chain):
        lines = chain.read_text().splitlines()
        _rewrite(chain, lines[:-1])
        with pytest.raises(tripwire.TripwireError) as exc:
            tripwire.assert_all()
        assert "tail_truncated" in str(exc.value)

    def test_missing_chain_is_a_fresh_install(self, chain):
        chain.unlink()
        results = tripwire.assert_all()
        r = next(r for r in results if r.name == "audit_chain_intact")
        assert r.ok and "fresh install" in r.detail

    def test_no_override_env_var(self, chain, monkeypatch):
        """There is no switch: any env var an attacker might set is ignored."""
        lines = chain.read_text().splitlines()
        for i, line in enumerate(lines):
            rec = json.loads(line)
            rec["hash"] = ("0" if rec["hash"][0] != "0" else "1") + rec["hash"][1:]
            lines[i] = json.dumps(rec)
        _rewrite(chain, lines)
        for flag in ("SKIP_AUDIT_CHECK", "CORVIN_SKIP_TRIPWIRE", "CORVIN_NO_COMPLIANCE",
                     "CORVIN_COMPLIANCE_OFF"):
            monkeypatch.setenv(flag, "1")
        tripwire._verify_cache.clear()
        with pytest.raises(tripwire.TripwireError):
            tripwire.assert_all()

    def test_redirect_outside_root_blocks_boot(self, chain, tmp_path, monkeypatch):
        """F-A3: an env-only redirect of the chain outside CORVIN_HOME is refused
        (outside pytest; inside pytest the conftest redirect must keep working)."""
        outside = tmp_path / "elsewhere" / "audit.jsonl"
        outside.parent.mkdir()
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(outside))
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        r = tripwire.audit_path_not_redirected()
        assert not r.ok and "OUTSIDE" in r.detail
        monkeypatch.setenv("PYTEST_CURRENT_TEST", "x")
        assert tripwire.audit_path_not_redirected().ok
        # inside the root it is fine without pytest
        monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
        monkeypatch.setenv("VOICE_AUDIT_PATH", str(Path(chain).parent / "other.jsonl"))
        assert tripwire.audit_path_not_redirected().ok
