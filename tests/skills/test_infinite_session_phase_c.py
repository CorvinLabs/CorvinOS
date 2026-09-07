"""Phase C — rollback manager, EMA smoother, drift detector (ADR-0542)."""

from __future__ import annotations

import json

import pytest

from core.infinite_session import (
    CryptoBinding,
    DriftDetector,
    DriftGateType,
    DriftLevel,
    EMASmoother,
    RollbackManager,
    TransactionStatus,
)

TENANT = "_default"


def _callback(events: list):
    def cb(**kwargs):
        events.append(kwargs)
        return True
    return cb


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    return tmp_path


@pytest.fixture
def rm(home):
    return RollbackManager(TENANT)


def _commit(rm, old, new, path="cfg.a", op="update"):
    tx, err = rm.begin_transaction(TENANT, path, old, new, operation=op)
    assert err is None
    ok, err = rm.commit_transaction(tx, TENANT, path, old, new, operation=op)
    assert ok, err
    return tx


# ── RollbackManager ───────────────────────────────────────────────────────


class TestRollbackManager:
    def test_layout_and_binding(self, home, rm):
        assert rm.log_file == home / "tenants" / TENANT / "infinite_session" / "rollback" / "log.jsonl"
        with pytest.raises(ValueError):
            RollbackManager("../x")
        assert rm.begin_transaction("tenant_b", "cfg", {}, {})[1].startswith("Tenant mismatch")
        assert rm.begin_transaction(TENANT, "", {}, {})[1] == "config_path is required"

    def test_begin_writes_fsynced_wal(self, rm):
        tx, err = rm.begin_transaction(TENANT, "cfg.a", {"v": 1}, {"v": 2})
        assert err is None
        wal = json.loads((rm.wal_dir / f"{tx}.json").read_text())
        assert wal["status"] == TransactionStatus.PREPARED.value and wal["new_state"] == {"v": 2}

    def test_commit_is_keyed_and_chained(self, rm):
        events: list = []
        tx1 = _commit(rm, {"v": 1}, {"v": 2})
        tx, err = rm.begin_transaction(TENANT, "cfg.a", {"v": 2}, {"v": 3})
        rm.commit_transaction(tx, TENANT, "cfg.a", {"v": 2}, {"v": 3}, audit_callback=_callback(events))
        entries = [json.loads(l) for l in rm.log_file.read_text().splitlines()]
        assert [e["status"] for e in entries] == ["committed", "committed"]
        assert entries[0]["prev_mac"] == "" and entries[1]["prev_mac"] == entries[0]["mac"]
        assert len(entries[1]["mac"]) == 64
        assert not (rm.wal_dir / f"{tx}.json").exists()
        # audit carries hashes, never states
        assert events[-1]["event_type"] == "infinite_session.transaction_committed"
        assert "old_state" not in events[-1] and len(events[-1]["old_state_hash"]) == 64
        assert rm.verify_chain_integrity(TENANT) == (True, None)

    def test_commit_without_wal_fails(self, rm):
        ok, err = rm.commit_transaction("00000000-0000-0000-0000-000000000000", TENANT, "cfg", {}, {})
        assert not ok and "WAL entry not found" in err

    def test_commit_payload_mismatch_is_logged_failed_and_chained(self, rm):
        tx, _ = rm.begin_transaction(TENANT, "cfg.a", {"v": 1}, {"v": 2})
        ok, err = rm.commit_transaction(tx, TENANT, "cfg.a", {"v": 1}, {"v": 999})
        assert not ok and "does not match WAL" in err
        entries = [json.loads(l) for l in rm.log_file.read_text().splitlines()]
        assert entries[-1]["status"] == "failed" and len(entries[-1]["mac"]) == 64
        # a FAILED entry is a full chain link, not a gap
        _commit(rm, {"v": 1}, {"v": 2})
        assert rm.verify_chain_integrity(TENANT) == (True, None)

    def test_tamper_detected_even_with_recomputed_sha(self, rm):
        _commit(rm, {"v": 1}, {"v": 2})
        entries = [json.loads(l) for l in rm.log_file.read_text().splitlines()]
        entries[0]["new_state"] = {"v": 42}
        rm.log_file.write_text("\n".join(json.dumps(e) for e in entries) + "\n")
        ok, err = rm.verify_chain_integrity(TENANT)
        assert not ok and "MAC mismatch" in err

    def test_foreign_key_cannot_forge(self, home, rm):
        _commit(rm, {"v": 1}, {"v": 2})
        other = CryptoBinding()
        entries = [json.loads(l) for l in rm.log_file.read_text().splitlines()]
        body = {k: v for k, v in entries[0].items() if k != "mac"}
        body["new_state"] = {"v": 42}
        from core.infinite_session.crypto_binding import canonical_json
        forged_mac, _ = other.hmac_bytes("tenant_b", canonical_json(body))
        body["mac"] = forged_mac
        rm.log_file.write_text(json.dumps(body) + "\n")
        assert rm.verify_chain_integrity(TENANT)[0] is False

    def test_rollback_creates_revert(self, rm):
        tx = _commit(rm, {"v": 1}, {"v": 2})
        events: list = []
        ok, err = rm.rollback_transaction(TENANT, tx, audit_callback=_callback(events))
        assert ok, err
        latest = rm.get_transaction_history(TENANT)[0]
        assert latest["operation"] == "revert" and latest["new_state"] == {"v": 1}
        assert events[-1]["event_type"] == "infinite_session.rollback_initiated"
        assert rm.verify_chain_integrity(TENANT) == (True, None)

    def test_rollback_missing_or_uncommitted_fails(self, rm):
        assert rm.rollback_transaction(TENANT, "nope")[0] is False
        tx, _ = rm.begin_transaction(TENANT, "cfg.a", {"v": 1}, {"v": 2})
        rm.commit_transaction(tx, TENANT, "cfg.a", {"v": 1}, {"v": 3})  # → FAILED
        assert rm.rollback_transaction(TENANT, tx)[0] is False

    def test_history_filter_and_order(self, rm):
        _commit(rm, {"v": 1}, {"v": 2}, path="a")
        _commit(rm, {"v": 1}, {"v": 2}, path="b")
        _commit(rm, {"v": 2}, {"v": 3}, path="a")
        hist = rm.get_transaction_history(TENANT, config_path="a")
        assert [h["new_state"]["v"] for h in hist] == [3, 2]
        assert rm.get_transaction_history("tenant_b") == []

    def test_wal_replay_closes_uncommitted_transactions(self, home, rm):
        _commit(rm, {"v": 1}, {"v": 2})
        tx, _ = rm.begin_transaction(TENANT, "cfg.a", {"v": 2}, {"v": 3})  # crash before commit
        assert (rm.wal_dir / f"{tx}.json").exists()
        restarted = RollbackManager(TENANT)  # replay on construction (grace window)
        assert (restarted.wal_dir / f"{tx}.json").exists()  # too young: may be in flight elsewhere
        assert restarted.recover_pending(max_age_s=0.0) == [tx]  # forced sweep = crash recovery
        assert not (restarted.wal_dir / f"{tx}.json").exists()
        latest = restarted.get_transaction_history(TENANT)[0]
        assert latest["transaction_id"] == tx and latest["status"] == "rolled_back"
        assert restarted.verify_chain_integrity(TENANT) == (True, None)
        assert restarted.recover_pending(max_age_s=0.0) == []

    def test_concurrent_commits_keep_chain(self, home):
        import threading
        errors: list = []

        def worker(n):
            m = RollbackManager(TENANT)
            for i in range(5):
                tx, err = m.begin_transaction(TENANT, f"cfg.{n}", {"i": i}, {"i": i + 1})
                ok, err = m.commit_transaction(tx, TENANT, f"cfg.{n}", {"i": i}, {"i": i + 1})
                if not ok:
                    errors.append(err)

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert errors == []
        m = RollbackManager(TENANT)
        assert m.verify_chain_integrity(TENANT) == (True, None)
        assert len(m.get_transaction_history(TENANT)) == 20


# ── EMASmoother ───────────────────────────────────────────────────────────


class TestEMASmoother:
    def test_ema_and_classification(self):
        s = EMASmoother(alpha=0.5)
        assert s.compute_ema([1.0, 1.0, 1.0]) == [1.0, 1.0, 1.0]
        assert s.classify_drift(0.05) == DriftLevel.NORMAL
        assert s.classify_drift(0.12) == DriftLevel.WARNING
        assert s.classify_drift(0.5) == DriftLevel.CRITICAL

    def test_sustained_drift(self):
        s = EMASmoother()
        steady = s.process_samples([(str(i), 0.5) for i in range(10)])
        assert s.detect_sustained_drift(steady, min_critical_samples=3)[0] is False
        ramp = s.process_samples([(str(i), 0.1 * i) for i in range(12)])
        assert s.detect_sustained_drift(ramp, min_critical_samples=3)[0] is True

    def test_invalid_params(self):
        with pytest.raises(ValueError):
            EMASmoother(alpha=1.5)


# ── DriftDetector ─────────────────────────────────────────────────────────


class TestDriftDetector:
    def test_binding_and_layout(self, home):
        d = DriftDetector(TENANT)
        assert d.alerts_dir == home / "tenants" / TENANT / "infinite_session" / "drift" / "alerts"
        assert d.check_drift("tenant_b", "cfg", [("t", 1.0)]) == (False, None)

    def test_assess_series_pure(self, home):
        d = DriftDetector(TENANT)
        assert d.assess_series([]).level == DriftLevel.NORMAL
        assert d.assess_series([(str(i), 0.5) for i in range(5)]).level == DriftLevel.NORMAL
        a = d.assess_series([(str(i), 0.1 * i) for i in range(12)])
        assert a.level == DriftLevel.CRITICAL and a.sustained
        assert list(d.alerts_dir.glob("*.json")) == []  # no side effects

    def test_assess_states_uses_numeric_leaves(self, home):
        d = DriftDetector(TENANT)
        states = [(str(i), {"cfg": {"threshold": 0.1 * i, "name": "x", "flag": True}}) for i in range(12)]
        a = d.assess_states(states)
        assert a.level == DriftLevel.CRITICAL and a.message.startswith("cfg.threshold")
        assert d.assess_states([("0", {"name": "only-strings"})]).level == DriftLevel.NORMAL

    def test_check_drift_gate_types_and_alerts(self, home):
        d = DriftDetector(TENANT)
        ramp = [(str(i), 0.1 * i) for i in range(12)]
        block, alert = d.check_drift(TENANT, "cfg.a", ramp, DriftGateType.STRICT)
        assert block is True and alert.action_taken == "blocked"
        block, alert = d.check_drift(TENANT, "cfg.b", ramp, DriftGateType.ADVISORY)
        assert block is False and alert.action_taken == "logged"
        assert d.check_drift(TENANT, "cfg.c", [(str(i), 0.5) for i in range(5)]) == (False, None)
        active = d.get_active_alerts(TENANT)
        assert {a.config_path for a in active} == {"cfg.a", "cfg.b"}
        assert d.get_active_alerts(TENANT, config_path="cfg.a")[0].alert_id == d.get_active_alerts(TENANT, "cfg.a")[0].alert_id

    def test_check_drift_fail_closed_on_bad_samples(self, home):
        d = DriftDetector(TENANT)
        block, alert = d.check_drift(TENANT, "cfg", [("t", "not-a-number")] * 5)  # type: ignore[list-item]
        assert block is True and alert.action_taken == "error"

    def test_revert_button_rolls_back_and_dismisses(self, home):
        d = DriftDetector(TENANT)
        rm = RollbackManager(TENANT)
        _commit(rm, {"threshold": 0.1}, {"threshold": 0.9}, path="cfg.a")
        _, alert = d.check_drift(TENANT, "cfg.a", [(str(i), 0.1 * i) for i in range(12)])
        events: list = []
        ok, err = d.create_revert_button(TENANT, alert.alert_id, rm, audit_callback=_callback(events))
        assert ok, err
        assert rm.get_transaction_history(TENANT)[0]["new_state"] == {"threshold": 0.1}
        assert d.get_active_alerts(TENANT) == []
        assert events[-1]["event_type"] == "infinite_session.drift_revert_button_pressed"
        assert d.create_revert_button(TENANT, "missing", rm)[0] is False
        assert d.create_revert_button(TENANT, "../x", rm)[0] is False


def test_e2e_drift_detection_to_recovery(home):
    """config drifts over 12 commits → alert → revert button → chain verifies."""
    rm = RollbackManager(TENANT)
    d = DriftDetector(TENANT)
    samples = []
    value = 0.5
    for i in range(12):
        new_value = round(value + 0.08, 3)
        _commit(rm, {"lr": value}, {"lr": new_value}, path="model.lr")
        value = new_value
        samples.append((f"2026-01-01T00:00:{i:02d}Z", value))
    blocked, alert = d.check_drift(TENANT, "model.lr", samples)
    assert blocked and alert.drift_level == DriftLevel.CRITICAL
    ok, err = d.create_revert_button(TENANT, alert.alert_id, rm)
    assert ok, err
    assert rm.get_transaction_history(TENANT)[0]["operation"] == "revert"
    assert rm.verify_chain_integrity(TENANT) == (True, None)
