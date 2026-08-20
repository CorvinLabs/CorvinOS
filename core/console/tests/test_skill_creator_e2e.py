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
def console_client(tmp_path: Path, engine=None, *, live: bool = False):
    """Mount the console router and yield a TestClient over real HTTP."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from corvin_console.routes import skill_creator_api as route
    from skill_forge import skill_creator as sc

    app = FastAPI()
    app.include_router(route.router, prefix="/v1/console")

    orig_resolve = sc.resolve_llm_client
    orig_runs = dict(route._generation_runs)
    orig_stats = dict(route._skill_stats)
    orig_orch = route.SkillCreatorOrchestrator

    if not live:
        sc.resolve_llm_client = lambda explicit=None: engine

    # Keep generated skills out of the operator's real ~/.claude/skills.
    def _orchestrator(**kwargs):
        kwargs.setdefault("skills_dir", str(tmp_path / "skills"))
        return orig_orch(**kwargs)

    route.SkillCreatorOrchestrator = _orchestrator
    route._generation_runs.clear()
    route._skill_stats.update({"total_generated": 0, "avg_quality": 0.0,
                               "total_iterations": 0, "last_generated_at": None})
    try:
        yield TestClient(app, raise_server_exceptions=False), route
    finally:
        sc.resolve_llm_client = orig_resolve
        route.SkillCreatorOrchestrator = orig_orch
        route._generation_runs.clear()
        route._generation_runs.update(orig_runs)
        route._skill_stats.clear()
        route._skill_stats.update(orig_stats)


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

        # Promoted to disk, into the sandboxed skills dir (never ~/.claude).
        promoted = tmp_path / "skills" / "assistant_check_json_syntax.md"
        assert promoted.exists()
        assert "name: assistant.check_json_syntax" in promoted.read_text()

        # The listing endpoint answers over the same HTTP boundary.
        listed = client.get("/v1/console/skill-creator/skills")
        assert listed.status_code == 200
        assert "skills" in listed.json()

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
    from skill_forge.llm_client import ClaudeCodeUnavailable

    engine = fake_engine(fail_with=ClaudeCodeUnavailable("claude binary not found: 'claude'"))
    with console_client(tmp_path, engine) as (client, _route):
        run_id = client.post("/v1/console/skill-creator/generate",
                             json={"user_request": "erzeuge einen JSON Skill",
                                   "async": True}).json()["run_id"]
        final = poll_until_done(client, run_id)

    assert final["status"] == "failed"
    assert "CORVIN_CLAUDE_BIN" in final["message"]
    assert "claude binary not found" in final["error"]


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
