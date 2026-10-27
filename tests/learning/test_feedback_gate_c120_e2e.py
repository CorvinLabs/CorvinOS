"""ADR-0534 feedback trust gate — regressions of review c120 (s1_feedback-3..7, P2).

Every case goes through the REAL console routes (``POST /v1/console/learning/feedback``,
``POST /v1/console/feedback/skill``, ``POST /v1/console/skills/{id}/rating``) with real
sessions, or through ``EventStore.write_event`` — the primitive every other producer
calls — and asserts on what the store, the gate ledger and the core chain contain.

* s1-3  the trust weight scales the optimizer step of /learning/feedback
* s1-4  admission is atomic across threads (HTTP) and processes (write_event)
* s1-5  junk on other subjects cannot flush a subject's throttle or trust history
* s1-6  ``skill:X`` and ``X`` share one rate budget
* s1-7  trust is per producer: a bad session does not lower an honest one
* P2    /feedback/skill is consent-gated (403, audited, nothing recorded)
"""
from __future__ import annotations

import json
import multiprocessing
import os
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console" / "tests"))
from test_learning_loop_routes_e2e import _sandbox, _seed_router_execution  # noqa: E402

from tests.learning.console_client import console_client  # noqa: E402

ROUTER = "os.delegation_router"
FEEDBACK = "/v1/console/learning/feedback"
STREAM4 = "/v1/console/feedback/skill"


def _post_feedback(client, task: str = "task-1"):
    return client.post(FEEDBACK, json={"task_id": task, "outcome_quality": "excellent",
                                       "would_repeat": True})


def _chain(home: Path, name: str) -> list[dict]:
    chain = home / "audit.jsonl"
    if not chain.exists():
        return []
    recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
    return [r for r in recs if r.get("event_type") == name]


def _all_chain_records(home: Path) -> list[dict]:
    out = []
    for f in home.rglob("audit.jsonl"):
        out.extend(json.loads(line) for line in f.read_text().splitlines() if line.strip())
    return out


def _named(home: Path, name: str) -> list[dict]:
    return [r for r in _all_chain_records(home) if r.get("event_type") == name]


@contextmanager
def _as_session(client, tenant_id: str, token_fp: str):
    """Swap the client onto a second real session (its own consent subject = producer)."""
    from corvin_console import auth as _auth
    from core.compliance import consent_store as _consent_store

    rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint=token_fp)
    _consent_store.get_consent_store(tenant_id).grant_consent(
        user_id=rec.sid_fingerprint, scope="learning_feedback")
    old_sid = client.cookies.get("corvin_console_sid")
    old_csrf = client.headers.get("X-CSRF-Token")
    client.cookies.set("corvin_console_sid", rec.sid)
    client.headers["X-CSRF-Token"] = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
    try:
        yield rec
    finally:
        client.cookies.set("corvin_console_sid", old_sid)
        client.headers["X-CSRF-Token"] = old_csrf


def _prime_optimizer(tenant_id: str, monkeypatch):
    """The epoch accepts a hypothesis (as the attack probe drove it): baseline 0, 10/10 outcomes."""
    from core.skills.os_skills.skill_adapter import SkillAdapter
    from corvin_console.routes import method_discovery_api as mda

    ad = SkillAdapter(ROUTER, tenant_id)
    ad.state.epoch = 50
    ad.state.baseline_success_rate = 0.0
    ad._persist()
    monkeypatch.setattr(mda, "recent_outcomes", lambda *a, **k: (10, 10))
    return ad.get_current_config().to_dict()["confidence_threshold"]


def _junk_flood(tenant_home: Path, tenant_id: str, n: int = 520) -> set:
    """``n`` rejected feedback signals on OTHER subjects, through the production primitive."""
    from core.learning.event_store import EventStore
    from core.learning.feedback_gate import FeedbackRejected
    from core.learning.learning_events import EventType, LearningEvent

    st = EventStore(tenant_home, tenant_id=tenant_id)
    reasons = set()
    for i in range(n):
        try:
            st.write_event(LearningEvent.create(EventType.FEEDBACK, f"tool:junk{i}", tenant_id,
                                                signal={"k": "v"}))
        except FeedbackRejected as e:
            reasons.add(e.reason)
    return reasons


# ── s1-3 + s1-5 (trust part) ────────────────────────────────────────────────

def test_trust_weight_scales_the_optimizer_step_and_survives_a_junk_flood(tmp_path, monkeypatch):
    with _sandbox(tmp_path, seed_execution=False) as (client, home, tenant_id, emitter, _c):
        # 4 rejected signals from THIS session (no execution to verify them yet)
        for i in range(4):
            r = _post_feedback(client, f"t{i}")
            assert r.status_code == 422, r.text
            assert r.json()["detail"]["reason"] == "no_corresponding_execution"
        # 520 junk signals on other subjects must not launder this producer's history
        assert _junk_flood(home / "tenants" / tenant_id, tenant_id) == {"subject_not_verifiable"}
        _seed_router_execution(emitter, tenant_id)
        before = _prime_optimizer(tenant_id, monkeypatch)

        r = _post_feedback(client, "t-final")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["trust_weight"] == pytest.approx(0.1)  # max(0.1, 1 - 4/4), prior history only
        hyp = [h for h in body["hypotheses"] if h["param"] == "confidence_threshold"]
        assert hyp and hyp[0]["accepted"], body["hypotheses"]
        assert hyp[0]["delta"] == pytest.approx(0.05 * 0.1)
        from core.skills.os_skills.skill_adapter import SkillAdapter
        after = SkillAdapter(ROUTER, tenant_id).get_current_config().to_dict()["confidence_threshold"]
        assert after - before == pytest.approx(0.005), (before, after)
        flagged = _named(home, "learning.adversarial_feedback_detected")
        assert flagged and flagged[-1]["details"]["rejection_count"] == 4
        assert flagged[-1]["details"]["source_key"].startswith("src_")


# ── s1-5 (throttle part) ───────────────────────────────────────────────────

def test_junk_on_other_subjects_does_not_reopen_the_throttle(tmp_path):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        codes = [_post_feedback(client, f"t{i}").status_code for i in range(6)]
        assert codes == [200] * 5 + [422], codes
        assert _junk_flood(home / "tenants" / tenant_id, tenant_id) == {"subject_not_verifiable"}
        r = _post_feedback(client, "after-flood")
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["reason"] == "rate_limit_exceeded"


# ── s1-4 ───────────────────────────────────────────────────────────────────

def test_concurrent_http_burst_is_throttled_to_the_window(tmp_path):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        barrier = threading.Barrier(20)
        codes: list[int] = []
        lock = threading.Lock()

        def go(i):
            barrier.wait()
            c = _post_feedback(client, f"t{i}").status_code
            with lock:
                codes.append(c)

        ts = [threading.Thread(target=go, args=(i,)) for i in range(20)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        assert codes.count(200) == 5, sorted(codes)
        assert codes.count(422) == 15, sorted(codes)
        fb = [e for e in _events(home, tenant_id) if e["event_type"] == "feedback"]
        assert len(fb) == 5


def _events(home: Path, tenant_id: str) -> list[dict]:
    out = []
    for f in sorted((home / "tenants" / tenant_id / "learning" / "events").glob("*.jsonl")):
        out.extend(json.loads(line) for line in f.read_text().splitlines() if line.strip())
    return out


def _child(tenant_home: str, tenant_id: str, barrier, q) -> None:
    from core.learning.event_store import EventStore
    from core.learning.feedback_gate import FeedbackRejected
    from core.learning.learning_events import EventType, LearningEvent

    st = EventStore(Path(tenant_home), tenant_id=tenant_id)
    ev = LearningEvent.create(EventType.FEEDBACK, ROUTER, tenant_id, signal={"k": "v"},
                              lom="tests/learning/test_feedback_gate_c120_e2e.py:_child")
    barrier.wait()
    try:
        st.write_event(ev)
        q.put("accepted")
    except FeedbackRejected as e:
        q.put(e.reason)
    except Exception as e:  # noqa: BLE001
        q.put(f"error:{type(e).__name__}")


def test_concurrent_processes_are_throttled_to_the_window(tmp_path):
    with _sandbox(tmp_path) as (_client, home, tenant_id, _e, _c):
        ctx = multiprocessing.get_context("fork")
        n = 12
        barrier, q = ctx.Barrier(n), ctx.Queue()
        th = str(home / "tenants" / tenant_id)
        procs = [ctx.Process(target=_child, args=(th, tenant_id, barrier, q)) for _ in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join(60)
        out = [q.get(timeout=5) for _ in range(n)]
        assert out.count("accepted") == 5, out
        assert out.count("rate_limit_exceeded") == n - 5, out


# ── s1-6 ───────────────────────────────────────────────────────────────────

def _seed_execution_sb(sb, skill_id: str) -> None:
    from core.learning.event_store import EventStore
    from core.learning.learning_events import EventType, LearningEvent
    EventStore(sb.tenant_home, tenant_id=sb.tenant_id).write_event(LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED, skill_id=skill_id, tenant_id=sb.tenant_id,
        signal={"status": "success", "output": {"decision": "allow"}},
        lom="core/skills/skill_registry_phase1.py:execute"))


def test_skill_prefix_and_bare_id_share_one_rate_budget(tmp_path):
    with console_client(tmp_path) as sb:
        _seed_execution_sb(sb, "os.flow_guard")
        # operator ratings record the subject as "skill:os.flow_guard"
        codes = [sb.client.post("/v1/console/skills/os.flow_guard/rating", json={"rating": 4},
                                headers=sb.csrf_headers).status_code for _ in range(5)]
        assert codes == [200] * 5, codes
        # stream4 records it as the bare "os.flow_guard": the same skill, the same window
        r = sb.client.post(STREAM4, json={"skill_id": "os.flow_guard", "subject_id": "task-1",
                                          "rating": 2, "category": "accuracy"},
                           headers=sb.csrf_headers)
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["reason"] == "rate_limit_exceeded"
        thr = [c for c in sb.chain_records() if c.get("event_type") == "learning.feedback_throttled"]
        assert thr and thr[-1]["details"]["skill_id"] == "os.flow_guard"


# ── s1-7 ───────────────────────────────────────────────────────────────────

def test_a_bad_producer_does_not_lower_an_honest_producers_weight(tmp_path):
    with _sandbox(tmp_path, seed_execution=False) as (client, home, tenant_id, emitter, _c):
        for i in range(4):  # session A: four rejected signals
            assert _post_feedback(client, f"a{i}").status_code == 422
        _seed_router_execution(emitter, tenant_id)
        n_flagged = len(_named(home, "learning.adversarial_feedback_detected"))
        with _as_session(client, tenant_id, "fp-honest-b"):
            r = _post_feedback(client, "b0")
        assert r.status_code == 200, r.text
        assert r.json()["trust_weight"] == 1.0
        assert len(_named(home, "learning.adversarial_feedback_detected")) == n_flagged
        fb = [e for e in _events(home, tenant_id) if e["event_type"] == "feedback"]
        assert [e["signal"]["trust_weight"] for e in fb] == [1.0]
        assert all("producer" not in e["signal"] for e in fb)  # the raw principal is never stored


# ── P2 ─────────────────────────────────────────────────────────────────────

def test_stream4_feedback_requires_learning_feedback_consent(tmp_path):
    with console_client(tmp_path) as sb:
        _seed_execution_sb(sb, "os.flow_guard")
        from core.compliance import consent_store as _consent_store
        from corvin_console import auth as _auth

        rec = _auth.create_session(tenant_id=sb.tenant_id, token_fingerprint="fp-no-consent")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        assert not _consent_store.get_consent_store(sb.tenant_id).get_consent(
            user_id=rec.sid_fingerprint, scope="learning_feedback")
        sb.client.cookies.set("corvin_console_sid", rec.sid)
        r = sb.client.post(STREAM4, json={"skill_id": "os.flow_guard", "subject_id": "task-1",
                                          "rating": 2, "category": "accuracy"},
                           headers={"x-csrf-token": csrf})
        assert r.status_code == 403, r.text
        assert [e for e in sb.events_on_disk() if e["event_type"] == "feedback"] == []
        recs = sb.chain_records()
        assert not [c for c in recs if str(c.get("event_type", "")).startswith("learning.feedback_")]
        assert not [c for c in recs if c.get("event_type") == "skill_feedback"]
        denied = [c for c in _all_chain_records(sb.home)
                  if "consent_required" in json.dumps(c.get("details", {}))]
        assert denied, "the consent denial must be audited"
        ledger = sb.tenant_home / "learning" / "feedback_gate" / "decisions.jsonl"
        assert not ledger.exists()
