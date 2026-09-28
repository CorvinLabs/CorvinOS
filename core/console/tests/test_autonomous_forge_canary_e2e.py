"""E2E for the Autonomous Skill Forge (ADR-2094).

Drives the REAL boundaries:
  * the console routes over HTTP (router mounted at /v1/console, session
    cookie + CSRF header, member licence loaded the way the hosts load it);
  * the REAL injection function the bridge adapter and the delegate path call
    (``skill_inject.collect_active_skills``) and the REAL follow-up grading
    (``skill_inject.grade_from_user_followup``);
  * the REAL tenant audit chain, verified at the end.

Only the LLM engine is faked (the same stand-in the Skill-Creator E2E uses),
so a run takes seconds instead of minutes of subscription time.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[3]
for _p in (_REPO / "corvin_operator", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "skill-forge", _REPO / "corvin_operator" / "forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from test_skill_creator_e2e import csrf_headers, refine_engine_for  # noqa: E402

SKILL = "assistant.check_json_syntax"
LIVE_BODY = "# Validate JSON\n\n1. Read the file\n2. Parse it\n3. Report errors"
BASE = "/v1/console/autonomous-forge"


@contextmanager
def forge_client(tmp_path: Path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / "_default" / "global" / "auth").mkdir(parents=True)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "XDG_CONFIG_HOME",
            "CORVIN_AUDIT_ANCHOR_KEY")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ.update({
        "CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
        "VOICE_AUDIT_PATH": str(home / "audit.jsonl"),
        "XDG_CONFIG_HOME": str(tmp_path / "xdg"),
        "CORVIN_AUDIT_ANCHOR_KEY": str(tmp_path / "anchor.key"),
    })

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from license import validator as host_validator  # the hosts' import name
    from corvin_console import auth as _auth
    from corvin_console.routes import autonomous_forge_routes as route
    from skill_creator import skill_creator as sc
    from skill_creator.registry_bridge import promote_to_registry

    orig_license = host_validator._ACTIVE_LICENSE
    host_validator._set_active_license({"tier": "member"})
    orig_resolve = sc.resolve_llm_client
    sc.resolve_llm_client = lambda explicit=None: refine_engine_for(SKILL)

    app = FastAPI()
    app.include_router(route.router, prefix=BASE)
    rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.session_record = rec  # type: ignore[attr-defined]
    root = home / "tenants" / "_default" / "skill-forge"
    promote_to_registry(root, name=SKILL, body_md=LIVE_BODY, description="Validates JSON files.")
    try:
        yield client, root, home
    finally:
        sc.resolve_llm_client = orig_resolve
        host_validator._set_active_license(orig_license)
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def post(client, path: str, body: dict):
    return client.post(BASE + path, json=body, headers=csrf_headers(client.session_record))


def wait_fork(client, run_id: str) -> dict:
    deadline = time.time() + 30
    while time.time() < deadline:
        run = client.get(f"{BASE}/fork/{run_id}").json()
        if run["status"] in ("success", "failed"):
            return run
        time.sleep(0.05)
    pytest.fail(f"fork {run_id} did not finish")


def serve(home: Path, channel: str) -> str:
    """One real injection for ``channel``; which body did it carry?"""
    import skill_inject as si
    block = si.collect_active_skills(channel_id=channel, profile={}, project_root=home)
    assert block and SKILL in block, block
    return "candidate" if "Melde doppelte Schluessel" in block else "live"


def follow_up(home: Path, channel: str, text: str) -> None:
    import skill_inject as si
    si.grade_from_user_followup(channel_id=channel, profile={}, user_text=text,
                                prev_run_id=f"run-{channel}", prev_skill_names=[SKILL])


def chain_records(home: Path) -> list[dict]:
    path = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def test_full_canary_lifecycle_over_http(tmp_path):
    with forge_client(tmp_path) as (client, root, home):
        # ── status before anything happened ────────────────────────────────
        st = client.get(f"{BASE}/status").json()
        assert st["canaries"] == []
        assert [s["skill_id"] for s in st["skills"]] == [SKILL]
        assert st["autopilot"]["enabled"] is True

        # ── fork needs CSRF ────────────────────────────────────────────────
        assert client.post(f"{BASE}/fork", json={"skill_id": SKILL}).status_code == 403

        resp = post(client, "/fork", {"skill_id": SKILL, "instruction": "warn on duplicate keys"})
        assert resp.status_code == 202, resp.text
        run = wait_fork(client, resp.json()["run_id"])
        assert run["status"] == "success", run

        (canary,) = client.get(f"{BASE}/status").json()["canaries"]
        assert canary["status"] == "canary" and canary["traffic_percent"] == 10
        cand = client.get(f"{BASE}/candidate/{SKILL}").json()
        assert "Melde doppelte Schluessel" in cand["candidate_body"]
        assert "Report errors" in cand["live_body"]

        # ── real injection splits by chat; follow-ups grade the served body ─
        served: dict[str, list[str]] = {"live": [], "candidate": []}
        i = 0
        while min(len(served["live"]), len(served["candidate"])) < 6:
            ch = f"discord:chat{i}"
            served[serve(home, ch)].append(ch)
            i += 1
            assert i < 400, served
        for ch in served["candidate"]:
            follow_up(home, ch, "danke, perfekt")      # approval 0.9
        for ch in served["live"]:
            follow_up(home, ch, "falsch, nochmal")     # rejection 0.1

        from skill_creator.registry_bridge import registry_for
        live_grades = registry_for(root).get(SKILL).grades
        assert all("0.9" not in str(g["score"]) for g in live_grades), \
            "a candidate grade leaked into the live skill's registry grades"

        canary = client.get(f"{BASE}/status").json()["canaries"][0]
        assert canary["stats"]["candidate"]["outcome_n"] == len(served["candidate"])
        assert canary["stats"]["candidate"]["outcome_mean"] == 0.9
        assert canary["stats"]["live"]["outcome_mean"] == 0.1
        assert canary["verdict"]["decision"] == "escalate"

        metrics = client.get(f"{BASE}/metrics", params={"skill_id": SKILL}).json()
        assert {p["variant"] for p in metrics["points"]} == {"live", "candidate"}

        # ── the autopilot executes the gate ────────────────────────────────
        from skill_creator import autonomous
        autonomous.tick(root)
        assert client.get(f"{BASE}/status").json()["canaries"][0]["traffic_percent"] == 25

        # ── pause serves live to everyone, resume restores the split ───────
        assert post(client, "/pause", {"skill_id": SKILL}).json()["canary"]["status"] == "paused"
        assert all(serve(home, ch) == "live" for ch in served["candidate"])
        assert post(client, "/resume", {"skill_id": SKILL}).json()["canary"]["status"] == "canary"

        # ── approve: the candidate is live, its grades are the evidence ────
        resp = post(client, "/approve", {"skill_id": SKILL})
        assert resp.status_code == 200, resp.text
        assert resp.json()["canary"]["status"] == "approved"
        reg = registry_for(root)
        assert "Melde doppelte Schluessel" in reg.get_body(SKILL)
        assert {g["score"] for g in reg.get(SKILL).grades} == {0.9}
        assert serve(home, "discord:new-chat") == "candidate"  # now simply the live body

        # ── rollback restores the previous body and grades ─────────────────
        resp = post(client, "/rollback", {"skill_id": SKILL})
        assert resp.json()["canary"]["status"] == "rolled_back"
        reg = registry_for(root)
        assert "Report errors" in reg.get_body(SKILL)
        assert reg.get(SKILL).grades == live_grades

        # ── history + audit chain ──────────────────────────────────────────
        (attempt,) = client.get(f"{BASE}/history").json()["attempts"]
        kinds = [e["type"] for e in attempt["events"]]
        assert kinds[0] == "started" and kinds[-1] == "rolled_back"
        assert "traffic" in kinds and "approved" in kinds
        assert all(e["hash"] for e in attempt["events"])

        records = chain_records(home)
        chain_hashes = {r.get("hash") for r in records}
        assert all(e["hash"] in chain_hashes for e in attempt["events"])
        graded = [r for r in records if r["event_type"] == "skill.canary_graded"]
        assert len(graded) == len(served["live"]) + len(served["candidate"])
        import forge.security_events as se
        ok, problems = se.verify_chain(
            home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl")
        assert ok, problems


def test_autopilot_forks_on_a_loss_signal(tmp_path):
    """No operator involved: three rejections of the live skill are a loss
    signal, and one autopilot pass forges a candidate and starts its canary."""
    with forge_client(tmp_path) as (client, root, home):
        for n in range(3):
            serve(home, f"telegram:{n}")
            follow_up(home, f"telegram:{n}", "falsch, das stimmt nicht")
        st = client.get(f"{BASE}/status").json()
        (row,) = st["skills"]
        assert row["loss_signal"] is True and row["outcome_mean"] == 0.1

        assert post(client, "/tick", {}).status_code == 202
        deadline = time.time() + 30
        while time.time() < deadline:
            canaries = client.get(f"{BASE}/status").json()["canaries"]
            if canaries:
                break
            time.sleep(0.05)
        (canary,) = canaries
        assert canary["source"] == "autopilot"
        assert canary["trigger"]["reason"] == "outcome_mean_below_threshold"
        assert canary["reference"] == {"mean": 0.1, "source": "live_before_canary"}


def test_autopilot_switch_and_gates(tmp_path):
    with forge_client(tmp_path) as (client, root, home):
        assert post(client, "/autopilot", {"enabled": False}).json() == {"enabled": False}
        from skill_creator import autonomous
        assert autonomous.tick(root)["skipped"] == "autopilot_disabled"
        assert post(client, "/autopilot", {"enabled": True}).json() == {"enabled": True}
        assert any(r["event_type"] == "skill.canary_autopilot_changed"
                   for r in chain_records(home))


def test_decisions_without_a_canary_are_refused(tmp_path):
    with forge_client(tmp_path) as (client, root, home):
        for action in ("approve", "defer", "pause", "resume", "rollback"):
            resp = post(client, f"/{action}", {"skill_id": SKILL})
            assert resp.status_code == 409, (action, resp.text)
        assert post(client, "/fork", {"skill_id": "code.not_mine"}).status_code == 422
        assert post(client, "/fork", {"skill_id": "assistant.missing"}).status_code == 404
        assert client.get(f"{BASE}/metrics", params={"skill_id": SKILL}).status_code == 404


def test_free_tier_forks_within_its_daily_quota(tmp_path):
    """ADR-2095: a fork is a skill generation — on the free tier it consumes
    one of the 5 daily credits; with none left it is refused 402 up front,
    while approve / rollback of an existing canary stay possible."""
    with forge_client(tmp_path) as (client, root, home):
        from license import validator as host_validator
        host_validator._set_active_license(None)
        from skill_creator.registry_bridge import _load_registry_module
        mod = _load_registry_module()
        # promote_to_registry in the fixture ran as member → free starts at 0 used.
        resp = post(client, "/fork", {"skill_id": SKILL})
        assert resp.status_code == 202, resp.text
        assert wait_fork(client, resp.json()["run_id"])["status"] == "success"
        tid, h = mod._tenant_and_home(root)
        assert mod.skill_quota_status(tid, h)["used"] == 1

        for _ in range(4):
            mod.charge_skill_quota(tid, h)
        resp = post(client, "/defer", {"skill_id": SKILL})
        assert resp.status_code == 200, resp.text
        resp = post(client, "/fork", {"skill_id": SKILL})
        assert resp.status_code == 402, resp.text
        assert resp.json()["detail"]["error"] == "limit_reached"


def test_concurrent_first_use_never_sees_a_half_imported_canary(tmp_path):
    """Live defect 2026-09-28: the console serves /status and /history on
    parallel pool threads; the first use of the canary module raced and one
    request got ``partially initialized module 'skill_forge.canary'`` (500).
    A fresh interpreter, 16 threads, one barrier."""
    import subprocess
    code = f"""
import sys, threading
sys.path[:0] = {[str(_REPO / 'corvin_operator'), str(_REPO / 'corvin_operator' / 'skill-forge'), str(_REPO / 'corvin_operator' / 'forge')]!r}
from skill_creator import autonomous
barrier = threading.Barrier(16); errors = []
def hit():
    barrier.wait()
    try:
        autonomous.canary_store({str(tmp_path)!r}).list_states()
    except Exception as exc:
        errors.append(repr(exc))
ts = [threading.Thread(target=hit) for _ in range(16)]
[t.start() for t in ts]; [t.join() for t in ts]
print("ERRORS", errors)
sys.exit(1 if errors else 0)
"""
    for _ in range(5):
        proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60,
                              env={**os.environ, "CORVIN_HOME": str(tmp_path / "h")})
        assert proc.returncode == 0, proc.stdout + proc.stderr


def test_engine_timeout_mid_ldd_keeps_the_scored_candidate(tmp_path):
    """Live defect 2026-09-28: on a long skill the refine call at k=1 ran past
    the per-call timeout AFTER k=1 had been measured, and the whole fork was
    lost ("claude CLI timed out after 180.0s"). The scored iterate must
    survive as a non-converged candidate; the canary still starts."""
    from skill_creator.llm_client import ClaudeCodeUnavailable

    engine = refine_engine_for(SKILL)
    inner = engine.messages.create.side_effect
    bad_rubric = ('{"clarity": 0.6, "executability": 0.6, "scope": 0.6, '
                  '"coupling": 0.6, "notes": "vague"}')

    def _create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "Reply with JSON ONLY" in prompt:
            from unittest.mock import MagicMock
            return MagicMock(content=[MagicMock(text=bad_rubric)])  # not converged → diagnose + fix
        if "You are refining a skill" in prompt:
            raise ClaudeCodeUnavailable("claude CLI timed out after 300.0s")
        return inner(**kwargs)

    engine.messages.create.side_effect = _create
    with forge_client(tmp_path) as (client, root, home):
        from skill_creator import skill_creator as sc
        sc.resolve_llm_client = lambda explicit=None: engine
        resp = post(client, "/fork", {"skill_id": SKILL})
        run = wait_fork(client, resp.json()["run_id"])
        assert run["status"] == "success", run
        (canary,) = client.get(f"{BASE}/status").json()["canaries"]
        assert canary["status"] == "canary"
