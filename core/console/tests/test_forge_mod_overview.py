"""Console side of the mod feature (CONCEPT-0118 phase 3, ADR-2285).

Two things are asserted through the REAL router, a REAL authenticated session and the REAL SkillForge
registry, in a sandboxed ``CORVIN_HOME`` (nothing here reads or writes the live install):

* ``GET /v1/console/forge/skills`` serves the same grade count and mean as the SkillForge registry (the
  source the ``skill_list`` MCP tool and therefore a generated mod's ``/skill-info`` read), plus the quiz-card
  count from the mod generator's own parser. ``null`` means unknown (a malformed quiz), never zero.
* ``GET /v1/console/forge/mod-activity`` lists ``mod.command_executed`` records of THE SESSION'S tenant only,
  says how much of the chain it read, and never reports a window as the whole history.
"""
from __future__ import annotations

import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in [
    str(_REPO / "corvin_operator"),
    str(_REPO / "corvin_operator" / "license"),
    str(_REPO / "corvin_operator" / "forge"),
    str(_REPO / "corvin_operator" / "skill-forge"),
    str(_REPO / "core" / "console"),
    str(_REPO / "corvin_operator" / "bridges" / "shared"),
    str(_REPO / "core" / "plugins"),
]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge", "skill_forge")
_EVENT = "mod.command_executed"

QUIZ_BODY = """# Quiz skill

## quiz
- first | What is the first thing? | alpha, beta | The first thing is alpha.
- second | And the second? | gamma | It is gamma.
"""


def _snapshot() -> dict:
    return {k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)}


def _reset(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED_PREFIXES):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _sandbox(tmp_path: Path, tenants: tuple[str, ...] = ("_default",)):
    """Sandboxed CORVIN_HOME with one authenticated client per tenant (pattern of test_admin_route.py)."""
    home = tmp_path / "corvin_home"
    for tid in tenants:
        for sub in ("auth", "forge", "console/sessions"):
            (home / "tenants" / tid / "global" / sub).mkdir(parents=True)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_PLUGIN_SLOT_DIR", "FORGE_ROOT",
            "CORVIN_AUDIT_ANCHOR_KEY")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_PLUGIN_SLOT_DIR"] = str(home / "plugin-slot")
    os.environ["CORVIN_TENANT_ID"] = tenants[0]
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    os.environ["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "anchor.key")
    os.environ.pop("FORGE_ROOT", None)
    preloaded = _snapshot()
    try:
        _reset()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        clients = {}
        for tid in tenants:
            rec = _auth.create_session(tenant_id=tid, token_fingerprint="test-fp")
            client = TestClient(app, raise_server_exceptions=False)
            client.cookies.set("corvin_console_sid", rec.sid)
            clients[tid] = client
        anonymous = TestClient(app, raise_server_exceptions=False)
        yield clients, anonymous, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset(restore=preloaded)


def _chain(home: Path, tid: str) -> Path:
    return home / "tenants" / tid / "global" / "forge" / "audit.jsonl"


def _write(chain: Path, event: str, **details) -> None:
    """Append through the REAL writer. The writer refuses a record whose ``tenant_id`` is not the process
    tenant (``AuditTenantMismatch``), so the process tenant is switched to the record's own for the call."""
    from forge import security_events as se  # noqa: PLC0415

    tid = details.get("tenant_id")
    prev = os.environ.get("CORVIN_TENANT_ID")
    if tid:
        os.environ["CORVIN_TENANT_ID"] = tid
    try:
        se.write_event(chain, event, details=details)
    finally:
        if prev is None:
            os.environ.pop("CORVIN_TENANT_ID", None)
        else:
            os.environ["CORVIN_TENANT_ID"] = prev


def _append_raw(chain: Path, event: str, **details) -> None:
    """A record the writer would have refused (a foreign tenant inside this chain: corruption or tampering).
    The reader must skip it on its own; it does not rely on the writer having kept the chain clean."""
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": time.time(), "event_type": event, "severity": "INFO", "details": details}) + "\n")


# ── skills: the same numbers the SkillForge registry (and so the mod) serves ──

def test_grades_and_quiz_cards_agree_with_the_skillforge_registry(tmp_path):
    with _sandbox(tmp_path) as (clients, _anon, _home):
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

        reg = MultiSkillRegistry(tenant_id="_default")
        reg.create(scope="user", name="assistant.quiz_one", type="domain", description="d", body_md=QUIZ_BODY)
        reg.create(scope="user", name="assistant.plain", type="domain", description="d", body_md="# plain\n")
        reg.create(scope="user", name="assistant.bad_quiz", type="domain", description="d",
                   body_md="# bad\n\n## quiz\n- only | three | parts\n")
        reg.grade("assistant.quiz_one", "r1", 1.0)
        reg.grade("assistant.quiz_one", "r2", 0.5)
        reg.grade("assistant.plain", "r1", 0.25)

        body = clients["_default"].get("/v1/console/forge/skills").json()
        by_name = {s["name"]: s for s in body["skills"]}
        # Only the scope both sides share. The registry also lists the TASK scope, which lives under
        # /tmp/.corvin/tasks/<id> (outside CORVIN_HOME, so outside this sandbox) and which the console does
        # not read by design; a skill created there by another process would otherwise leak into this test.
        registry = {spec.name: spec for scope, spec in reg.list_with_scope() if scope == "user"}
        assert set(registry) == {"assistant.quiz_one", "assistant.plain", "assistant.bad_quiz"}
        assert set(registry) <= set(by_name), "the console must list every skill the registry lists"

        for name, spec in registry.items():
            item = by_name[name]
            assert item["grade_count"] == spec.n_grades, name
            if spec.n_grades:
                assert item["mean_score"] == pytest.approx(spec.mean_score, abs=0.001), name
            else:
                assert item["mean_score"] is None, name

        assert by_name["assistant.quiz_one"]["quiz_cards"] == 2
        assert by_name["assistant.plain"]["quiz_cards"] == 0
        # A malformed quiz is unknown, not "no quiz": zero would claim the skill has none.
        assert by_name["assistant.bad_quiz"]["quiz_cards"] is None


def test_quiz_cards_is_null_when_the_generator_is_not_on_this_build(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (clients, _anon, _home):
        from corvin_console.routes import forge_unified  # noqa: PLC0415
        from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415

        MultiSkillRegistry(tenant_id="_default").create(
            scope="user", name="assistant.quiz_one", type="domain", description="d", body_md=QUIZ_BODY)
        monkeypatch.setattr(forge_unified, "_MOD_MODULE", None)  # "not available on this build"
        item = next(s for s in clients["_default"].get("/v1/console/forge/skills").json()["skills"]
                    if s["name"] == "assistant.quiz_one")
        assert item["quiz_cards"] is None


# ── mod activity ──────────────────────────────────────────────────────────────

def test_activity_lists_only_the_sessions_tenant_newest_first(tmp_path):
    with _sandbox(tmp_path, tenants=("_default", "other")) as (clients, _anon, home):
        a, b = _chain(home, "_default"), _chain(home, "other")
        _write(a, _EVENT, mod="corvin-quiz-mod", command="quiz-answer", skill="s.one", score=1.0, tenant_id="_default")
        _write(a, "plugin.execution_timeout", plugin_id="p", boot_layer="x", timeout_ms=1, tenant_id="_default")
        _write(a, _EVENT, mod="corvin-skills-mod", command="skill-info", skill="s.two", tenant_id="_default")
        _write(b, _EVENT, mod="corvin-quiz-mod", command="quiz-answer", skill="other.skill", score=0.0, tenant_id="other")

        mine = clients["_default"].get("/v1/console/forge/mod-activity").json()
        assert mine["tenant_id"] == "_default" and mine["reported_by"] == "mod"
        assert [e["skill"] for e in mine["events"]] == ["s.two", "s.one"]  # newest first, other event type absent
        assert mine["events"][1] == {
            "ts": mine["events"][1]["ts"], "mod": "corvin-quiz-mod", "command": "quiz-answer",
            "skill": "s.one", "score": 1.0,
        }
        assert mine["window"]["complete"] is True

        theirs = clients["other"].get("/v1/console/forge/mod-activity").json()
        assert [e["skill"] for e in theirs["events"]] == ["other.skill"]
        assert "s.one" not in json.dumps(theirs) and "s.two" not in json.dumps(theirs)


def test_a_record_naming_another_tenant_inside_my_chain_is_skipped(tmp_path):
    with _sandbox(tmp_path) as (clients, _anon, home):
        chain = _chain(home, "_default")
        _write(chain, _EVENT, mod="m", command="c", skill="mine", tenant_id="_default")
        _append_raw(chain, _EVENT, mod="m", command="c", skill="stray", tenant_id="someone-else")
        skills = [e["skill"] for e in clients["_default"].get("/v1/console/forge/mod-activity").json()["events"]]
        assert skills == ["mine"]


def test_the_window_is_reported_and_a_short_window_is_not_the_whole_history(tmp_path, monkeypatch):
    with _sandbox(tmp_path) as (clients, _anon, home):
        from corvin_console.routes import forge_unified  # noqa: PLC0415

        chain = _chain(home, "_default")
        _write(chain, _EVENT, mod="m", command="c", skill="old", tenant_id="_default")
        for i in range(30):  # padding that pushes "old" out of a small window
            _write(chain, "plugin.execution_timeout", plugin_id=f"p{i}", boot_layer="x", timeout_ms=i, tenant_id="_default")
        _write(chain, _EVENT, mod="m", command="c", skill="new", tenant_id="_default")
        size = chain.stat().st_size

        monkeypatch.setattr(forge_unified, "_MOD_ACTIVITY_WINDOW_BYTES", 1500)
        body = clients["_default"].get("/v1/console/forge/mod-activity").json()
        assert body["window"] == {"bytes_scanned": 1500, "chain_bytes": size, "complete": False}
        assert [e["skill"] for e in body["events"]] == ["new"], "'old' is outside the window and must not appear"

        monkeypatch.setattr(forge_unified, "_MOD_ACTIVITY_WINDOW_BYTES", size + 10)
        full = clients["_default"].get("/v1/console/forge/mod-activity").json()
        assert full["window"]["complete"] is True
        assert [e["skill"] for e in full["events"]] == ["new", "old"]


def test_no_chain_yet_is_an_empty_complete_window(tmp_path):
    with _sandbox(tmp_path) as (clients, _anon, _home):
        body = clients["_default"].get("/v1/console/forge/mod-activity").json()
        assert body["events"] == [] and body["count"] == 0
        assert body["window"]["complete"] is True


def test_an_unauthenticated_request_is_refused(tmp_path):
    with _sandbox(tmp_path) as (_clients, anonymous, _home):
        assert anonymous.get("/v1/console/forge/mod-activity").status_code in (401, 403)
