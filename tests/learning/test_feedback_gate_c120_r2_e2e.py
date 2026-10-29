"""ADR-0534 feedback trust gate — regressions of review c120 round 2 (r2_s1-8, r2_s1-9).

Every case goes through the REAL console route ``POST /v1/console/learning/feedback``
with a real session (the sandbox of ``test_learning_loop_routes_e2e``) and asserts on
what the store, the gate ledger and the core chain contain.

* r2_s1-8  a producer's own rate-limit refusals are not "rejected" in its trust history
* r2_s1-9  a ledger append that fails after ``learning.feedback_accepted`` committed
           chains ``learning.feedback_not_stored`` and refuses the feedback; a failing
           throttle index never opens the budget; a torn index line is skipped
"""
from __future__ import annotations

import errno
import json
import sys
import time
from pathlib import Path

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console" / "tests"))
from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

FEEDBACK = "/v1/console/learning/feedback"
ROUTER = "os.delegation_router"


def _post(client, task: str):
    return client.post(FEEDBACK, json={"task_id": task, "outcome_quality": "excellent",
                                       "would_repeat": True})


def _named(home: Path, name: str) -> list[dict]:
    out = []
    for f in home.rglob("audit.jsonl"):
        out.extend(r for r in (json.loads(x) for x in f.read_text().splitlines() if x.strip())
                   if r.get("event_type") == name)
    return out


def _stored_feedback(home: Path, tenant_id: str) -> list[dict]:
    out = []
    for f in sorted((home / "tenants" / tenant_id / "learning" / "events").glob("*.jsonl")):
        out.extend(e for e in (json.loads(x) for x in f.read_text().splitlines() if x.strip())
                   if e.get("event_type") == "feedback")
    return out


def _fail_append(monkeypatch, name: str, state: dict):
    """Make the gate's ledger append to the file ``name`` fail with ENOSPC while
    ``state['fail']`` holds (signature-faithful, so the real code path runs)."""
    import core.learning.feedback_gate as fg
    real = fg._append_line

    def bad(path, line, **kw):
        if state["fail"] and Path(path).name == name:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, line, **kw)
    monkeypatch.setattr(fg, "_append_line", bad)


# ── r2_s1-8 ────────────────────────────────────────────────────────────────

def test_rate_limit_refusals_do_not_lower_the_producers_trust(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (client, home, _tenant_id, _e, _c):
        real = time.time
        shift = {"s": 0.0}
        monkeypatch.setattr(time, "time", lambda: real() + shift["s"])
        weights = []
        for burst in range(3):
            codes = []
            for i in range(9):
                r = _post(client, f"b{burst}-{i}")
                codes.append(r.status_code)
                if r.status_code == 200:
                    weights.append(r.json()["trust_weight"])
                else:
                    assert r.json()["detail"]["reason"] == "rate_limit_exceeded", r.text
            assert codes == [200] * 5 + [422] * 4, codes
            shift["s"] += 600.0            # the next burst is outside the throttle window
        r = _post(client, "next")
        assert r.status_code == 200, r.text
        assert r.json()["trust_weight"] == 1.0
        assert set(weights) == {1.0}, weights
        assert _named(home, "learning.adversarial_feedback_detected") == []


# ── r2_s1-9 ────────────────────────────────────────────────────────────────

def test_failed_global_ledger_append_chains_not_stored_and_refuses(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        _fail_append(monkeypatch, "decisions.jsonl", {"fail": True})
        r = _post(client, "t0")
        assert r.status_code == 422, r.text
        assert r.json()["detail"]["reason"] == "ledger_write_failed"
        acc = _named(home, "learning.feedback_accepted")
        nst = _named(home, "learning.feedback_not_stored")
        assert len(acc) == 1 and len(nst) == 1, (acc, nst)
        d = nst[0]["details"]
        assert d["feedback_event_id"] == acc[0]["details"]["feedback_event_id"]
        assert d["stage"] == "ledger" and d["error_type"] == "OSError"
        assert "No space" not in json.dumps(d)          # error class only, never its text
        assert _stored_feedback(home, tenant_id) == []


def test_failing_throttle_index_never_opens_the_budget(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        state = {"fail": True}
        _fail_append(monkeypatch, "all.jsonl", state)
        first = [_post(client, f"f{i}") for i in range(8)]
        assert [r.status_code for r in first] == [422] * 8
        assert {r.json()["detail"]["reason"] for r in first} == {"ledger_write_failed"}
        state["fail"] = False
        codes = [_post(client, f"a{i}").status_code for i in range(6)]
        assert codes == [200] * 5 + [422], codes
        stored = _stored_feedback(home, tenant_id)
        acc = _named(home, "learning.feedback_accepted")
        nst = _named(home, "learning.feedback_not_stored")
        assert len(stored) == 5
        # every "accepted" without a stored event is compensated in the chain
        assert len(acc) - len(nst) == len(stored), (len(acc), len(nst), len(stored))


def test_a_torn_index_line_never_admits_more_than_the_budget(tmp_path):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        import core.learning.feedback_gate as fg
        led = fg.DecisionLedger(home / "tenants" / tenant_id / "learning")
        d = led._subject_dir(ROUTER)
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "all.jsonl", "ab") as fh:
            fh.write(b'{"ts": 17')            # a short write left by an earlier full disk
        codes = [_post(client, f"t{i}").status_code for i in range(7)]
        assert codes == [200] * 5 + [422] * 2, codes
        assert len(_stored_feedback(home, tenant_id)) == 5
