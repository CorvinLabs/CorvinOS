"""Layer Forge operations as runs (ADR-2253 D1; T-0151): plan, create and promote are three independent requests,
each its own run with its own phases, gates as events, and an audit failure shown in the operation's own phase.

The console router is the real one; the REVIEW (an LLM call) is stubbed exactly as test_layer_forge_routes_e2e does."""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_layer_forge_routes_e2e import BASE, TENANT, _client, _manifest  # noqa: E402

F = "/v1/console/forge"


def _run(client, run_id):
    r = client.get(f"{F}/runs/{run_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _events(client, run_id):
    return client.get(f"{F}/runs/{run_id}/events").json()["events"]


def test_a_create_is_a_run_with_every_phase_and_every_verdict_as_an_event(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        r = client.post(BASE, json=_manifest(), headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, r.text
        body = r.json()
        run_id = body["run_id"]
        run = _run(client, run_id)
        assert run["kind"] == "layer" and run["operation"] == "create" and run["status"] == "success"
        assert run["phases"] == ["validate", "test", "enforce", "review", "create"]
        assert run["artifact_ref"] == {"kind": "layer", "id": "console.rule@0.1.0"}
        ev = _events(client, run_id)
        phases_seen = []
        for e in ev:  # in order, without repeating: the closing 100 % event belongs to the last phase
            if e["type"] == "progress" and e["phase"] and (not phases_seen or phases_seen[-1] != e["phase"]):
                phases_seen.append(e["phase"])
        assert phases_seen == ["validate", "test", "enforce", "review", "create"], phases_seen
        assert ev[-1]["data"].get("progress") == 100
        gates = [e for e in ev if e["type"] == "gate"]
        by_phase = {p: [g for g in gates if g["phase"] == p] for p in ("test", "enforce", "review")}
        assert any(g["data"].get("gate_id") == "schema" and g["data"]["status"] == "PASS" for g in by_phase["test"])
        assert any(g["data"].get("rule_id") == "schema_validation" for g in by_phase["enforce"])
        assert by_phase["review"][0]["data"]["verdict"] == "PASS"
        # the response shape is unchanged: the pipeline result plus one additive field
        assert set(body) == {"status", "registry_key", "error", "phase", "gate_verdicts", "enforcement_verdicts",
                             "review_verdict", "run_id"}


def test_a_failing_gate_fails_the_run_in_the_test_phase_and_the_run_id_is_in_the_error_body(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        bad = _manifest(quality_gates=[{"gate_id": "missing", "test_path": "tests/layer_forge/test_does_not_exist_xyz.py"}])
        r = client.post(BASE, json=bad, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 422
        detail = r.json()["detail"]
        run = _run(client, detail["run_id"])
        assert run["status"] == "failed" and run["phase"] == "test" and run["phase"] in run["phases"]
        gate = next(e for e in _events(client, detail["run_id"]) if e["type"] == "gate")
        assert gate["data"]["gate_id"] == "missing" and gate["data"]["status"] != "PASS"


def test_an_invalid_manifest_fails_in_validate(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        r = client.post(BASE, json={"id": "x", "version": "banana", "targets": []}, headers={"X-CSRF-Token": csrf})
        assert r.status_code == 422
        run = _run(client, r.json()["detail"]["run_id"])
        assert run["status"] == "failed" and run["phase"] == "validate"


def test_a_promote_is_its_own_run_linked_to_the_create_by_artifact_ref(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        h = {"X-CSRF-Token": csrf}
        created = client.post(BASE, json=_manifest(), headers=h).json()
        r = client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "deployed"}, headers=h)
        assert r.status_code == 200, r.text
        out = r.json()
        assert out["status"] == "deployed" and out["run_id"] != created["run_id"]
        run = _run(client, out["run_id"])
        assert run["operation"] == "promote" and run["phases"] == ["promote"] and run["status"] == "success"
        assert run["artifact_ref"] == _run(client, created["run_id"])["artifact_ref"], "linked by id@version"
        rows = client.get(f"{F}/runs", params={"kind": "layer"}).json()["runs"]
        assert {r["operation"] for r in rows} == {"create", "promote"}


def test_a_refused_promote_fails_its_run_and_an_unknown_definition_records_no_run(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        h = {"X-CSRF-Token": csrf}
        client.post(BASE, json=_manifest(), headers=h)
        client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "deployed"}, headers=h)
        before = client.get(f"{F}/runs", params={"kind": "layer"}).json()["count"]
        assert client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "proposed"}, headers=h).status_code == 409
        rows = client.get(f"{F}/runs", params={"kind": "layer"}).json()["runs"]
        assert len(rows) == before + 1
        refused = next(r for r in rows if r["status"] == "failed")
        assert refused["operation"] == "promote" and refused["phase"] == "promote" and refused["failure_code"] == "promotion_refused"
        # an unknown definition is a 404 and records NO run (garbage ids must not fill the history)
        assert client.post(f"{BASE}/does.not.exist/1.0.0/transition", json={"to_status": "accepted"}, headers=h).status_code == 404
        assert client.get(f"{F}/runs", params={"kind": "layer"}).json()["count"] == before + 1


@pytest.mark.parametrize("operation", ["plan", "create", "promote"])
def test_an_audit_failure_shows_the_operations_own_phase_with_audit_unavailable(tmp_path, operation):
    """The orchestrator calls this phase 'audit'; a run's failing phase must be one of its own phases."""
    from core.orchestration.layer_forge import audit as lf_audit
    from core.orchestration.layer_forge.orchestrator import LayerForgeOrchestrator

    def boom(self, event, **details):
        raise lf_audit.LayerForgeAuditError("chain unavailable")

    with _client(tmp_path) as (client, csrf, home):
        h = {"X-CSRF-Token": csrf}
        if operation == "promote":
            assert client.post(BASE, json=_manifest(), headers=h).status_code == 200  # exists before the chain goes down
        with patch.object(LayerForgeOrchestrator, "_audit", boom), \
                patch("core.orchestration.layer_forge.orchestrator.plan_layer_definition",
                      return_value={"id": "console.planned", "version": "1.0.0", "targets": [{"layer_id": "L34"}]}):
            if operation == "plan":
                r = client.post(f"{BASE.rsplit('/definitions', 1)[0]}/plan", json={"layer_id": "L34", "intent": "x"}, headers=h)
                run_id = r.json()["detail"]["run_id"]
            elif operation == "create":
                r = client.post(BASE, json=_manifest(id="console.other"), headers=h)
                run_id = r.json()["detail"]["run_id"]
            else:
                r = client.post(f"{BASE}/console.rule/0.1.0/transition", json={"to_status": "deployed"}, headers=h)
                run_id = next(x["run_id"] for x in client.get(f"{F}/runs", params={"kind": "layer", "status": "failed"}).json()["runs"]
                              if x["operation"] == "promote")
            assert r.status_code == 503, r.text
        run = _run(client, run_id)
        assert run["status"] == "failed" and run["failure_code"] == "audit_unavailable"
        assert run["phase"] == operation and run["phase"] in run["phases"], run


def test_layer_runs_do_not_count_against_the_generation_cap_and_are_never_refused_by_it(tmp_path):
    from corvin_console import forge_runs
    with _client(tmp_path) as (client, csrf, home):
        runs_dir = home / "tenants" / TENANT / "global" / "forge_runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        for i in range(forge_runs.MAX_RUNNING_PER_TENANT):  # a live peer's engine runs fill the cap
            (runs_dir / f"run-{i:012d}.json").write_text(json.dumps({
                "tenant_id": TENANT, "kind": "tool", "status": "running", "boot_id": "peer", "heartbeat_at": time.time()}))
        assert forge_runs.running_count(TENANT) == forge_runs.MAX_RUNNING_PER_TENANT
        r = client.post(BASE, json=_manifest(), headers={"X-CSRF-Token": csrf})
        assert r.status_code == 200, "a layer create is not an engine generation: the cap must not refuse it"
        assert forge_runs.running_count(TENANT) == forge_runs.MAX_RUNNING_PER_TENANT, "and it does not count against it"


def test_run_start_and_finish_are_on_the_chain_for_a_layer_run(tmp_path):
    with _client(tmp_path) as (client, csrf, home):
        run_id = client.post(BASE, json=_manifest(), headers={"X-CSRF-Token": csrf}).json()["run_id"]
        chain = home / "tenants" / TENANT / "global" / "forge" / "audit.jsonl"
        recs = [json.loads(line) for line in chain.read_text().splitlines() if line.strip()]
        fin = [r for r in recs if r.get("event_type") == "forge_run.finished" and r["details"]["run_id"] == run_id]
        assert len(fin) == 1 and fin[0]["details"]["run_kind"] == "layer" and fin[0]["details"]["artifact_id"] == "console.rule@0.1.0"
