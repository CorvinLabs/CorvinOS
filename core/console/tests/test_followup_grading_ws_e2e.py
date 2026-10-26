"""E2E: praise typed into the console web chat grades the previous turn's skills.

Drives TWO turns through the REAL chat WebSocket route
(``/v1/console/chat/sessions/{sid}/stream``) with the fake claude CLI
(``bgscope_testkit``) and a real SkillForge registry + real consent store under a
sandboxed ``CORVIN_HOME``:

  turn 1: the CEL step binds ``assistant.followup_demo`` -> ``chat_runtime`` records
          the snapshot (call site 1, ``followup_grading.record_turn_skills``)
  turn 2: the user types the follow-up -> ``routes/chat.py::_run_turn`` grades it
          (call site 2, ``followup_grading.grade_followup`` ->
          ``skill_inject.grade_from_user_followup``) -> a grade lands on disk

Substituted, and none of it is under test: the CEL pipeline's OUTPUT (a bundle that
binds one skill — the pipeline itself has its own suites), the two ship-dark CEL
flags that put the turn on that path, the house-rules classifier and the license
gate (as in ``test_chat_turn_outcome_ws_e2e.py``).

Negative controls in this file: no consent, ``spec.web_chat.outcome_grading: false``,
a negated praise, an incidental praise word in a long message — each writes no grade.
"""
from __future__ import annotations

import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

_THIS = Path(__file__).resolve()
_REPO = _THIS.parents[3]
for _p in (_REPO, _REPO / "core" / "console", _REPO / "corvin_operator" / "bridges" / "shared",
           _REPO / "corvin_operator" / "bridges" / "shared" / "tests",
           _REPO / "corvin_operator" / "forge", _REPO / "corvin_operator" / "skill-forge"):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import bgscope_testkit as kit  # noqa: E402

SKILL = "assistant.followup_demo"
pytestmark = pytest.mark.skipif(sys.platform.startswith("win"),
                                reason="POSIX launcher for the fake claude CLI")


def _launcher(tmp: Path) -> Path:
    bin_dir = tmp / "fake-bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    launcher = bin_dir / "claude"
    launcher.write_text("#!/bin/sh\n" f"exec {sys.executable!r} {str(kit.FAKE)!r} \"$@\"\n",
                        encoding="utf-8")
    launcher.chmod(launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP)
    return launcher


def _fixture(tmp_path: Path) -> Path:
    lines = [
        {"type": "system", "subtype": "init", "session_id": "fg-ws-e2e"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "Here is the report."}]}, "session_id": "fg-ws-e2e"},
        {"type": "result", "subtype": "success", "is_error": False,
         "result": "Here is the report.", "session_id": "fg-ws-e2e"},
    ]
    return kit.write_fixture(tmp_path / "fixture", "fg-ws-e2e", lines)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    home.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_PROJECT_ROOT", "")   # project scope -> tenant root, never the repo
    monkeypatch.setenv("CORVIN_OS_ENGINE", "claude_code")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-dummy-not-a-credential")
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.setenv("CORVIN_CLAUDE_BIN", str(_launcher(tmp_path)))
    monkeypatch.setenv("FAKE_CLAUDE_SPEEDUP", "4.0")
    monkeypatch.setenv("FAKE_CLAUDE_SILENT_S", "0.0")
    monkeypatch.setenv("FAKE_CLAUDE_PIDFILE", str(tmp_path / "fake.pid"))
    monkeypatch.setenv("FAKE_CLAUDE_SPAWNLOG", str(tmp_path / "spawns.log"))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(_fixture(tmp_path)))
    import house_rules as _hr  # type: ignore  # noqa: PLC0415
    monkeypatch.setattr(_hr, "_house_rules_classifier",
                        lambda task, rules, auth, **kw: ("", 1.0, "test-benign"))

    from corvin_console import chat_runtime as cr  # noqa: PLC0415
    from corvin_console import followup_grading as fg  # noqa: PLC0415
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
    from corvin_operator.context_engineering.stages.base import ContextBundle  # noqa: PLC0415
    from corvin_operator.context_engineering.stages.binding import SkillRef  # noqa: PLC0415

    with fg._guard:
        fg._snapshots.clear()

    reg = MultiSkillRegistry(tenant_id="_default")
    reg.create(scope="user", name=SKILL, type="learned-experience",
               description="report writing demo", body_md="# Report\n\nWrite short reports.\n",
               claim={"summary": "reports."})
    reg.grade(SKILL, "seed", 0.6, notes="seed")

    # Put the turn on the CEL active-brain path, with a pipeline result that binds SKILL.
    real_is_enabled = cr._feature_flags.is_enabled
    monkeypatch.setattr(cr._feature_flags, "is_enabled",
                        lambda name, *a, **k: True if name in ("vibe_engineering",
                                                               "vibe_engineering_active")
                        else real_is_enabled(name, *a, **k))
    monkeypatch.setattr(cr, "_CEL_AVAILABLE", True)

    async def _fake_cel(prompt, tenant_id, sess, **kw):  # noqa: ANN001, ARG001
        b = ContextBundle(task=prompt)
        b.skills_to_bind = [SkillRef(skill_id=SKILL, body="Write short reports.")]
        return b, {"stages": []}
    monkeypatch.setattr(cr, "_cel_run_full_async", _fake_cel)

    rec = MagicMock()
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "fp-fg-ws-e2e"
    rec.tier = "owner"
    rec.token_fingerprint = ""      # local-operator session -> subject local-operator:_default
    return SimpleNamespace(cr=cr, reg=reg, rec=rec, tmp=tmp_path)


def _grant_consent() -> None:
    from core.compliance.consent_store import get_consent_store  # noqa: PLC0415
    get_consent_store("_default").grant_consent("local-operator:_default", "learning_feedback")


def _two_turns(env, follow_up: str) -> list[dict]:
    from fastapi import FastAPI  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from corvin_console.routes import chat as chat_routes  # noqa: PLC0415

    sess = env.cr.create_session("_default")
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    wire: list[dict] = []
    with (
        patch.object(chat_routes.session_auth, "load_session", return_value=env.rec),
        patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
              lambda *a, **k: None),
    ):
        client = TestClient(app)
        client.cookies.set("corvin_console_sid", "valid-sid")
        with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
            assert ws.receive_json()["type"] == "ready"
            for text in ("write me a short report on the backups", follow_up):
                ws.send_json({"type": "user", "text": text})
                while True:
                    ev = ws.receive_json()
                    wire.append(ev)
                    if ev.get("type") == "done":
                        break
    return wire


def _grades(env) -> list[dict]:
    return list(env.reg.get(SKILL).grades)


def test_praise_in_the_follow_up_grades_the_previous_turns_skill(env) -> None:
    _grant_consent()
    assert len(_grades(env)) == 1
    wire = _two_turns(env, "Gut gemacht!")
    assert sum(1 for e in wire if e.get("type") == "done") == 2, wire
    grades = _grades(env)
    assert len(grades) == 2, grades
    assert float(grades[-1]["score"]) == 0.9
    note = str(grades[-1].get("notes", ""))
    assert "outcome (approval)" in note
    # Privacy: nothing of the user's text reaches the grade record.
    assert "gut" not in note.lower() and "report" not in note.lower()


def test_rejection_in_the_follow_up_grades_low(env) -> None:
    _grant_consent()
    _two_turns(env, "Nein, das ist falsch.")
    grades = _grades(env)
    assert len(grades) == 2 and float(grades[-1]["score"]) == 0.1, grades


@pytest.mark.parametrize("follow_up", [
    "nicht gut gemacht",
    "That was not a good job",
    "Now please also add the restore steps, the great thing about this setup "
    "is that it runs nightly and keeps fourteen days of history",
])
def test_negated_or_incidental_praise_writes_no_grade(env, follow_up) -> None:
    _grant_consent()
    _two_turns(env, follow_up)
    assert len(_grades(env)) == 1, _grades(env)


def test_without_learning_feedback_consent_no_grade_is_written(env) -> None:
    _two_turns(env, "Gut gemacht!")
    assert len(_grades(env)) == 1, _grades(env)


def test_tenant_opt_out_outcome_grading_false_writes_no_grade(env, monkeypatch) -> None:
    _grant_consent()
    real_spec = env.cr._tenant_spec
    monkeypatch.setattr(env.cr, "_tenant_spec",
                        lambda tid: {**real_spec(tid), "web_chat": {"outcome_grading": False}})
    _two_turns(env, "Gut gemacht!")
    assert len(_grades(env)) == 1, _grades(env)
