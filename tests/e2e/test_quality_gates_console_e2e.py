"""Quality Gates console — real runs over real artifacts (ADR-0688 amendment).

Over HTTP (TestClient), session overridden, against a TEMP ``CORVIN_HOME``
(the DuckDB event store lands there) and a TEMP Corvin-ADR root
(``CORVIN_ADR_ROOT``) with a handful of markdown artifacts whose verdicts are
known. Until 2026-09-20 "Run All Gates" wrote one hard-coded ``pass`` per gate
and the page fetched paths that 404ed; every assertion below was red then.

1. status: per-gate 24h/7d counts with a pass share that is ``null`` (not 0)
   for a gate nobody ran;
2. run/all: a job that judges every artifact of the ADR root with the REAL
   validators, records hash-chained gate_events, reports progress to 100;
3. trend: one point per day with events; failures: fail/warn rows with the
   validator's own reason;
4. a foreign tenant's run is 403; no ADR root → a failed run that says so.
"""
from __future__ import annotations

import dataclasses
import time
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import deps as console_deps


def _fake_session_record(tenant_id: str) -> session_auth.SessionRecord:
    now = 1_000_000.0
    values: dict[str, object] = {}
    for f in dataclasses.fields(session_auth.SessionRecord):
        if f.default is not dataclasses.MISSING:
            continue
        ann = str(f.type)
        if "float" in ann:
            values[f.name] = now + (3600 if f.name == "expires_at" else 0)
        elif "bool" in ann:
            values[f.name] = False
        elif f.name == "tier":
            tier = getattr(session_auth, "Tier", None)
            values[f.name] = next(iter(tier)) if tier else "owner"
        elif f.name == "tenant_id":
            values[f.name] = tenant_id
        else:
            values[f.name] = f"test-{f.name}"
    return session_auth.SessionRecord(**values)  # type: ignore[arg-type]


GOOD_ADR = """---
id: ADR-9001
status: accepted
depends_on: []
paths:
  - core/x.py
docs:
  - docs/x.md
commits: [abc1234]
---
# ADR-9001
Body.
"""
BAD_ADR = """---
id: ADR-9002
status: proposed
depends_on: []
paths: []
docs: []
commits: []
---
# ADR-9002 — no paths, no docs, no commits
"""
GOOD_PLAN = """---
id: PLAN-9001
---
# Plan
## Phase 1: Foundation (Weeks 1-2)
Effort: 3 person-days.
## Phase 2: Wiring
## Phase 3: Proof
## Success criteria
Everything green.
"""
CONCEPT = """---
id: CONCEPT-9001
commits: []
---
# Concept
Short narrative.
"""


@pytest.fixture
def adr_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "Corvin-ADR"
    (root / "decisions").mkdir(parents=True)
    (root / "concepts").mkdir()
    (root / "implementation-plans").mkdir()
    (root / "ideas").mkdir()
    (root / "decisions" / "ADR-9001-good.md").write_text(GOOD_ADR)
    (root / "decisions" / "ADR-9002-bad.md").write_text(BAD_ADR)
    (root / "implementation-plans" / "PLAN-9001.md").write_text(GOOD_PLAN)
    (root / "concepts" / "CONCEPT-9001.md").write_text(CONCEPT)
    monkeypatch.setenv("CORVIN_ADR_ROOT", str(root))
    return root


@pytest.fixture
def home(tmp_path, monkeypatch) -> Path:
    h = tmp_path / "corvin-home"
    (h / "tenants" / "_default" / "global").mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(h))
    return h


@pytest.fixture
def client(home, monkeypatch):
    from core.console.corvin_console.routes import quality_gates as qg

    # the route resolves the DuckDB path through forge paths → CORVIN_HOME
    monkeypatch.setattr(qg._forge_paths, "corvin_home", lambda: home, raising=False)
    qg._run_cache.clear()
    app = FastAPI()
    app.include_router(qg.router, prefix="/v1/console")
    app.dependency_overrides[console_deps.require_session] = lambda: _fake_session_record("_default")
    app.dependency_overrides[console_deps.require_csrf] = lambda: _fake_session_record("_default")
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


BASE = "/v1/console/api/quality/gates"


def _wait(client, run_id: str) -> dict:
    for _ in range(300):
        r = client.get(f"{BASE}/results/{run_id}").json()
        if r["status"] in ("completed", "failed"):
            return r
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_status_before_any_run_has_null_pass_shares_not_zeros(client, adr_root):
    body = client.get(f"{BASE}/status").json()
    assert body["gates_total"] == 4 and body["events_24h"] == 0
    for gate, gs in body["summary"].items():
        assert gs["last_24h"]["total"] == 0 and gs["last_24h"]["pass_percentage"] is None, gate
    assert body["source_root"] == str(adr_root)
    assert client.get(f"{BASE}/trend").json()["points"] == []
    assert client.get(f"{BASE}/failures").json()["failures"] == []


def test_run_all_judges_the_real_artifacts_and_records_events(client, adr_root, home):
    r = client.post(f"{BASE}/run/all")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    result = _wait(client, run_id)
    assert result["status"] == "completed", result
    assert result["progress"] == 100 and result["artifacts_total"] == 4 and result["artifacts_done"] == 4
    by_gate = {g["gate_name"]: g for g in result["gates_results"]}
    assert by_gate["ADRGate"] == {"gate_name": "ADRGate", "artifacts": 2, "pass": 1, "warn": 0, "fail": 1}
    assert by_gate["ImplementationPlanGate"]["pass"] == 1
    assert by_gate["ConceptGate"]["fail"] == 1
    assert by_gate["IdeaGate"]["artifacts"] == 0

    # the event store holds one hash-chained row per judged artifact
    assert (home / "tenants" / "_default" / "global" / "quality_gates.db").exists()
    status = client.get(f"{BASE}/status").json()
    assert status["events_24h"] == 4 and status["events_total"] == 4
    adr = status["summary"]["ADRGate"]["last_24h"]
    assert adr["total"] == 2 and adr["pass"] == 1 and adr["fail"] == 1 and adr["pass_percentage"] == 50.0
    assert status["summary"]["IdeaGate"]["last_24h"]["pass_percentage"] is None
    assert status["last_run"]["run_id"] == run_id and status["last_run"]["status"] == "completed"

    trend = client.get(f"{BASE}/trend").json()["points"]
    assert len(trend) == 1 and trend[0]["total"] == 4 and trend[0]["pass_percentage"] == 50.0

    failures = client.get(f"{BASE}/failures").json()
    assert failures["total"] == 2
    reasons = {f["artifact_id"]: f for f in failures["failures"]}
    assert reasons["ADR-9002-bad"]["gate_name"] == "ADRGate" and reasons["ADR-9002-bad"]["findings_count"] >= 1
    assert reasons["ADR-9002-bad"]["reason"] == "ADR frontmatter incomplete"
    assert reasons["CONCEPT-9001"]["artifact_type"] == "Concept"

    # the artifact's own history lists the verdict — keyed on the FILE stem,
    # because 77 live decisions declare the placeholder id ADR-0000
    hist = client.get(f"{BASE}/history/ADR-9002-bad").json()
    assert hist["pagination"]["total"] == 1 and hist["events"][0]["verdict"] == "fail"

    # the audit chain verifies
    from core.quality_gates.audit import QualityGateAuditLogger
    from core.quality_gates.graph import KnowledgeGraph

    g = KnowledgeGraph(str(home / "tenants" / "_default" / "global" / "quality_gates.db"), "_default")
    assert QualityGateAuditLogger(g.conn).verify_chain("_default") is True
    g.close()


def test_a_second_run_appends_never_rewrites(client, adr_root):
    _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])
    _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])
    assert client.get(f"{BASE}/status").json()["events_total"] == 8


def test_no_adr_root_is_a_failed_run_that_says_so(client, monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_ADR_ROOT", str(tmp_path / "nowhere"))
    from core.quality_gates import artifacts

    monkeypatch.setattr(artifacts, "resolve_adr_root", lambda: None)
    from core.console.corvin_console.routes import quality_gates as qg

    monkeypatch.setattr(qg, "resolve_adr_root", lambda: None)
    r = client.post(f"{BASE}/run/all").json()
    assert r["status"] == "failed" and "no Corvin-ADR checkout" in r["error"]


def test_a_foreign_tenants_run_is_403(client, adr_root):
    run_id = client.post(f"{BASE}/run/all").json()["run_id"]
    _wait(client, run_id)
    from core.console.corvin_console.routes import quality_gates as qg

    with qg._runs_lock:
        qg._run_cache[run_id]["tenant_id"] = "other"
    assert client.get(f"{BASE}/results/{run_id}").status_code == 403


def test_current_state_survives_the_24h_window_and_counts_each_artifact_once(client, adr_root, home):
    """The page reads the CURRENT verdict per artifact, not a 24h window.

    Live 2026-09-22: the last run was 3 days old, every tile read 0, every gate
    "not run" and the failure list was empty — over 2 184 recorded verdicts,
    1 624 of them fails. An artifact nobody touched does not stop failing
    because a day passed.
    """
    _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])
    _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])

    from core.quality_gates.graph import KnowledgeGraph

    g = KnowledgeGraph(str(home / "tenants" / "_default" / "global" / "quality_gates.db"), "_default")
    # age every verdict past both windows (breaks the chain — test DB only)
    g.conn.execute("UPDATE gate_events SET timestamp = '2020-01-0' || CAST(1 + (rowid % 2) AS VARCHAR) || 'T00:00:00.000000Z'")
    g.close()

    status = client.get(f"{BASE}/status").json()
    assert status["events_24h"] == 0
    adr = status["summary"]["ADRGate"]
    assert adr["last_24h"]["total"] == 0
    # two runs, two ADRs → two current verdicts, not four
    assert adr["current"] == {"pass": 1, "warn": 0, "fail": 1, "total": 2, "pass_percentage": 50.0}
    assert status["summary"]["IdeaGate"]["current"]["pass_percentage"] is None
    assert status["as_of"].startswith("2020-01-0")

    assert client.get(f"{BASE}/failures").json()["total"] == 0  # the window scope is unchanged
    cur = client.get(f"{BASE}/failures?scope=current").json()
    assert cur["total"] == 2
    assert {f["artifact_id"] for f in cur["failures"]} == {"ADR-9002-bad", "CONCEPT-9001"}
