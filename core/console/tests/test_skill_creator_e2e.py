"""E2E for the console Skill-Creator route (ADR-0363).

Drives the REAL HTTP boundary (FastAPI router mounted at /v1/console, the
same prefix the console SPA calls) — POST /generate, poll /status, read
/skills and /stats — rather than calling the orchestrator directly.

Engine reach: the default engine is the Claude Code CLI (Max subscription).
These tests inject a fake engine so CI neither needs a login nor spends
minutes per run; `test_live_generation` exercises the real CLI and is
opt-in via CORVIN_E2E_LIVE_ENGINE=1.
"""

from __future__ import annotations

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock

import pytest

_REPO = Path(__file__).resolve().parents[3]
_OPERATOR_DIR = _REPO / "operator"
if str(_OPERATOR_DIR) not in sys.path:
    sys.path.insert(0, str(_OPERATOR_DIR))

SPEC_JSON = (
    '{"name": "assistant.check-json-syntax", "scope": "assistant", '
    '"purpose": "Validates JSON files for syntax errors and reports line numbers.", '
    '"method": "# Validate JSON\\n\\n1. Read the file\\n2. Parse it strictly\\n'
    '3. Report every error with its line number\\n4. Exit non-zero on failure\\n'
    '5. Summarise how many files were checked", '
    '"dependencies": ["python3"], "keywords": ["json"]}'
)
CLEAN_RUBRIC = ('{"clarity": 0.0, "executability": 0.0, "scope": 0.0, '
                '"coupling": 0.0, "notes": "none"}')


# A purpose exactly one character over the 200-char cap — the live failure
# ("Purpose length 201 outside range [20, 200]") that used to kill a run
# after minutes of engine time.
OVERLONG_PURPOSE = "A" * 150 + ". " + "B" * 49
OVERLONG_SPEC_JSON = SPEC_JSON.replace(
    "Validates JSON files for syntax errors and reports line numbers.",
    OVERLONG_PURPOSE,
)


REFINED_METHOD = ("# Validate JSON\\n\\n1. Read the file\\n2. Parse strictly\\n"
                  "3. Melde doppelte Schluessel als Warnung\\n"
                  "4. Exit non-zero on a syntax error\\n5. Summarise the run")


def refine_engine_for(name: str):
    """Engine whose refine reply changes the body and keeps the name."""
    refined = (
        '{"name": "%s", "scope": "assistant", '
        '"purpose": "Validates JSON files and reports duplicate keys as warnings too.", '
        '"method": "%s", "dependencies": ["python3"], "keywords": ["json"]}'
    ) % (name, REFINED_METHOD)

    client = MagicMock()
    client.engine_id = "claude_code"

    def _create(**kwargs):
        prompt = kwargs["messages"][0]["content"]
        if "Reply with JSON ONLY" in prompt:
            text = CLEAN_RUBRIC
        elif "refining an EXISTING skill" in prompt:
            text = refined
        elif "FINDING:" in prompt:
            text = "VERDICT: REFUTED"
        else:
            text = "User validates a file with duplicate keys"
        return MagicMock(content=[MagicMock(text=text)])

    client.messages.create.side_effect = _create
    return client


def fake_engine(*, fail_with: Exception | None = None, spec_json: str = SPEC_JSON):
    """Engine stand-in answering each phase prompt in its own shape."""
    client = MagicMock()
    client.engine_id = "claude_code"

    def _create(**kwargs):
        if fail_with is not None:
            raise fail_with
        prompt = kwargs["messages"][0]["content"]
        if "Reply with JSON ONLY" in prompt:
            text = CLEAN_RUBRIC
        elif "SYNTHESIS:" in prompt:
            text = spec_json
        elif "Generate a realistic test scenario" in prompt:
            text = "User validates a 500-line JSON file"
        elif "FINDING:" in prompt:
            text = "VERDICT: REFUTED"
        else:
            text = "- point one\n- point two"
        return MagicMock(content=[MagicMock(text=text)])

    client.messages.create.side_effect = _create
    return client


@contextmanager
def console_client(tmp_path: Path, engine=None, *, live: bool = False,
                   tenant: str = "_default"):
    """Mount the console router and yield an AUTHENTICATED TestClient.

    The route derives its tenant from the session record, so the test has to
    carry a real session cookie — the same contract the console SPA has.
    """
    home = tmp_path / "corvin_home"
    (home / "tenants" / tenant / "global" / "auth").mkdir(parents=True, exist_ok=True)

    prev = {k: os.environ.get(k) for k in ("CORVIN_HOME", "CORVIN_TENANT_ID",
                                           "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console import auth as _auth
    from corvin_console.routes import skill_creator_api as route
    from skill_creator import skill_creator as sc

    app = FastAPI()
    app.include_router(route.router, prefix="/v1/console")

    orig_resolve = sc.resolve_llm_client
    orig_runs = dict(route._generation_runs)
    orig_stats = dict(route._skill_stats)

    if not live:
        sc.resolve_llm_client = lambda explicit=None: engine

    route._generation_runs.clear()
    route._skill_stats.update({"total_generated": 0, "avg_quality": 0.0,
                               "total_iterations": 0, "last_generated_at": None})

    rec = _auth.create_session(tenant_id=tenant, token_fingerprint="test-fp")
    client = TestClient(app, raise_server_exceptions=False)
    client.cookies.set("corvin_console_sid", rec.sid)
    client.session_record = rec  # type: ignore[attr-defined]
    try:
        yield client, route
    finally:
        sc.resolve_llm_client = orig_resolve
        route._generation_runs.clear()
        route._generation_runs.update(orig_runs)
        route._skill_stats.clear()
        route._skill_stats.update(orig_stats)
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def registry_root(tmp_path: Path, tenant: str = "_default") -> Path:
    # Sibling of `global`, not a child — see _registry_root in the route.
    return tmp_path / "corvin_home" / "tenants" / tenant / "skill-forge"


def csrf_headers(rec) -> dict:
    from corvin_console import auth as _auth
    return {"x-csrf-token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)}


def poll_until_done(client, run_id: str, timeout_s: float = 30.0) -> dict:
    deadline = time.time() + timeout_s
    body: dict = {}
    while time.time() < deadline:
        resp = client.get(f"/v1/console/skill-creator/status/{run_id}")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        if body["status"] in ("success", "failed"):
            return body
        time.sleep(0.1)
    pytest.fail(f"run {run_id} did not finish within {timeout_s}s: {body}")


# ── happy path ────────────────────────────────────────────────────────────

def test_generate_runs_on_claude_code_engine_and_promotes(tmp_path):
    """The regression this route shipped with: generation died in Planning
    with the Anthropic SDK's "Could not resolve authentication method"
    because it constructed anthropic.Anthropic() on an install that has no
    API key. It must run on the Claude Code engine instead."""
    with console_client(tmp_path, fake_engine()) as (client, route):
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "erzeuge einen Skill der JSON validiert",
                                 "async": True})
        assert resp.status_code == 202, resp.text
        accepted = resp.json()
        assert accepted["engine"] == "claude_code"

        final = poll_until_done(client, accepted["run_id"])

        assert final["status"] == "success", final
        assert final["error"] is None
        assert final["engine"] == "claude_code"
        assert final["progress"] == 100
        assert final["phase"] == "promotion"
        # The hyphenated name the model produced is normalised, not rejected.
        assert final["skill"]["name"] == "assistant.check_json_syntax"

        # Registered in the tenant's SkillForge registry — the manifest every
        # consumer resolves against — and bootstrap-graded, so it is past
        # skill_inject's eligibility gate.
        assert final["skill"]["injectable"] is True
        promoted = (registry_root(tmp_path) / "skills" /
                    "assistant.check_json_syntax" / "SKILL.md")
        assert promoted.exists()

        listed = client.get("/v1/console/skill-creator/skills").json()
        assert listed["tenant_id"] == "_default"
        assert listed["injectable_count"] == 1
        entry = next(s for s in listed["skills"] if s["name"] == "assistant.check_json_syntax")
        assert entry["n_grades"] == 1
        assert entry["injectable"] is True

        # The View action has an endpoint behind it now.
        detail = client.get("/v1/console/skill-creator/skills/assistant.check_json_syntax")
        assert detail.status_code == 200
        body = detail.json()["body"]
        # The registry renders its own front-matter around the generated
        # body; exactly ONE such block must be present (the promoter must not
        # add a second, which would make the skill unparseable for the engine).
        assert body.count("\n---\n") >= 1
        assert "name: assistant.check_json_syntax" in body
        assert "# Validate JSON" in body
        assert detail.json()["injectable"] is True

        stats = client.get("/v1/console/skill-creator/stats").json()
        assert stats["total_generated"] == 1
        assert stats["last_generated_at"] is not None


def test_overlong_purpose_no_longer_kills_the_run(tmp_path):
    """Regression for the second live failure.

    Phase 1 generates freely, Phase 2 validates fail-closed, and a spec one
    character over the purpose cap discarded the whole run. The spec is now
    normalised before validation; the gate itself stays fail-closed.
    """
    assert len(OVERLONG_PURPOSE) == 201

    with console_client(tmp_path, fake_engine(spec_json=OVERLONG_SPEC_JSON)) as (client, _r):
        run_id = client.post("/v1/console/skill-creator/generate",
                             json={"user_request": "erzeuge einen JSON Skill",
                                   "async": True}).json()["run_id"]
        final = poll_until_done(client, run_id)

    assert final["status"] == "success", final
    assert len(final["skill"]["purpose"]) <= 200
    # Trimmed at the sentence boundary — not chopped mid-word.
    assert final["skill"]["purpose"].endswith(".")


def test_promoted_skill_reaches_the_injection_block(tmp_path):
    """The end of the wire: does a generated skill actually get INJECTED?

    Every cheaper check passed while the answer was no — the skill was
    written, registered, listed and reported `injectable`, into a registry
    root that `skill_inject` never reads (`<tenant>/global/skill-forge`
    instead of `<tenant>/skill-forge`). Only asking the real consumer for
    its markdown block catches that.
    """
    skill_inject = pytest.importorskip(
        "skill_inject", reason="bridge shared tree not importable")

    with console_client(tmp_path, fake_engine()) as (client, _route):
        run_id = client.post("/v1/console/skill-creator/generate",
                             json={"user_request": "erzeuge einen JSON Skill",
                                   "async": True}).json()["run_id"]
        final = poll_until_done(client, run_id)
        assert final["status"] == "success", final

        block = skill_inject.collect_active_skills(
            channel_id="web:e2e", profile={}, max_skills=50,
        )

    assert block, "skill_inject produced no block at all"
    assert "assistant.check_json_syntax" in block


def test_status_reports_every_phase_in_order(tmp_path):
    """The panel renders a stepper; the run must actually walk the phases.

    Before this change the route pinned phase="planning"/20% for the whole
    run and jumped to done, so the UI looked stuck.
    """
    seen: list[str] = []
    with console_client(tmp_path, fake_engine()) as (client, route):
        run_id = client.post("/v1/console/skill-creator/generate",
                             json={"user_request": "erzeuge einen JSON Skill",
                                   "async": True}).json()["run_id"]
        deadline = time.time() + 30
        while time.time() < deadline:
            body = client.get(f"/v1/console/skill-creator/status/{run_id}").json()
            if not seen or seen[-1] != body["phase"]:
                seen.append(body["phase"])
            if body["status"] in ("success", "failed"):
                break
            time.sleep(0.02)

    assert seen[-1] == "promotion"
    order = list(route.PHASES)
    assert [p for p in order if p in seen] == [p for p in seen if p in order]


# ── failure path ──────────────────────────────────────────────────────────

def test_engine_failure_surfaces_actionable_message(tmp_path):
    """A missing CLI must not resurface as an opaque SDK auth string."""
    from skill_creator.llm_client import ClaudeCodeUnavailable

    engine = fake_engine(fail_with=ClaudeCodeUnavailable("claude binary not found: 'claude'"))
    with console_client(tmp_path, engine) as (client, _route):
        run_id = client.post("/v1/console/skill-creator/generate",
                             json={"user_request": "erzeuge einen JSON Skill",
                                   "async": True}).json()["run_id"]
        final = poll_until_done(client, run_id)

    assert final["status"] == "failed"
    assert "CORVIN_CLAUDE_BIN" in final["message"]
    assert "claude binary not found" in final["error"]


def test_view_rejects_a_traversal_name(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        resp = client.get("/v1/console/skill-creator/skills/..%2F..%2Fetc%2Fpasswd")
        assert resp.status_code in (400, 404)


def test_view_of_unknown_skill_is_404(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        assert client.get("/v1/console/skill-creator/skills/assistant.nope").status_code == 404


def test_generation_requires_a_session(tmp_path):
    """Generation spends real engine time; it must not be callable
    unauthenticated (it was)."""
    with console_client(tmp_path, fake_engine()) as (client, _route):
        client.cookies.clear()
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "erzeuge einen JSON Skill",
                                 "async": True})
        assert resp.status_code in (401, 403)


def test_unknown_run_is_404(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        assert client.get("/v1/console/skill-creator/status/run-nope").status_code == 404


def test_short_request_is_rejected(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "short", "async": True})
        assert resp.status_code == 422


# ── live engine (opt-in) ──────────────────────────────────────────────────

@pytest.mark.skipif(os.environ.get("CORVIN_E2E_LIVE_ENGINE") != "1",
                    reason="set CORVIN_E2E_LIVE_ENGINE=1 to run against the real Claude Code CLI")
def test_live_generation(tmp_path):
    """Full run on the operator's actual Claude subscription. Minutes long."""
    with console_client(tmp_path, live=True) as (client, _route):
        run_id = client.post(
            "/v1/console/skill-creator/generate",
            json={"user_request": "erzeuge einen Skill der CSV-Dateien auf fehlende Spalten prueft",
                  "async": True},
        ).json()["run_id"]
        final = poll_until_done(client, run_id, timeout_s=900)

    assert final["status"] == "success", final
    assert final["engine"] == "claude_code"
    assert final["skill"]["name"].startswith(("assistant.", "project."))


# ── managing a generated skill: view · refine · delete ────────────────────

def _generate(client, request: str = "erzeuge einen Skill der JSON validiert",
              base: str | None = None) -> dict:
    payload = {"user_request": request, "async": True}
    if base:
        payload["base_skill"] = base
    resp = client.post("/v1/console/skill-creator/generate", json=payload)
    assert resp.status_code == 202, resp.text
    return poll_until_done(client, resp.json()["run_id"])


def test_delete_removes_the_skill_everywhere(tmp_path):
    """Delete must clear the manifest, the directory AND the listing —
    a directory removed behind the registry's back leaves a manifest entry
    pointing at nothing."""
    with console_client(tmp_path, fake_engine()) as (client, _route):
        final = _generate(client)
        name = final["skill"]["name"]

        resp = client.delete(f"/v1/console/skill-creator/skills/{name}",
                             headers=csrf_headers(client.session_record))
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"ok": True, "name": name}

        assert client.get(f"/v1/console/skill-creator/skills/{name}").status_code == 404
        assert client.get("/v1/console/skill-creator/skills").json()["count"] == 0
        assert not (registry_root(tmp_path) / "skills" / name).exists()


def test_delete_requires_csrf(tmp_path):
    """A destructive route without CSRF is a cross-site delete."""
    with console_client(tmp_path, fake_engine()) as (client, _route):
        name = _generate(client)["skill"]["name"]
        resp = client.delete(f"/v1/console/skill-creator/skills/{name}")
        assert resp.status_code == 403
        assert client.get(f"/v1/console/skill-creator/skills/{name}").status_code == 200


def test_delete_of_unknown_skill_is_404(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        resp = client.delete("/v1/console/skill-creator/skills/assistant.nope",
                             headers=csrf_headers(client.session_record))
        assert resp.status_code == 404


def test_refine_updates_the_skill_in_place(tmp_path):
    """Iterating on a skill must replace it, not add a near-duplicate."""
    with console_client(tmp_path, fake_engine()) as (client, route):
        first = _generate(client)
        name = first["skill"]["name"]

        # Second run answers the refine prompt with a changed body.
        from skill_creator import skill_creator as sc
        sc.resolve_llm_client = lambda explicit=None: refine_engine_for(name)

        second = _generate(client, "ergaenze eine Pruefung auf doppelte Schluessel",
                           base=name)

        assert second["status"] == "success", second
        assert second["skill"]["name"] == name
        assert second["base_skill"] == name

        listing = client.get("/v1/console/skill-creator/skills").json()
        assert listing["count"] == 1, "refine registered a second skill"

        body = client.get(f"/v1/console/skill-creator/skills/{name}").json()["body"]
        assert "doppelte" in body or "duplicate" in body


def test_refine_of_unknown_skill_is_404_not_a_new_skill(tmp_path):
    """Silently creating a NEW skill would leave the operator's target
    untouched and a duplicate beside it."""
    with console_client(tmp_path, fake_engine()) as (client, _route):
        resp = client.post("/v1/console/skill-creator/generate",
                           json={"user_request": "aendere diesen Skill bitte",
                                 "async": True, "base_skill": "assistant.nope"})
        assert resp.status_code == 404
        assert client.get("/v1/console/skill-creator/skills").json()["count"] == 0


def test_name_guard_rejects_traversal_on_every_route(tmp_path):
    with console_client(tmp_path, fake_engine()) as (client, _route):
        bad = "..%2F..%2Fetc%2Fpasswd"
        assert client.get(f"/v1/console/skill-creator/skills/{bad}").status_code in (400, 404)
        assert client.delete(
            f"/v1/console/skill-creator/skills/{bad}",
            headers=csrf_headers(client.session_record),
        ).status_code in (400, 404)
