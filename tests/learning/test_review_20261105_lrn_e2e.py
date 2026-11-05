"""Adversarial review 2026-11-05, area LRN (learning stores, gate, optimizer, retention, erasure).

Repro assertions from the review scratchpad, plus the route / host / erasure-handler paths:

* LRN-2   the process-wide learning emitter wrote every tenant's events into the boot tenant's
          directory (unbound store); ``recent_outcomes(acme)`` read ``_default``'s partitions.
* LRN-5   ``event_store.query_events`` logged foreign records verbatim (user_id, payload).
* LRN-6   both stores share ``learning/events``; each reader now filters by schema.
* LRN-7   ``count_events`` dropped every record after a torn line.
* LRN-8   a stored ``+00:00Z`` timestamp / a record missing a key broke ``read_events``.
* LRN-9   the gate's "accepted" outlived a failed store write / a failed route audit.
* LRN-12  the optimizer took "no evidence" as a 0 % baseline and accepted on it.
* LRN-13  no retention: the learning host now prunes both stores' partitions and the ledgers.
* LRN-15  tenant validators accepted a trailing newline; event_store had a looser private rule.
* LRN-16  one malformed token value disabled the Free-tier savings meter.
* LRN-17  the PII scrub rewrote a skill_executed ``output`` and broke honest output hashes.
* LRN-20  ``fetch_unprocessed_outcomes`` could never return anything (removed).
* LRN-21  ``signature_verified`` was taken from the caller.
* LRN-22  the feedback time window relabelled the clock and had no lower bound.
* LRN-24  the gate's producer key (recomputable from the subject id) survived Art. 17 erasure.
* LRN-26  SkillAdapter persisted through a fixed, unsynced, umask temp file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console" / "tests"))
for _p in (_REPO / "corvin_operator" / "bridges" / "shared", _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))
from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

from core.learning import event_persistence  # noqa: E402
from core.learning.event_store import EventStore, TenantEventStores  # noqa: E402
from core.learning.learning_events import EventType, LearningEvent  # noqa: E402


@pytest.fixture()
def fake_chain(monkeypatch):
    """Record chain writes instead of committing them (primitive-level cases)."""
    calls: list[str] = []

    def _audit(event_type, *, tenant_id, details, user=""):
        calls.append(event_type)
        return f"ref-{len(calls)}"

    monkeypatch.setattr(event_persistence, "core_audit_event", _audit)
    return calls


@pytest.fixture()
def store(tmp_path, fake_chain):
    return EventStore(tmp_path / "tenants" / "t1", tenant_id="t1")


def _ev(tenant="t1", event_type=EventType.OUTCOME, signal=None, skill="os.delegation_router", lom=None):
    return LearningEvent.create(event_type=event_type, skill_id=skill, tenant_id=tenant,
                                signal=signal if signal is not None else {"task_id": "x", "success": True},
                                lom=lom)


# ── LRN-2 ─────────────────────────────────────────────────────────────────────


def test_lrn2_unbound_store_under_a_tenant_home_refuses_a_foreign_event(tmp_path, fake_chain):
    boot_home = tmp_path / "tenants" / "_default"
    store = EventStore(boot_home)  # the shape core/skills/boot.py used to build
    with pytest.raises(ValueError):
        store.write_event(_ev(tenant="acme"))
    assert not list((boot_home / "learning" / "events").glob("*.jsonl"))


def test_lrn2_process_emitter_writes_and_reads_each_tenants_own_directory(tmp_path, fake_chain, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from core.learning.event_emitter import EventEmitter
    from core.learning.outcome_sink import emit_task_outcome, recent_outcomes
    from core.skills.boot import _default_learning_backend

    backend = _default_learning_backend("_default")  # what boot_skills attaches
    assert isinstance(backend.emitter.store, TenantEventStores)
    emitter: EventEmitter = backend.emitter
    try:
        assert emit_task_outcome(tenant_id="acme", task_id="t-a", status="completed", emitter=emitter)
        assert emit_task_outcome(tenant_id="_default", task_id="t-d", status="failed", emitter=emitter)
    finally:
        emitter.stop(timeout=5.0)
    assert emitter.written == 2 and emitter.write_failures == 0
    acme_dir = tmp_path / "tenants" / "acme" / "learning" / "events"
    default_dir = tmp_path / "tenants" / "_default" / "learning" / "events"
    acme_lines = "".join(p.read_text() for p in acme_dir.glob("*.jsonl"))
    default_lines = "".join(p.read_text() for p in default_dir.glob("*.jsonl"))
    assert '"t-a"' in acme_lines and '"t-a"' not in default_lines
    assert '"t-d"' in default_lines and '"t-d"' not in acme_lines
    assert recent_outcomes("acme", store=emitter.store) == (1, 1)
    assert recent_outcomes("_default", store=emitter.store) == (0, 1)


def test_lrn2_each_tenant_store_is_bound(tmp_path, fake_chain):
    stores = TenantEventStores(lambda t: tmp_path / "tenants" / t)
    acme = stores.for_tenant("acme")
    assert acme.tenant_id == "acme"
    with pytest.raises(ValueError):
        acme.write_event(_ev(tenant="other"))
    with pytest.raises(ValueError):
        stores.for_tenant("../x")


# ── LRN-5 / LRN-6 / LRN-7 ────────────────────────────────────────────────────


def test_lrn7_count_events_survives_a_torn_line(store):
    ev = _ev()
    f = store.events_dir / (ev.timestamp.split("T")[0] + ".jsonl")
    store.write_event(ev)
    with open(f, "ab") as fh:
        fh.write(b'{"event_id":"torn","tenant_id":"t1"')  # crash mid-append
    store.write_event(_ev())
    store.write_event(_ev())
    assert len(store.query_events("t1")) == 3
    assert store.count_events("t1") == 3


def _persistence_line(**extra):
    rec = {"event_type": "learning.method.observation", "tenant_id": "t1",
           "user_id": "alice@example.com", "event_id": "p1", "instance_id": "i", "session_id": "s",
           "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "") + "Z",
           "payload": {"prompt_excerpt": "my IBAN is DE89370400440532013000"}, "tags": []}
    rec.update(extra)
    return json.dumps(rec) + "\n"


def test_lrn6_count_events_ignores_tombstones_and_the_sibling_schema(store):
    ev = _ev()
    store.write_event(ev)
    f = store.events_dir / (ev.timestamp.split("T")[0] + ".jsonl")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"event_type": "learning.tombstone", "tenant_id": "t1",
                             "erasure_id": "e", "erased_count": 4}) + "\n")
        fh.write(_persistence_line())
    assert store.count_events("t1") == len(store.query_events("t1")) == 1
    assert store.count_events("t1", EventType.OUTCOME) == 1


def test_lrn6_persistence_readers_ignore_the_adr0314_stores_records(tmp_path, monkeypatch, fake_chain):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from core.learning.event_persistence import EventStore as PStore
    from core.learning.event_schema import LearningEvent as PEvent, LearningEventType

    pstore = PStore("t1")
    pev = PEvent(event_type=LearningEventType.METHOD_OBSERVATION, tenant_id="t1", instance_id="i",
                 skill_name="os.method_discovery", session_id="s", payload={"hash": "h"},
                 timestamp_utc=datetime.utcnow())
    asyncio.run(pstore.write_event(pev, "t1"))
    # the ADR-0314 store writes the same directory
    EventStore(tmp_path / "tenants" / "t1", tenant_id="t1").write_event(_ev())
    assert asyncio.run(pstore.get_event_count(tenant_id="t1")) == 1
    got = asyncio.run(pstore.read_events(tenant_id="t1"))  # unfiltered: no KeyError('instance_id')
    assert [e.event_id for e in got] == [pev.event_id]


def test_lrn5_query_events_never_logs_a_record(store, caplog):
    ev = _ev()
    store.write_event(ev)
    f = store.events_dir / (ev.timestamp.split("T")[0] + ".jsonl")
    with open(f, "a", encoding="utf-8") as fh:
        fh.write(_persistence_line())
        # a record of THIS schema with a missing field is logged by field names only
        fh.write(json.dumps({"event_type": "outcome", "tenant_id": "t1", "event_id": "x",
                             "timestamp": "2026-11-05T00:00:00Z",
                             "signal": {"note": "alice@example.com"}}) + "\n")
    caplog.set_level(logging.WARNING)
    assert len(store.query_events("t1")) == 1
    assert "alice@example.com" not in caplog.text and "DE89" not in caplog.text, caplog.text[:400]
    assert "skill_id" in caplog.text  # the malformed own record is still reported


# ── LRN-8 ────────────────────────────────────────────────────────────────────


def test_lrn8_legacy_timestamp_and_missing_key_do_not_break_read_events(tmp_path, monkeypatch, fake_chain):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    from core.learning.event_persistence import EventStore as PStore
    from core.learning.event_schema import LearningEvent as PEvent, LearningEventType

    pstore = PStore("t1")
    ev = PEvent(event_type=LearningEventType.METHOD_OBSERVATION, tenant_id="t1", instance_id="i",
                skill_name="os.method_discovery", session_id="s", payload={"hash": "h2"},
                timestamp_utc=datetime.utcnow())
    asyncio.run(pstore.write_event(ev, "t1"))
    part = pstore.events_dir / f"{ev.timestamp_utc.date().isoformat()}.jsonl"
    legacy = json.loads(_persistence_line(event_id="legacy", timestamp="2026-11-04T10:00:00+00:00Z"))
    missing = json.loads(_persistence_line(event_id="missing"))
    del missing["session_id"]
    with open(part, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(legacy) + "\n" + json.dumps(missing) + "\n")
    got = asyncio.run(pstore.read_events(tenant_id="t1", event_type=LearningEventType.METHOD_OBSERVATION))
    ids = {e.event_id for e in got}
    assert ev.event_id in ids and "legacy" in ids and "missing" not in ids


# ── LRN-9 ────────────────────────────────────────────────────────────────────


def test_lrn9_store_failure_after_admit_voids_the_accepted_line(tmp_path, monkeypatch):
    calls: list[str] = []

    def fake_core_audit(event_type, *, tenant_id, details, user=""):
        calls.append(event_type)
        if event_type == "learning.feedback":
            raise RuntimeError("core audit write did not commit")
        return "ref-%d" % len(calls)

    monkeypatch.setattr(event_persistence, "core_audit_event", fake_core_audit)
    from core.learning.feedback_gate import DecisionLedger, source_key_of

    home = tmp_path / "tenants" / "t1"
    store = EventStore(home, tenant_id="t1")
    ev = _ev(event_type=EventType.FEEDBACK, signal={"rating": 1, "producer": "console:alice"}, lom="x")
    with pytest.raises(RuntimeError):
        store.write_event(ev, subject_verified=True)
    assert "learning.feedback_accepted" in calls
    assert calls[-1] == "learning.feedback_not_stored"
    assert store.query_events("t1") == []
    ledger = DecisionLedger(home / "learning")
    hist = ledger.source_history("os.delegation_router", source_key_of("t1", "console:alice"))
    assert [r for r in hist if r.get("decision") == "accepted"] == []
    assert [r for r in ledger.subject_history("os.delegation_router")
            if r.get("decision") == "accepted"] == []
    assert [r for r in ledger.tail() if r.get("decision") == "accepted"] == []


def test_lrn9_route_audit_failure_voids_the_gates_accepted_entry(tmp_path):
    """POST /v1/console/feedback/skill: the gate admits, the route's own audit record fails (500)."""
    with _sandbox(tmp_path) as (client, home, tenant_id, _emitter, chain):
        mod = sys.modules.get("corvin_console.routes.stream4_skill_feedback")
        assert mod is not None, "stream4 route not mounted"
        original = mod.emit_skill_audit_event
        mod.emit_skill_audit_event = lambda *a, **k: False
        try:
            r = client.post("/v1/console/feedback/skill", json={
                "skill_id": "os.delegation_router", "subject_id": "task-1", "rating": 1,
                "category": "accuracy"})
        finally:
            mod.emit_skill_audit_event = original
        assert r.status_code == 500, r.text
        from core.learning.feedback_gate import DecisionLedger

        ledger = DecisionLedger(home / "tenants" / tenant_id / "learning")
        assert ledger.subject_history("os.delegation_router") == [], "refused feedback counted"
        chained = [json.loads(ln).get("event_type") for ln in chain.read_text().splitlines() if ln.strip()]
        assert "learning.feedback_accepted" in chained
        assert "learning.feedback_not_stored" in chained


# ── LRN-12 ───────────────────────────────────────────────────────────────────


def _hyp():
    from core.skills.os_skills.feedback_loop import ConfigHypothesis

    return ConfigHypothesis(hypothesis_id="h1", skill_id="os.delegation_router",
                            param="confidence_threshold", delta=-0.05,
                            reason="user rated poorly", confidence=0.8)


def test_lrn12_empty_window_is_not_a_zero_percent_baseline(tmp_path):
    from core.skills.os_skills.skill_adapter import SkillAdapter

    a = SkillAdapter("os.delegation_router", "t1", work_dir=tmp_path)
    for _ in range(50):
        assert a.run_optimizer_epoch(None, 0, 0) == (False, "no_evidence")
    assert a.state.epoch == 1  # unchanged: no window was counted
    accepted, why = a.run_optimizer_epoch(_hyp(), 1, 10)
    assert not accepted, f"accepted on no baseline evidence: {why}"
    assert a.state.hypotheses_tested == 0  # still baseline phase: nothing tested yet


def test_lrn12_baseline_is_the_mean_and_every_tested_hypothesis_counts(tmp_path):
    from core.skills.os_skills.skill_adapter import SkillAdapter

    a = SkillAdapter("os.delegation_router", "t1", work_dir=tmp_path)
    for i in range(49):  # epochs 2..50, alternating 20 % / 60 %: mean ~40 %, last window 20 %
        a.run_optimizer_epoch(None, 2 if i % 2 == 0 else 6, 10)
    assert a.state.baseline_success_rate == pytest.approx((25 * 0.2 + 24 * 0.6) / 49)
    accepted, _ = a.run_optimizer_epoch(_hyp(), 9, 10)  # +50 points over the mean
    assert accepted
    a.run_optimizer_epoch(_hyp(), 4, 10)  # rejected
    assert a.state.hypotheses_tested == 2 and a.state.hypotheses_accepted == 1


# ── LRN-26 ───────────────────────────────────────────────────────────────────


def test_lrn26_skill_adapter_persists_privately_without_a_leftover_temp(tmp_path):
    from core.skills.os_skills.skill_adapter import SkillAdapter

    a = SkillAdapter("os.delegation_router", "t1", work_dir=tmp_path)
    a.run_optimizer_epoch(None, 5, 10)
    assert a.config_file.exists()
    assert (a.config_file.stat().st_mode & 0o777) == 0o600
    assert not list(a.config_file.parent.glob("*.tmp")), "temp file left behind"
    assert json.loads(a.config_file.read_text())["optimizer"]["epoch"] == 2


# ── LRN-13 ───────────────────────────────────────────────────────────────────


def test_lrn13_learning_host_prunes_both_stores_and_the_gate_ledgers(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, _emitter, chain):
        from core.background import learning_host
        from core.learning.feedback_gate import DecisionLedger

        events = home / "tenants" / tenant_id / "learning" / "events"
        events.mkdir(parents=True, exist_ok=True)
        old_day = (datetime.now(timezone.utc) - timedelta(days=200)).date().isoformat()
        fresh_day = datetime.now(timezone.utc).date().isoformat()
        old = events / f"{old_day}.jsonl"
        old.write_text(
            json.dumps({"event_id": "a", "event_type": "outcome", "skill_id": "s",
                        "tenant_id": tenant_id, "timestamp": old_day + "T00:00:00Z"}) + "\n"
            + _persistence_line(tenant_id=tenant_id, timestamp=old_day + "T00:00:00Z"),
            encoding="utf-8")
        fresh = events / f"{fresh_day}.jsonl"
        fresh.write_text(json.dumps({"event_id": "b", "event_type": "outcome", "skill_id": "s",
                                     "tenant_id": tenant_id, "timestamp": fresh_day + "T00:00:00Z"})
                         + "\n", encoding="utf-8")
        ledger = DecisionLedger(home / "tenants" / tenant_id / "learning")
        now = time.time()
        with ledger.locked("os.delegation_router"):
            ledger.append(ts=now - 200 * 86400, subject="os.delegation_router", decision="accepted",
                          reason="valid", source_key="src_old")
            ledger.append(ts=now, subject="os.delegation_router", decision="accepted",
                          reason="valid", source_key="src_new")

        host = learning_host.LearningHost(tenant_id, sources=lambda t: [],
                                          prune=lambda *a, **k: None)
        asyncio.run(host.tick(now=now))  # the daily retention runs on the first tick

        assert not old.exists(), "expired partition (both stores' records) kept"
        assert fresh.exists()
        assert [r["source_key"] for r in ledger.tail()] == ["src_new"]
        assert not (ledger._subject_dir("os.delegation_router") / "src_old.jsonl").exists() or \
            ledger.source_history("os.delegation_router", "src_old") == []
        assert host.status()["retention"]["events_removed"] == 2
        assert host.status()["retention"]["ledger_lines_removed"] >= 3
        recs = [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]
        scopes = sorted((r.get("details") or {}).get("scope") for r in recs
                        if r.get("event_type") == "learning.retention")
        # RL-10: the remaining tenant stores are chained too, one record per scope
        assert scopes == ["ce_stage_grades", "events", "feedback_gate", "model_confidence",
                          "skill_feedback", "token_metrics"], scopes


# ── LRN-15 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad", ["acme\n", "_default\n"])
def test_lrn15_canonical_validators_refuse_a_trailing_newline(bad):
    from core.tenants.validation import validate_tenant_id
    from forge.tenants import validate_tenant_id as forge_validate  # type: ignore[import-not-found]

    with pytest.raises(ValueError):
        validate_tenant_id(bad)
    with pytest.raises(ValueError):
        forge_validate(bad)


@pytest.mark.parametrize("bad", ["ACME", "-x", "test", "x\n"])
def test_lrn15_event_store_uses_the_canonical_rule(bad):
    from core.learning.event_store import _validate_tenant_id

    with pytest.raises(ValueError):
        _validate_tenant_id(bad)


# ── LRN-16 ───────────────────────────────────────────────────────────────────


def test_lrn16_one_malformed_token_value_does_not_disable_the_meter(tmp_path, monkeypatch):
    from core.learning import savings_meter

    def _rec(ts, **det):
        return json.dumps({"event_type": "os_turn.completed", "ts": ts,
                           "details": {"model": "claude-haiku-4-5", **det}}) + "\n"

    monkeypatch.setattr(savings_meter, "_epoch_ts", lambda t: 0.0)
    chain = tmp_path / "audit.jsonl"
    now = time.time()
    chain.write_text(_rec(now, input_tokens=1000, output_tokens=200)
                     + _rec(now, input_tokens="1.5e3", output_tokens=10)
                     + _rec(now, input_tokens=-5, output_tokens=10), encoding="utf-8")
    r = savings_meter.read("t1", "claude-opus-5", chain_path=chain, now=now)
    assert r is not None and r.total_turns == 3


# ── LRN-17 ───────────────────────────────────────────────────────────────────


def test_lrn17_honest_output_claim_is_accepted(store):
    from core.learning.feedback_gate import FeedbackRejected
    from core.learning.reality_check import execution_output_hash

    produced = {"output": {"engine": "native", "run_started": "1793837611", "confidence": 0.8,
                           "job_id": "a" * 40}, "note": "call 555-123-4567"}
    store.write_event(_ev(event_type=EventType.SKILL_EXECUTED, signal=produced))
    stored = store.query_events("t1", event_type=EventType.SKILL_EXECUTED)[0].signal
    assert stored["output"] == produced["output"]
    assert stored["note"] == "call [PHONE]"  # the rest of the signal is still scrubbed
    fb = _ev(event_type=EventType.FEEDBACK, lom="producer",
             signal={"rating": 1, "claimed_output_hash": execution_output_hash(produced)})
    try:
        store.write_event(fb)
    except FeedbackRejected as e:
        pytest.fail(f"honest claim rejected: {e.reason}")


# ── LRN-20 / LRN-21 / LRN-22 ─────────────────────────────────────────────────


def test_lrn20_dead_pull_reader_is_gone():
    from core.learning.model_selection_learning_listener import ModelSelectionLearningListener

    assert not hasattr(ModelSelectionLearningListener, "fetch_unprocessed_outcomes")
    assert not hasattr(ModelSelectionLearningListener, "run_epoch")


def test_lrn21_signature_verified_is_never_the_callers_claim():
    import inspect

    from core.learning.feedback_sink import FeedbackEvent, OutcomeFeedbackType

    assert "signature_verified" not in inspect.signature(FeedbackEvent.create).parameters
    ev = FeedbackEvent(feedback_id="f", skill_id="s", task_id="t", tenant_id="t1",
                       timestamp="2026-11-05T00:00:00Z", outcome_feedback=OutcomeFeedbackType.YES,
                       signature_verified=True)
    assert ev.signature_verified is False


def test_lrn22_feedback_window_is_utc_and_bounded_on_both_sides():
    from core.learning.feedback_sink import FeedbackValidator

    ok = FeedbackValidator._is_within_feedback_window
    now = datetime.now(timezone.utc)
    plus2 = timezone(timedelta(hours=2))
    assert ok(now.astimezone(plus2).isoformat())                       # now, written at +02:00
    assert ok((now - timedelta(minutes=30)).isoformat().replace("+00:00", "Z"))
    assert ok((now - timedelta(minutes=5)).replace(tzinfo=None).isoformat())  # naive = UTC
    assert not ok((now + timedelta(minutes=10)).isoformat())           # future
    assert not ok((now - timedelta(minutes=61)).isoformat())           # too old
    assert not ok((now - timedelta(minutes=61)).astimezone(plus2).isoformat())
    assert not ok("not a timestamp")


# ── LRN-24 ───────────────────────────────────────────────────────────────────


def test_lrn24_art17_erasure_removes_the_subjects_gate_ledger_lines(tmp_path):
    with _sandbox(tmp_path) as (_client, home, tenant_id, emitter, _chain):
        from core.learning.feedback_gate import DecisionLedger, admit, source_key_of

        store = emitter.store
        for producer in ("console:alice@example.com", "bob"):
            admit(_ev(tenant=tenant_id, event_type=EventType.FEEDBACK, signal={"rating": 1}),
                  store, source=producer)
        ledger = DecisionLedger(home / "tenants" / tenant_id / "learning")
        alice = source_key_of(tenant_id, "console:alice@example.com")
        bob = source_key_of(tenant_id, "bob")
        assert {r["source_key"] for r in ledger.tail()} == {alice, bob}

        from erasure_handlers import LearningEventHandler, LayerStatus  # type: ignore

        res = LearningEventHandler(tenant_id=tenant_id).purge("alice@example.com", "req-1")
        assert res.status == LayerStatus.APPLIED, res.reason
        assert {r["source_key"] for r in ledger.tail()} == {bob}
        assert {r["source_key"] for r in ledger.subject_history("os.delegation_router")} == {bob}
        assert not (ledger._subject_dir("os.delegation_router") / f"{alice}.jsonl").exists()
        assert (ledger._subject_dir("os.delegation_router") / f"{bob}.jsonl").exists()
        for f in (home / "tenants" / tenant_id / "learning" / "feedback_gate").rglob("*.jsonl"):
            assert (f.stat().st_mode & 0o777) == 0o600


# ══ Refutation round 2026-11-05 (RL-1 .. RL-11) ══════════════════════════════
#
# Copies of the refutation repros (scratchpad refute_learning/) plus the route / host /
# subprocess paths. Each asserts the corrected behaviour.

import errno  # noqa: E402
import multiprocessing as _mp  # noqa: E402


def _fb(tenant="t1"):
    return LearningEvent.create(event_type=EventType.FEEDBACK, skill_id="os.delegation_router",
                                tenant_id=tenant, signal={"rating": 1, "producer": "console:alice"},
                                lom="x")


@pytest.fixture()
def chain_calls(monkeypatch):
    out: list[tuple[str, dict]] = []

    def fake(event_type, *, tenant_id, details, user=""):
        out.append((event_type, dict(details)))
        return "ref-%d" % len(out)

    monkeypatch.setattr(event_persistence, "core_audit_event", fake)
    return out


def _accepted(home):
    from core.learning.feedback_gate import DecisionLedger
    return [r for r in DecisionLedger(home / "learning").tail() if r.get("decision") == "accepted"]


def _pending(home):
    d = home / "learning" / "feedback_gate" / "pending"
    return sorted(d.glob("*.json")) if d.is_dir() else []


def test_rl1_disk_full_void_is_chained_as_void_failed_and_retried(tmp_path, monkeypatch, chain_calls):
    """ENOSPC: the store append fails, and so does every void append (same disk). The chain
    must not claim a compensation that did not happen; the admission stays retryable."""
    from core.learning import event_store as es_mod, feedback_gate

    home = tmp_path / "tenants" / "t1"
    store = EventStore(home, tenant_id="t1")
    monkeypatch.setattr(es_mod, "_append_record",
                        lambda p, l: (_ for _ in ()).throw(OSError(errno.ENOSPC, "full")))
    real = feedback_gate._append_line

    def gate_append(path, line, *, exclusive=False):
        if b'"decision":"void"' in line:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, line, exclusive=exclusive)

    monkeypatch.setattr(feedback_gate, "_append_line", gate_append)
    ev = _fb()
    with pytest.raises(OSError) as ei:
        store.write_event(ev, subject_verified=True)
    names = [c[0] for c in chain_calls]
    assert "learning.feedback_not_stored" not in names, names
    failed = [d for n, d in chain_calls if n == "learning.feedback_void_failed"]
    assert failed and failed[-1]["voided_files"] == 0 and failed[-1]["stage"] == "store", failed
    assert any("not voided" in n for n in getattr(ei.value, "__notes__", [])), ei.value
    assert len(_accepted(home)) == 1 and len(_pending(home)) == 1
    # the disk has room again: the retry voids it everywhere and only now says "not stored"
    monkeypatch.setattr(feedback_gate, "_append_line", real)
    assert feedback_gate.void_admission(ev.event_id, stage="store") is True
    assert _accepted(home) == [] and _pending(home) == []
    done = [d for n, d in chain_calls if n == "learning.feedback_not_stored"]
    assert done and done[-1]["voided_files"] == 3


def test_rl1_ledger_failure_inside_admit_chains_void_failed_when_the_void_misses(tmp_path, monkeypatch,
                                                                                 chain_calls):
    """admit()'s own compensation: decisions.jsonl took "accepted", all.jsonl refused it,
    and the void line is refused too - chained as void_failed, never as not_stored."""
    from core.learning import feedback_gate

    home = tmp_path / "tenants" / "t1"
    store = EventStore(home, tenant_id="t1")
    real = feedback_gate._append_line

    def gate_append(path, line, *, exclusive=False):
        if path.name == "all.jsonl" or b'"decision":"void"' in line:
            raise OSError(errno.ENOSPC, "full")
        return real(path, line, exclusive=exclusive)

    monkeypatch.setattr(feedback_gate, "_append_line", gate_append)
    with pytest.raises(feedback_gate.FeedbackRejected):
        feedback_gate.admit(_fb(), store, subject_verified=True)
    names = [c[0] for c in chain_calls]
    assert "learning.feedback_void_failed" in names and "learning.feedback_not_stored" not in names
    assert len(_pending(home)) == 1, "the unvoided entry must stay reconcilable"


def test_rl2_busy_lock_keeps_the_original_error_and_the_admission(tmp_path, monkeypatch, chain_calls):
    """admission(): 'Whatever the body raises propagates unchanged after the void' - also when
    the void cannot take the subject lock; the admission stays retryable."""
    from core.learning import event_store as es_mod, feedback_gate
    from core.learning.feedback_gate import DecisionLedger

    home = tmp_path / "tenants" / "t1"
    store = EventStore(home, tenant_id="t1")
    monkeypatch.setattr(es_mod, "_append_record",
                        lambda p, l: (_ for _ in ()).throw(OSError(errno.EIO, "I/O error")))
    n = {"locked": 0}
    real_locked = DecisionLedger.locked

    def locked(self, subject, timeout_s=feedback_gate.LOCK_TIMEOUT_S):
        n["locked"] += 1
        if n["locked"] == 2:  # admit() took it once; the void's take times out
            raise RuntimeError("feedback gate: subject lock busy")
        return real_locked(self, subject, timeout_s)

    monkeypatch.setattr(DecisionLedger, "locked", locked)
    ev = _fb()
    with pytest.raises(Exception) as ei:
        store.write_event(ev, subject_verified=True)
    assert isinstance(ei.value, OSError), f"body error replaced by {type(ei.value).__name__}"
    failed = [d for n_, d in chain_calls if n_ == "learning.feedback_void_failed"]
    assert failed and failed[-1]["reason"] == "lock_busy", failed
    assert feedback_gate.void_admission(ev.event_id, stage="store") is True
    assert _accepted(home) == []


def test_rl2_route_answers_503_when_the_void_does_not_land(tmp_path):
    """POST /v1/console/feedback/skill: the route's audit record fails AND the void cannot be
    written - 503 feedback_void_failed, chained, never a silent 'not stored'."""
    with _sandbox(tmp_path) as (client, home, tenant_id, _emitter, chain):
        from core.learning import feedback_gate

        mod = sys.modules.get("corvin_console.routes.stream4_skill_feedback")
        assert mod is not None, "stream4 route not mounted"
        original_emit, original_append = mod.emit_skill_audit_event, feedback_gate._append_line

        def gate_append(path, line, *, exclusive=False):
            if b'"decision":"void"' in line:
                raise OSError(errno.ENOSPC, "full")
            return original_append(path, line, exclusive=exclusive)

        mod.emit_skill_audit_event = lambda *a, **k: False
        feedback_gate._append_line = gate_append
        try:
            r = client.post("/v1/console/feedback/skill", json={
                "skill_id": "os.delegation_router", "subject_id": "task-1", "rating": 1,
                "category": "accuracy"})
        finally:
            mod.emit_skill_audit_event = original_emit
            feedback_gate._append_line = original_append
        assert r.status_code == 503, r.text
        assert r.json()["detail"]["error"] == "feedback_void_failed", r.text
        chained = [json.loads(ln).get("event_type") for ln in chain.read_text().splitlines() if ln.strip()]
        assert "learning.feedback_void_failed" in chained
        assert "learning.feedback_not_stored" not in chained
        # a success confirms the admission: no pending marker is left behind
        r = client.post("/v1/console/feedback/skill", json={
            "skill_id": "os.delegation_router", "subject_id": "task-2", "rating": 1,
            "category": "accuracy"})
        assert r.status_code == 200, r.text
        assert len(_pending(home / "tenants" / tenant_id)) == 1  # only the unvoided one


def test_rl3_crash_after_accepted_is_voided_by_the_reconcile_pass(tmp_path, chain_calls):
    """A process that dies between the gate's 'accepted' and the store write leaves a pending
    marker; after the grace window reconcile_pending voids it (chained crash_recovery)."""
    from core.learning import feedback_gate

    home = tmp_path / "tenants" / "t1"
    store = EventStore(home, tenant_id="t1")
    ev = _fb()
    feedback_gate.admit(ev, store, subject_verified=True)  # "accepted"; the write never happens
    with feedback_gate._admitted_lock:  # the process died: its in-memory admission is gone
        feedback_gate._entries.pop(ev.event_id, None)
    assert len(_accepted(home)) == 1 and len(_pending(home)) == 1
    learning = home / "learning"
    assert feedback_gate.reconcile_pending(learning, "t1") == 0, "inside the grace window"
    later = time.time() + feedback_gate.PENDING_GRACE_S + 1
    assert feedback_gate.reconcile_pending(learning, "t1", wall_now=later) == 1
    assert _accepted(home) == [] and _pending(home) == []
    done = [d for n, d in chain_calls if n == "learning.feedback_not_stored"]
    assert done and done[-1]["stage"] == "crash_recovery" and done[-1]["voided_files"] == 3
    # a stored write leaves no marker at all, and a marker whose event IS stored is only removed
    ev2 = _fb()
    store.write_event(ev2, subject_verified=True)
    assert _pending(home) == []
    entry = feedback_gate._AcceptedEntry(learning_root=learning, tenant_id="t1",
                                         subject="os.delegation_router", source_key="src_x",
                                         entry_id="e1", ts=time.time(),
                                         event_date=ev2.timestamp[:10])
    feedback_gate._write_pending(entry, ev2.event_id)
    assert feedback_gate.reconcile_pending(learning, "t1", wall_now=later) == 0
    assert _pending(home) == [] and len(_accepted(home)) == 1


def test_rl3_the_learning_host_pass_reconciles_on_start(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, emitter, chain):
        from core.background import learning_host
        from core.learning import feedback_gate

        thome = home / "tenants" / tenant_id
        ev = _fb(tenant=tenant_id)
        feedback_gate.admit(ev, emitter.store, subject_verified=True)
        with feedback_gate._admitted_lock:
            feedback_gate._entries.pop(ev.event_id, None)
        marker = _pending(thome)[0]
        rec = json.loads(marker.read_text())
        rec["created"] -= feedback_gate.PENDING_GRACE_S + 5  # the crash was a while ago
        marker.write_text(json.dumps(rec))
        host = learning_host.LearningHost(tenant_id, sources=lambda t: [], prune=lambda *a, **k: None)
        asyncio.run(host.tick(now=time.time()))  # the first tick runs the retention pass
        assert _pending(thome) == [] and _accepted(thome) == []
        recs = [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]
        assert any(r.get("event_type") == "learning.feedback_not_stored"
                   and (r.get("details") or {}).get("stage") == "crash_recovery" for r in recs)


@pytest.mark.parametrize("bad", ["acme\n", "ACME", "test", "../x", ""])
def test_rl4_skill_management_validator_is_the_canonical_rule(bad):
    from core.skill_management.tenant_validator import validate_tenant_id
    with pytest.raises(ValueError):
        validate_tenant_id(bad)
    assert validate_tenant_id("acme-corp") == "acme-corp"


@pytest.mark.parametrize("content", ['[]', '"x"', '{"grades": 1}', '{"s": []}',
                                     '{"s": {"grades": "x"}}',
                                     '{"s": {"grades": [{"grader": "operator"}]}}'])
def test_rl7_wrong_shape_grade_store_fails_closed(tmp_path, monkeypatch, content, caplog):
    from corvin_operator.context_engineering.stages import grades
    p = tmp_path / "ce_stage_grades.json"
    p.write_text(content)
    monkeypatch.setattr(grades, "_store_path", lambda t: p)
    monkeypatch.setattr(grades, "_UNREADABLE_LOGGED", set())
    with pytest.raises(grades.GradeStoreUnreadable):
        grades.grade_stage("t1", "s", 1.0, grader="operator")
    with pytest.raises(grades.GradeStoreUnreadable):
        grades.get_grade("t1", "s")
    assert p.read_text() == content, "a writer must not rewrite an unreadable store"
    with caplog.at_level(logging.WARNING):
        assert grades.is_default_eligible("t1", "s", builtin_ids=()) is False
    assert "unreadable" in caplog.text


def test_rl8_token_metrics_db_recreated_after_removal_has_its_schema(tmp_path, monkeypatch):
    import sqlite3
    import core.learning.token_metrics_db as m
    monkeypatch.setattr(m, "default_db_path", lambda tid: tmp_path / tid / "learning" / "token_metrics.db")
    db = m.TokenMetricsDB()
    with db._connect("t1") as conn:
        conn.execute("SELECT count(*) FROM token_metrics")
    db.path_for("t1").unlink()  # tenant reset / restore / operator cleanup
    with db._connect("t1") as conn:
        try:
            conn.execute("SELECT count(*) FROM token_metrics")
        except sqlite3.OperationalError as e:
            raise AssertionError(f"recreated file has no schema: {e}")


def test_rl8_console_turn_row_carries_the_consent_subject_for_art17(tmp_path, monkeypatch):
    """A real console turn stores ``user_id`` = the turn's consent subject, so an Art. 17
    request naming the user (not the chat sid) erases the row."""
    for extra in (_REPO / "corvin_operator" / "bridges" / "shared" / "tests",
                  _REPO / "corvin_operator" / "bridges" / "shared",
                  _REPO / "core" / "console" / "tests"):
        if str(extra) not in sys.path:
            sys.path.insert(0, str(extra))
    import test_chat_turn_usage_stream_e2e as tu  # the turn_usage harness

    tu._sandbox(tmp_path, monkeypatch)
    monkeypatch.setenv("HOME", str(tmp_path / "userhome"))
    import core.learning.token_measurement_hook as hook_mod
    monkeypatch.setattr(hook_mod, "_hook", None, raising=False)
    monkeypatch.setattr(hook_mod, "_autoinit_attempted", False, raising=False)
    tu._replay(tmp_path, monkeypatch, tu._stream(init_model=tu.CONFIRMED, usage=tu.USAGE))
    from corvin_console import chat_runtime as cr

    sess = cr.create_session("_default")

    async def _collect():
        evs = [ev async for ev in cr.stream_turn(sess, "say done")]
        await asyncio.sleep(0.5)  # the DB insert is a fire-and-forget task on this loop
        return evs

    asyncio.run(_collect())
    import contextlib
    import sqlite3
    db = Path(os.environ["CORVIN_HOME"]) / "tenants" / "_default" / "learning" / "token_metrics.db"
    with contextlib.closing(sqlite3.connect(db)) as conn:
        rows = conn.execute("SELECT session_id, user_id FROM token_metrics").fetchall()
    assert rows == [(sess.sid, "local-operator:_default")], rows
    from erasure_handlers import TokenMetricsHandler  # type: ignore
    res = TokenMetricsHandler(tenant_id="_default").purge("local-operator:_default", "req-rl8")
    assert res.count == 1, res


def test_rl9_count_and_query_share_one_partition_selector(store):
    ev = _ev()
    store.write_event(ev)
    line = (store.events_dir / (ev.timestamp.split("T")[0] + ".jsonl")).read_text()
    (store.events_dir / "backup.jsonl").write_text(line)  # an operator copy / restore
    assert store.count_events("t1") == len(store.query_events("t1")) == 1
    old = json.loads(line)
    old["event_id"], old["timestamp"] = "old-1", "2025-12-31T23:00:00Z"
    (store.events_dir / "2025-12-31.jsonl").write_text(json.dumps(old) + "\n")
    assert store.count_events("t1") == len(store.query_events("t1")) == 2


def test_rl10_retention_covers_every_tenant_store_and_repairs_foreign_partitions(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (_client, home, tenant_id, _emitter, chain):
        from core.background import learning_host
        from corvin_operator.context_engineering.stages import grades
        from core.learning import confidence_persistence
        from core.learning.token_metrics_db import TokenMetricsDB
        from core.skills.feedback import history_store

        now = time.time()
        old_iso = datetime.fromtimestamp(now - 200 * 86400, timezone.utc).isoformat()
        new_iso = datetime.fromtimestamp(now, timezone.utc).isoformat()
        thome = home / "tenants" / tenant_id
        for fid, ts in (("fb_old", old_iso), ("fb_new", new_iso)):
            history_store.append(tenant_id, feedback_id=fid, timestamp=ts, skill_id="s",
                                 subject_id="u", rating=1, category="accuracy")
        grades.grade_stage(tenant_id, "stage.x", 1.0, grader="operator")
        data = json.loads(grades._store_path(tenant_id).read_text())
        data["stage.x"]["grades"][0]["ts"] = now - 200 * 86400
        data["stage.x"]["grades"].append({"score": 1.0, "notes": "", "grader": "operator", "ts": now})
        grades._store_path(tenant_id).write_text(json.dumps(data))
        cpath = confidence_persistence._stats_path(tenant_id)
        cpath.parent.mkdir(parents=True, exist_ok=True)
        cpath.write_text(json.dumps({
            f"model_stats:code:m-old:{tenant_id}": {"stats": {"last_updated": old_iso}},
            f"model_stats:code:m-new:{tenant_id}": {"stats": {"last_updated": new_iso}}}))
        tdb = TokenMetricsDB()
        import contextlib
        import sqlite3
        with tdb._connect(tenant_id) as conn:
            for eid, created in (("old", "2000-01-01 00:00:00"), ("new", None)):
                conn.execute("INSERT INTO token_metrics (event_id, turn_id, session_id, tenant_id, "
                             "instance_id, created_at) VALUES (?, 't', 's', ?, 'i', "
                             + ("?" if created else "CURRENT_TIMESTAMP") + ")",
                             (eid, tenant_id, created) if created else (eid, tenant_id))
            conn.commit()
        # a pre-LRN-2 partition: other tenants' records under this tenant's directory
        events = thome / "learning" / "events"
        events.mkdir(parents=True, exist_ok=True)
        day = datetime.now(timezone.utc).date().isoformat()
        part = events / f"{day}.jsonl"
        mine = {"event_id": "m", "event_type": "outcome", "skill_id": "s", "tenant_id": tenant_id,
                "timestamp": day + "T00:00:00Z"}
        acme = dict(mine, event_id="a", tenant_id="acme")
        junk = dict(mine, event_id="j", tenant_id="../evil")
        part.write_text("".join(json.dumps(r) + "\n" for r in (mine, acme, junk)))

        host = learning_host.LearningHost(tenant_id, sources=lambda t: [], prune=lambda *a, **k: None)
        asyncio.run(host.tick(now=now))

        assert [r["feedback_id"] for r in history_store.read_all(tenant_id)] == ["fb_new"]
        assert len(json.loads(grades._store_path(tenant_id).read_text())["stage.x"]["grades"]) == 1
        assert list(json.loads(cpath.read_text())) == [f"model_stats:code:m-new:{tenant_id}"]
        with contextlib.closing(sqlite3.connect(tdb.path_for(tenant_id))) as conn:
            assert [r[0] for r in conn.execute("SELECT event_id FROM token_metrics")] == ["new"]
        assert [json.loads(ln)["event_id"] for ln in part.read_text().splitlines()] == ["m"]
        moved = home / "tenants" / "acme" / "learning" / "events" / f"{day}.jsonl"
        assert [json.loads(ln)["event_id"] for ln in moved.read_text().splitlines()] == ["a"]
        status = host.status()["retention"]
        assert status["skill_feedback_removed"] == 1 and status["token_metrics_removed"] == 1
        assert status["ce_stage_grades_removed"] == 1 and status["model_confidence_removed"] == 1
        assert status["last_error"] == ""
        recs = [json.loads(ln) for ln in chain.read_text().splitlines() if ln.strip()]
        repaired = [r for r in recs if r.get("event_type") == "learning.cross_tenant_records_repaired"]
        assert len(repaired) == 1 and repaired[0]["details"]["moved_count"] == 1
        assert repaired[0]["details"]["removed_count"] == 1
        assert repaired[0]["details"]["foreign_tenants"] == ["acme"]
        # the next pass finds nothing foreign: no repeated alert for the same records
        from core.learning.event_persistence import EventStore as PStore
        assert PStore(tenant_id).repair_foreign_records(tenant_id=tenant_id)["partitions"] == 0
        assert not [r for r in recs if r.get("event_type") == "learning.cross_tenant_contamination_detected"]


def _rl11_appender(root: str, n: int) -> None:
    from core.learning import feedback_gate
    led = feedback_gate.DecisionLedger(Path(root))
    for i in range(n):
        with led.locked("os.delegation_router"):
            led.append(ts=time.time(), subject="os.delegation_router", decision="accepted",
                       reason="valid", source_key="src_keep")


def _rl11_rewriter(root: str, n: int) -> None:
    from core.learning import feedback_gate
    feedback_gate._audit = lambda *a, **k: "ref"
    led = feedback_gate.DecisionLedger(Path(root))
    for _ in range(n):
        with led.locked("os.other"):
            led.append(ts=1.0, subject="os.other", decision="accepted", reason="valid",
                       source_key="src_old")  # expired at once: every prune rewrites the file
        feedback_gate.prune_ledgers(Path(root).parent, "t1", retention_days=1)


def test_rl11_decisions_lock_keeps_appends_across_a_concurrent_rewrite(tmp_path):
    """Two PROCESSES: one appends to decisions.jsonl while the other rewrites it (retention).
    Without the decisions lock a line appended to the replaced inode is lost."""
    root = tmp_path / "tenants" / "t1" / "learning"
    root.mkdir(parents=True)
    ctx = _mp.get_context("fork")
    n = 150
    procs = [ctx.Process(target=_rl11_appender, args=(str(root), n)),
             ctx.Process(target=_rl11_rewriter, args=(str(root), n))]
    for p in procs:
        p.start()
    for p in procs:
        p.join(120)
    assert all(p.exitcode == 0 for p in procs), [p.exitcode for p in procs]
    from core.learning.feedback_gate import DecisionLedger
    kept = [r for r in DecisionLedger(root).tail(10 * n) if r.get("source_key") == "src_keep"]
    assert len(kept) == n, f"{n - len(kept)} appended decisions lost to a concurrent rewrite"


def test_rl1_void_records_are_registered_with_their_fields(tmp_path):
    """Through the REAL chain writer: the new compensation fields survive the allowlist floor."""
    from forge import security_events

    chain = tmp_path / "audit.jsonl"
    _tid = os.environ.get("CORVIN_TENANT_ID") or "_default"
    base = {"tenant_id": _tid, "skill_id": "s", "feedback_event_id": "e", "source_key": "src_x",
            "stage": "store", "error_type": "OSError", "voided_files": 1}
    security_events.write_event(chain, "learning.feedback_void_failed",
                                details={**base, "reason": "ledger_append_failed"})
    security_events.write_event(chain, "learning.feedback_not_stored", details={**base, "voided_files": 3})
    security_events.write_event(chain, "learning.cross_tenant_records_repaired", details={
        "tenant_id": _tid, "file": "2026-11-01.jsonl", "moved_count": 2, "removed_count": 1,
        "foreign_tenants": ["acme"]})
    failed, done, repaired = [json.loads(ln) for ln in chain.read_text().splitlines()]
    assert failed["severity"] == "ERROR" and failed["details"]["voided_files"] == 1
    assert failed["details"]["reason"] == "ledger_append_failed"
    assert done["details"]["voided_files"] == 3
    assert repaired["severity"] == "WARNING" and repaired["details"]["moved_count"] == 2
    for rec in (failed, done, repaired):
        assert "_dropped_fields" not in rec["details"], rec
