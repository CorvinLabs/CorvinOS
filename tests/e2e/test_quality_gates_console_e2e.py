"""Quality Gates console — real runs, judged by the checkout's own ``kb check``.

Over HTTP (TestClient), session overridden, against a TEMP ``CORVIN_HOME`` (the DuckDB
event store lands there) and a TEMP Corvin-Knowledge root (``CORVIN_ADR_ROOT``) that is a
real, minimal knowledge repository: the KB's scripts are copied in, valid artifacts are
written by ``kb new`` (the only identity writer) and one decision is broken by hand.

Until 2026-10-26 the four gates carried their own rule tables (``validators.py``) and
disagreed with ``kb check`` on whether an artifact fails for 848 of 1277 live artifacts:
a proposed decision with ``commits: []`` — the shape ``kb new decision`` writes — failed
here. The verdict now comes from ``kb check`` alone (``core/quality_gates/kb_verdicts.py``).

1. status: per-gate counts with a pass share that is ``null`` (not 0) for a gate nobody ran;
2. run/all: judges every artifact with the KB's rules, records hash-chained gate_events,
   reports progress to 100; a kb error fails, a kb warning warns, a clean artifact passes;
3. a checkout whose kb.py predates per-finding severity is refused, never guessed;
4. a foreign tenant's run is 403; no ADR root → a failed run that says so.
"""
from __future__ import annotations

import dataclasses
import json
import shutil
import subprocess
import sys
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


BAD_STATUS_ADR = """---
id: ADR-0009
uid: 01K0000000000000000000BAD9
status: maybe
depends_on: []
paths: []
docs: []
commits: []
---
# ADR-0009 — a status the KB does not know
"""

_SOURCES = (
    "sources:\n  - name: kb\n    root: .\n    writable: true\n    dirs:\n"
    "      decisions: decision\n      concepts: concept\n      ideas: idea\n"
    "      implementation-plans: plan\n      kb/initiatives: initiative\n"
    "      kb/epics: epic\n      kb/tasks: task\n"
)


def _kb_scripts() -> Path:
    """The KB scripts this host would run: the sibling checkout, else the submodule."""
    repo = Path(__file__).resolve().parents[2]
    for c in (repo.parent / "Corvin-Knowledge" / "scripts", repo / "corvin_decisions" / "scripts"):
        if (c / "kb.py").is_file():
            return c
    pytest.skip("no Corvin-Knowledge scripts on this host (sibling checkout or corvin_decisions submodule)")


def _git(*a: str, cwd: Path) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *a], cwd=cwd, check=True,
                   capture_output=True)


def _kb(root: Path, *a: str) -> dict:
    r = subprocess.run([sys.executable, str(root / "scripts" / "kb.py"), "--repo", str(root), *a],
                       capture_output=True, text=True)
    assert r.returncode == 0, f"kb {a}: {r.stdout}{r.stderr}"
    return json.loads(r.stdout)


@pytest.fixture
def adr_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "Corvin-Knowledge"
    (root / "scripts").mkdir(parents=True)
    for f in ("kb.py", "kb_model.py", "kb_lib.py"):
        shutil.copy(_kb_scripts() / f, root / "scripts" / f)
    for d in ("decisions", "concepts", "implementation-plans", "ideas", "kb/_meta"):
        (root / d).mkdir(parents=True, exist_ok=True)
    (root / "kb/_meta/sources.yaml").write_text(_SOURCES)
    (root / "kb/audit.jsonl").write_text("")
    (root / ".gitignore").write_text("kb/.lock\nkb/graph/\n.kb-cache/\nscripts/__pycache__/\n")
    _git("init", "-q", "-b", "main", cwd=root)
    _git("add", "-A", cwd=root)
    _git("commit", "-q", "-m", "init", cwd=root)
    # a proposed decision exactly as `kb new` writes it: depends_on/paths/docs/commits empty
    _kb(root, "new", "decision", "--title", "A proposed decision")
    _kb(root, "new", "concept", "--title", "A working method")
    _kb(root, "new", "plan", "--title", "A plan")
    (root / "decisions" / "ADR-0009-bad-status.md").write_text(BAD_STATUS_ADR)
    monkeypatch.setenv("CORVIN_ADR_ROOT", str(root))
    return root


def _stem(root: Path, sub: str, exclude: str = "") -> str:
    return next(p.stem for p in sorted((root / sub).glob("*.md")) if p.stem != exclude)


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
    for _ in range(1500):
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


def test_run_all_judges_every_artifact_by_the_kb_rules_and_records_events(client, adr_root, home):
    r = client.post(f"{BASE}/run/all")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]
    result = _wait(client, run_id)
    assert result["status"] == "completed", result
    assert result["progress"] == 100 and result["artifacts_total"] == 4 and result["artifacts_done"] == 4
    by_gate = {g["gate_name"]: g for g in result["gates_results"]}
    # the hand-broken status is a kb ERROR; the proposed decision only carries the kb's
    # readiness WARNINGS (no concept, no plan yet) — it is not a failure
    assert by_gate["ADRGate"] == {"gate_name": "ADRGate", "artifacts": 2, "pass": 0, "warn": 1, "fail": 1}
    assert by_gate["IdeaGate"]["artifacts"] == 0

    # the event store holds one hash-chained row per judged artifact
    assert (home / "tenants" / "_default" / "global" / "quality_gates.db").exists()
    status = client.get(f"{BASE}/status").json()
    assert status["events_24h"] == 4 and status["events_total"] == 4
    assert status["summary"]["IdeaGate"]["last_24h"]["pass_percentage"] is None
    assert status["last_run"]["run_id"] == run_id and status["last_run"]["status"] == "completed"

    failures = {f["artifact_id"]: f for f in client.get(f"{BASE}/failures?scope=current").json()["failures"]}
    bad = failures["ADR-0009-bad-status"]
    assert bad["gate_name"] == "ADRGate" and bad["verdict"] == "fail"
    assert bad["reason"].startswith("kb check: ") and bad["findings_count"] >= 1
    proposed = _stem(adr_root, "decisions", exclude="ADR-0009-bad-status")
    assert failures[proposed]["verdict"] == "warn"

    # the verdict names the kb rule that decided
    hist = client.get(f"{BASE}/history/ADR-0009-bad-status").json()
    assert hist["pagination"]["total"] == 1 and hist["events"][0]["verdict"] == "fail"

    # the audit chain verifies
    from core.quality_gates.audit import QualityGateAuditLogger
    from core.quality_gates.graph import KnowledgeGraph

    g = KnowledgeGraph(str(home / "tenants" / "_default" / "global" / "quality_gates.db"), "_default")
    assert QualityGateAuditLogger(g.conn).verify_chain("_default") is True
    g.close()


def test_every_verdict_equals_the_kb_checks_own_severity(client, adr_root, home):
    """One rule source: for every artifact the gate verdict is what `kb check` says about
    that file. The reference is independent of the route's code: an own `kb check --json`
    plus the RULES table obtained by actually importing the checkout's kb.py (the route
    parses it with ast and never imports it)."""
    _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])
    ref_file = adr_root.parent / "ref.json"
    subprocess.run([sys.executable, str(adr_root / "scripts" / "kb.py"), "--repo", str(adr_root),
                    "check", "--json", str(ref_file)], capture_output=True, text=True)
    ref = json.loads(ref_file.read_text())
    rules = json.loads(subprocess.run(
        [sys.executable, "-c", "import json, kb; print(json.dumps({k: v[0] for k, v in kb.RULES.items()}))"],
        cwd=adr_root / "scripts", capture_output=True, text=True, check=True).stdout)
    sev: dict[str, set] = {}
    for rule, items in ref["issues"].items():
        for x in items:
            sev.setdefault(Path(x["file"]).resolve().name, set()).add(rules[rule])

    from core.quality_gates.graph import KnowledgeGraph

    g = KnowledgeGraph(str(home / "tenants" / "_default" / "global" / "quality_gates.db"), "_default")
    rows = g.conn.execute("SELECT artifact_id, verdict FROM gate_events").fetchall()
    g.close()
    verdicts = {aid: v for aid, v in rows}
    for sub in ("decisions", "concepts", "implementation-plans"):
        for p in (adr_root / sub).glob("*.md"):
            s = sev.get(p.name, set())
            expected = "fail" if "error" in s else "warn" if s else "pass"
            got = [v for aid, v in verdicts.items() if aid == p.stem]
            assert got and got[0] == expected, (p.name, s, verdicts)


def test_a_checkout_without_its_kb_is_refused_never_judged_by_another_table(client, adr_root):
    (adr_root / "scripts" / "kb.py").unlink()
    result = _wait(client, client.post(f"{BASE}/run/all").json()["run_id"])
    assert result["status"] == "failed" and "no scripts/kb.py" in (result["error"] or ""), result
    assert client.get(f"{BASE}/status").json()["events_total"] == 0   # no verdict without the KB's rules


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
    assert r["status"] == "failed" and "no Corvin-Knowledge checkout" in r["error"]


def test_a_foreign_tenants_run_is_403(client, adr_root):
    run_id = client.post(f"{BASE}/run/all").json()["run_id"]
    _wait(client, run_id)
    from core.console.corvin_console.routes import quality_gates as qg

    with qg._runs_lock:
        qg._run_cache[run_id]["tenant_id"] = "other"
    assert client.get(f"{BASE}/results/{run_id}").status_code == 403


def test_current_state_survives_the_24h_window_and_counts_each_artifact_once(client, adr_root, home):
    """The page reads the CURRENT verdict per artifact, not a 24h window (live 2026-09-22:
    a 3-day-old run read as zeros over 2 184 recorded verdicts)."""
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
    assert adr["current"]["total"] == 2 and adr["current"]["fail"] == 1
    assert status["summary"]["IdeaGate"]["current"]["pass_percentage"] is None
    assert status["as_of"].startswith("2020-01-0")
    assert client.get(f"{BASE}/failures").json()["total"] == 0  # the window scope is unchanged
