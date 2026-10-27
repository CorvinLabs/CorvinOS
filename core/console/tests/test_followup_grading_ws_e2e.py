"""E2E: praise typed into the console web chat grades the previous turn's skills.

Drives turns through the REAL chat WebSocket route
(``/v1/console/chat/sessions/{sid}/stream``) with the fake claude CLI
(``bgscope_testkit``) and a real SkillForge registry + real consent store under a
sandboxed ``CORVIN_HOME``:

  turn 1: the CEL step binds skills -> ``chat_runtime`` computes the ids the
          rendered skill block carries (``followup_grading.injected_skill_ids``)
          and records them once the native ``claude`` spawn started with that
          block (``followup_grading.record_turn_skills``)
  turn 2: the user types the follow-up -> ``routes/chat.py::_run_turn`` grades it
          (``followup_grading.grade_followup`` ->
          ``skill_inject.grade_from_user_followup``) -> a grade lands on disk

Substituted, and none of it is under test: the CEL pipeline's OUTPUT (a bundle that
binds skills — the pipeline itself has its own suites), the two ship-dark CEL
flags that put the turn on that path, the house-rules classifier and the license
gate (as in ``test_chat_turn_outcome_ws_e2e.py``).

The project scope resolves to a REAL repo-shaped root (a tmp dir with a
``.corvin_repo`` marker, via ``CORVIN_PROJECT_ROOT``) — never blanked — so the
cross-tenant case (review c120 s4-2) runs the production scope resolution.

Negative controls in this file: no consent, ``spec.web_chat.outcome_grading: false``,
a negated praise, an incidental praise word in a long message, a skill the renderer
dropped, a turn no engine answered, an error answer, another tenant's project-scope
skill, a failing audit writer, a repeated praise on the same day — each writes no grade.
"""
from __future__ import annotations

import json
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


def _fixture(tmp_path: Path, *, is_error: bool = False) -> Path:
    text = "API Error: 500 overloaded" if is_error else "Here is the report."
    lines = [
        {"type": "system", "subtype": "init", "session_id": "fg-ws-e2e"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": text}]}, "session_id": "fg-ws-e2e"},
        {"type": "result", "subtype": "error_during_execution" if is_error else "success",
         "is_error": is_error, "result": text, "session_id": "fg-ws-e2e"},
    ]
    return kit.write_fixture(tmp_path / "fixture", "fg-ws-e2e", lines)


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "corvin"
    home.mkdir()
    proj = tmp_path / "shared_repo"           # a real repo-shaped project root
    proj.mkdir()
    (proj / ".corvin_repo").write_text("", encoding="utf-8")
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_PROJECT_ROOT", str(proj))
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
    _mk_skill(reg, SKILL, seed=0.6)

    # Put the turn on the CEL active-brain path, with a pipeline result that binds skills.
    real_is_enabled = cr._feature_flags.is_enabled
    monkeypatch.setattr(cr._feature_flags, "is_enabled",
                        lambda name, *a, **k: True if name in ("vibe_engineering",
                                                               "vibe_engineering_active")
                        else real_is_enabled(name, *a, **k))
    monkeypatch.setattr(cr, "_CEL_AVAILABLE", True)
    state = {"refs": lambda: [SkillRef(skill_id=SKILL, body="Write short reports.")]}

    async def _fake_cel(prompt, tenant_id, sess, **kw):  # noqa: ANN001, ARG001
        b = ContextBundle(task=prompt)
        b.skills_to_bind = state["refs"]()
        return b, {"stages": []}
    monkeypatch.setattr(cr, "_cel_run_full_async", _fake_cel)

    return SimpleNamespace(cr=cr, fg=fg, reg=reg, tmp=tmp_path, home=home, proj=proj,
                           state=state, SkillRef=SkillRef, monkeypatch=monkeypatch)


def _mk_skill(reg, name: str, *, scope: str = "user", seed: float = 0.3) -> None:
    reg.create(scope=scope, name=name, type="learned-experience",
               description="demo " + name, body_md="# Demo\n\nWrite short reports.\n",
               claim={"summary": "reports."})
    reg.grade(name, "seed", seed, notes="seed")


def _rec(tenant: str = "_default"):
    rec = MagicMock()
    rec.tenant_id = tenant
    rec.sid_fingerprint = f"fp-fg-ws-e2e-{tenant}"
    rec.tier = "owner"
    rec.token_fingerprint = ""      # local-operator session -> subject local-operator:<tenant>
    return rec


def _grant_consent(tenant: str = "_default") -> None:
    from core.compliance.consent_store import get_consent_store  # noqa: PLC0415
    get_consent_store(tenant).grant_consent(f"local-operator:{tenant}", "learning_feedback")


def _app():
    from fastapi import FastAPI  # noqa: PLC0415
    from corvin_console.routes import chat as chat_routes  # noqa: PLC0415
    app = FastAPI()
    app.include_router(chat_routes.router, prefix="/v1/console")
    return app, chat_routes


def _turns(env, texts, *, tenant: str = "_default", sess=None) -> list[dict]:
    from fastapi.testclient import TestClient  # noqa: PLC0415

    sess = sess or env.cr.create_session(tenant)
    app, chat_routes = _app()
    wire: list[dict] = []
    with (
        patch.object(chat_routes.session_auth, "load_session", return_value=_rec(tenant)),
        patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
              lambda *a, **k: None),
    ):
        client = TestClient(app)
        client.cookies.set("corvin_console_sid", "valid-sid")
        with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
            assert ws.receive_json()["type"] == "ready"
            for text in texts:
                ws.send_json({"type": "user", "text": text})
                while True:
                    ev = ws.receive_json()
                    wire.append(ev)
                    if ev.get("type") == "done":
                        break
    env.last_sess = sess
    return wire


def _two_turns(env, follow_up: str, **kw) -> list[dict]:
    return _turns(env, ["write me a short report on the backups", follow_up], **kw)


def _grades(env, name: str = SKILL, reg=None) -> list[dict]:
    return list((reg or env.reg).get(name).grades)


def _chain_records(tenant: str) -> list[dict]:
    from forge.paths import tenant_audit_chain  # noqa: PLC0415
    p = Path(tenant_audit_chain(tenant))
    return [json.loads(x) for x in p.read_text().splitlines()] if p.exists() else []


# ── baseline behaviour ──────────────────────────────────────────────────────

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
    # review c120 s1-8/9/10 repros, through the console path
    "alles andere als echt gut gemacht",
    "is that correct?",
    "Great, now everything is broken",
    "danke, und jetzt bitte die tests für das modul schreiben",
    "\"gut gemacht\" sagt der chef, aber ich nicht wirklich",
    "x" * 100000 + " gut gemacht",
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


# ── s1-1: only skills the rendered block carried ────────────────────────────

def test_only_skills_the_rendered_block_carried_are_graded(env) -> None:
    empty, over = "assistant.followup_gate2_denied", "assistant.followup_over_cap"
    for nm in (empty, over):
        _mk_skill(env.reg, nm)
    SR = env.SkillRef
    env.state["refs"] = lambda: (
        [SR(skill_id=SKILL, body="Write short reports."), SR(skill_id=empty, body="")]
        + [SR(skill_id=f"assistant.filler{i}", body="filler") for i in range(8)]
        + [SR(skill_id=over, body="beyond MAX_BINDINGS")])
    _grant_consent()
    _two_turns(env, "Danke!")
    assert [float(g["score"]) for g in _grades(env)] == [0.3, 0.9]
    assert len(_grades(env, empty)) == 1, _grades(env, empty)   # Gate-2 emptied: never injected
    assert len(_grades(env, over)) == 1, _grades(env, over)     # past MAX_BINDINGS: never injected


# ── s1-2: only turns an engine actually answered ────────────────────────────

def test_debug_engine_turn_is_not_graded(env) -> None:
    _grant_consent()
    _turns(env, ["/debug-engine write me a short report", "Danke!"])
    assert len(_grades(env)) == 1, _grades(env)


def test_error_answer_turn_is_not_graded(env, monkeypatch) -> None:
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(_fixture(env.tmp / "err", is_error=True)))
    _grant_consent()
    _two_turns(env, "Danke!")
    assert len(_grades(env)) == 1, _grades(env)


def test_cancelled_turn_is_not_graded(env, monkeypatch) -> None:
    """The engine started (snapshot recorded) but the operator cancelled it."""
    import time as _t  # noqa: PLC0415
    from fastapi.testclient import TestClient  # noqa: PLC0415
    slow = kit.write_fixture(env.tmp / "slowfx", "slow", [
        {"_t": 0, "type": "system", "subtype": "init", "session_id": "s"},
        {"_t": 40, "type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "text", "text": "late"}]}, "session_id": "s"},
        {"_t": 40, "type": "result", "subtype": "success", "is_error": False,
         "result": "late", "session_id": "s"},
    ])
    fast = str(_fixture(env.tmp / "fastfx"))
    monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", str(slow))
    _grant_consent()
    sess = env.cr.create_session("_default")
    app, chat_routes = _app()
    with (
        patch.object(chat_routes.session_auth, "load_session", return_value=_rec()),
        patch("corvin_console.routes._compute_license_gate.enforce_chat_turns",
              lambda *a, **k: None),
    ):
        client = TestClient(app)
        client.cookies.set("corvin_console_sid", "valid-sid")
        with client.websocket_connect(f"/v1/console/chat/sessions/{sess.sid}/stream") as ws:
            assert ws.receive_json()["type"] == "ready"
            ws.send_json({"type": "user", "text": "write me a short report"})
            while ws.receive_json().get("type") != "turn_started":
                pass
            deadline = _t.time() + 15
            while not env.fg._snapshots and _t.time() < deadline:   # engine really started
                _t.sleep(0.05)
            assert env.fg._snapshots, "positive control: the spawn recorded its skills"
            ws.send_json({"type": "cancel"})
            while ws.receive_json().get("type") != "done":
                pass
            monkeypatch.setenv("FAKE_CLAUDE_FIXTURE", fast)
            ws.send_json({"type": "user", "text": "Danke!"})
            while ws.receive_json().get("type") != "done":
                pass
    assert len(_grades(env)) == 1, _grades(env)


# ── s4-2: another tenant's (shared project scope) skill is never graded ─────

def test_follow_up_never_grades_another_tenants_project_scope_skill(env) -> None:
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
    shared, own = "assistant.shared_demo", "assistant.beta_own"
    acme = MultiSkillRegistry(tenant_id="acme", project_root=env.proj)
    _mk_skill(acme, shared, scope="project")
    beta = MultiSkillRegistry(tenant_id="beta")
    _mk_skill(beta, own)
    assert beta.find_scope(shared) == "project"   # production resolution reaches the shared repo
    SR = env.SkillRef
    env.state["refs"] = lambda: [SR(skill_id=shared, body="Write short reports."),
                                 SR(skill_id=own, body="Write short reports.")]
    _grant_consent("beta")
    _two_turns(env, "Gut gemacht!", tenant="beta")
    assert [float(g["score"]) for g in _grades(env, own, beta)] == [0.3, 0.9]  # positive control
    assert len(_grades(env, shared, MultiSkillRegistry(tenant_id="acme",
                                                       project_root=env.proj))) == 1
    beta_runs = [r["details"].get("run_id") for r in _chain_records("beta")
                 if r.get("event_type") == "skill.grade" and r.get("tool") == shared]
    assert beta_runs == [], beta_runs


# ── s4-6: no prompt text kept; chat delete drops the snapshot ───────────────

def test_snapshot_holds_no_prompt_text_and_console_never_rephrases(env) -> None:
    _grant_consent()
    prompt = "write me a short report on the backups zebrafinkquux"
    _turns(env, [prompt])
    snaps = list(env.fg._snapshots.values())
    assert len(snaps) == 1 and snaps[0]["skills"] == [SKILL]
    assert "zebrafinkquux" not in repr(snaps) and "user_text" not in snaps[0]
    # A near-repeat would be a rephrase on the bridge; the console has no prompt to compare.
    _turns(env, ["write me a short report on the backups zebrafinkquu"], sess=env.last_sess)
    assert len(_grades(env)) == 1, _grades(env)


def test_chat_delete_drops_the_snapshot(env) -> None:
    from fastapi.testclient import TestClient  # noqa: PLC0415
    from corvin_console import deps  # noqa: PLC0415
    _grant_consent()
    _turns(env, ["write me a short report on the backups"])
    assert len(env.fg._snapshots) == 1
    app, _ = _app()
    app.dependency_overrides[deps.require_csrf] = lambda: _rec()
    r = TestClient(app).delete(f"/v1/console/chat/sessions/{env.last_sess.sid}")
    assert r.status_code == 200, r.text
    assert env.fg._snapshots == {}


# ── s4-3: audit-first, through the console path ─────────────────────────────

@pytest.mark.parametrize("exc", [RuntimeError, OSError])
def test_failed_skill_grade_record_persists_nothing_and_chain_stays_green(env, exc) -> None:
    from skill_forge import registry as sreg  # noqa: PLC0415
    from core.compliance.corvin_compliance_reports import tripwire  # noqa: PLC0415
    real = sreg._write_event

    def boom(*a, **k):
        if a[1] == "skill.grade":
            raise exc("writer down")
        return real(*a, **k)
    env.monkeypatch.setattr(sreg, "_write_event", boom)
    _grant_consent()
    _two_turns(env, "Gut gemacht!")
    assert len(_grades(env)) == 1, _grades(env)                 # nothing persisted
    recs = _chain_records("_default")
    assert not [r for r in recs if str((r.get("details") or {}).get("action", ""))
                .startswith("skill.outcome_graded")]            # no success reported
    assert all("hash" in r for r in recs)                       # no unchained line
    from forge.paths import tenant_audit_chain  # noqa: PLC0415
    from forge.security_events import verify_chain  # noqa: PLC0415
    assert verify_chain(Path(tenant_audit_chain("_default"))) == (True, [])
    assert tripwire.audit_chain_intact().ok


# ── decision: one organic follow-up grade per skill per channel per UTC day ─

def test_repeated_praise_grades_once_per_day(env) -> None:
    _grant_consent()
    _turns(env, ["write me a short report", "Gut gemacht!", "Gut gemacht!", "Danke!"])
    _turns(env, ["another report please", "Gut gemacht!"])          # a new chat, same day
    assert [float(g["score"]) for g in _grades(env)] == [0.3, 0.9], _grades(env)
    note = str(_grades(env)[-1]["notes"])
    assert "dd=" in note and "console" not in note                  # a hash, never the id
