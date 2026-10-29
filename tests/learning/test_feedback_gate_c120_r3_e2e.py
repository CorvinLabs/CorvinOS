"""ADR-0534 feedback trust gate — regressions of review c120 round 3 (r3_s1-6, r3_s1-7).

Every case goes through the REAL console route ``POST /v1/console/learning/feedback``
with a real session (the sandbox of ``test_learning_loop_routes_e2e``).

* r3_s1-6  decisions.jsonl is written FIRST; when a later per-subject append fails
           after ``learning.feedback_accepted`` committed, the feedback is refused,
           ``learning.feedback_not_stored`` is chained and every file that took the
           "accepted" line gets a ``void`` line: the throttle index, the producer's
           trust history and the anomaly monitor's ledger never count it.
* r3_s1-7  a REAL short write (part of the line reaches the file, then ENOSPC) to a
           per-subject file is cut back to the previous size.
"""
from __future__ import annotations

import errno
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(_REPO / "core" / "console" / "tests"))
from test_learning_loop_routes_e2e import _sandbox  # noqa: E402

FEEDBACK = "/v1/console/learning/feedback"
ROUTER = "os.delegation_router"

pytestmark = pytest.mark.skipif(not os.path.isdir("/proc/self/fd"),
                                reason="the short-write fault resolves fds via /proc")


def _post(client, task: str):
    return client.post(FEEDBACK, json={"task_id": task, "outcome_quality": "excellent",
                                       "would_repeat": True})


def _named(home: Path, name: str) -> list[dict]:
    out = []
    for f in home.rglob("audit.jsonl"):
        out.extend(r for r in (json.loads(x) for x in f.read_text().splitlines() if x.strip())
                   if r.get("event_type") == name)
    return out


def _ledger(home: Path, tenant_id: str):
    import core.learning.feedback_gate as fg
    return fg.DecisionLedger(home / "tenants" / tenant_id / "learning")


def _producer_files(led) -> list[Path]:
    d = led._subject_dir(ROUTER)
    return sorted(p for p in d.glob("*.jsonl") if p.name != "all.jsonl") if d.exists() else []


def _short_write(monkeypatch, target, state: dict) -> None:
    """``os.write`` to a file selected by ``target(path)`` writes HALF the buffer for
    real, then fails with ENOSPC (a disk that filled mid-record), while
    ``state['fail']`` holds. Every other write is untouched."""
    real = os.write

    def write(fd, data):
        if state["fail"]:
            try:
                path = os.readlink(f"/proc/self/fd/{fd}")
            except OSError:
                path = ""
            if path and target(Path(path)):
                half = max(1, len(data) // 2)
                real(fd, bytes(data[:half]))
                state["hits"] = state.get("hits", 0) + 1
                raise OSError(errno.ENOSPC, "No space left on device")
        return real(fd, data)
    monkeypatch.setattr(os, "write", write)


def _size(p: Path) -> int:
    return p.stat().st_size if p.exists() else 0


def test_short_write_to_the_throttle_index_is_cut_back_and_voided(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        assert _post(client, "warm").status_code == 200           # files exist, one accepted
        led = _ledger(home, tenant_id)
        idx = led._subject_dir(ROUTER) / "all.jsonl"
        before = _size(idx)
        state = {"fail": True}
        _short_write(monkeypatch, lambda p: p.name == "all.jsonl", state)
        r = _post(client, "t0")
        state["fail"] = False
        assert state.get("hits") == 1                             # the fault really fired
        assert r.status_code == 422 and r.json()["detail"]["reason"] == "ledger_write_failed"
        assert _size(idx) == before                               # r3_s1-7: cut back
        assert len(_named(home, "learning.feedback_not_stored")) == 1
        # r3_s1-6: decisions.jsonl took it first, then got the void: not counted
        assert [d["decision"] for d in led.tail()] == ["accepted"]
        assert [d["decision"] for d in led.subject_history(ROUTER)] == ["accepted"]


def test_short_write_to_the_producer_file_is_cut_back_and_voided(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        assert _post(client, "warm").status_code == 200
        led = _ledger(home, tenant_id)
        [prod] = _producer_files(led)
        before = _size(prod)
        state = {"fail": True}
        _short_write(monkeypatch, lambda p: p == prod, state)
        r = _post(client, "t0")
        state["fail"] = False
        assert state.get("hits") == 1
        assert r.status_code == 422 and r.json()["detail"]["reason"] == "ledger_write_failed"
        assert _size(prod) == before                              # r3_s1-7: cut back
        src = prod.stem
        assert [d["decision"] for d in led.source_history(ROUTER, src)] == ["accepted"]
        # all.jsonl and decisions.jsonl took the "accepted" line and its void
        assert [d["decision"] for d in led.subject_history(ROUTER)] == ["accepted"]
        assert [d["decision"] for d in led.tail()] == ["accepted"]


def test_refused_feedback_never_lifts_the_producers_trust(tmp_path, monkeypatch):
    """r3_s1-6 (the review's gate_drift repro): three refusals after a failed producer
    append leave the index, the producer file and decisions.jsonl without an
    'accepted' — the trust assessment sees no history, the monitor sees none."""
    import core.learning.adversarial_detector as ad
    import core.learning.feedback_gate as fg
    with _sandbox(tmp_path) as (client, home, tenant_id, _e, _c):
        real = fg._append_line
        state = {"fail": True}

        def bad(path, line, **kw):
            p = Path(path)
            if (state["fail"] and p.parent.parent.name == "subjects"
                    and p.name != "all.jsonl" and b'"accepted"' in line):
                raise OSError(errno.ENOSPC, "No space left on device")
            return real(path, line, **kw)
        monkeypatch.setattr(fg, "_append_line", bad)
        codes = [_post(client, f"f{i}").status_code for i in range(3)]
        assert codes == [422] * 3, codes
        led = _ledger(home, tenant_id)
        assert led.subject_history(ROUTER) == []
        assert led.tail() == []
        for prod in _producer_files(led):
            assert led.source_history(ROUTER, prod.stem) == []
        assert ad.assess(led.subject_history(ROUTER), subject=ROUTER).accepted_count == 0
        assert len(_named(home, "learning.feedback_not_stored")) == 3
        state["fail"] = False
        assert _post(client, "ok").status_code == 200             # positive control
        assert [d["decision"] for d in led.subject_history(ROUTER)] == ["accepted"]
