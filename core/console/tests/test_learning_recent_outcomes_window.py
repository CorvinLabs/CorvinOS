"""The optimizer's ground truth must be the NEWEST outcomes (round-4 review, F3).

``outcome_sink.recent_outcomes`` asked ``EventStore.query_events`` for
``limit=5000`` and then sliced ``events[-10:]``. ``query_events`` selected the
**oldest** 5000, so after a tenant's 5000th OUTCOME the "last ten outcomes" fed
into ``SkillAdapter.run_optimizer_epoch`` were outcomes #4991–#5000 of all time
— a constant, forever. That is the sole evidence the optimizer uses to accept or
reject a config hypothesis for ``os.delegation_router``.

Driven through the REAL boundary: an HTTP ``POST /v1/console/learning/feedback``
against the real router, with more than 5000 outcomes on disk.

Run:  .venv/bin/python -m pytest core/console/tests/test_learning_recent_outcomes_window.py
"""
from __future__ import annotations

import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
for _p in (str(_REPO), str(_REPO / "core" / "console"), str(_REPO / "core" / "plugins")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

TENANT = "_default"
_PURGED = ("corvin_console", "corvin_gateway", "forge")


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _console(tmp_path: Path):
    home = tmp_path / "corvin_home"
    (home / "tenants" / TENANT / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / TENANT / "global" / "console" / "sessions").mkdir(parents=True)

    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = TENANT
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    preloaded = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    emitter = None
    try:
        _reset_modules()
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from corvin_console import auth as _auth
        from corvin_console.app import router

        # Boot the ACP registry with a learning backend on the sandbox store —
        # this is what makes ``recent_outcomes`` read a REAL store, i.e. what
        # puts the defect on the route's live path.
        from core.learning.event_emitter import EventEmitter
        from core.learning.event_store import EventStore
        from core.skills.boot import boot_skills
        from core.skills.skill_registry_phase1 import LearningEmitterBackend

        emitter = EventEmitter(EventStore(home / "tenants" / TENANT))
        boot_skills(
            TENANT,
            audit_emit=lambda *_a, **_k: None,
            learning_backend=LearningEmitterBackend(emitter, session_id="test"),
        )

        rec = _auth.create_session(tenant_id=TENANT, token_fingerprint="outcomes-fp")
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        client.headers.update({"X-CSRF-Token": csrf})
        yield client, home
    finally:
        try:
            if emitter is not None:
                emitter.stop(timeout=5.0)
        except Exception:  # noqa: BLE001
            pass
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


def _seed_outcomes(home: Path, *, failures: int, successes: int) -> None:
    """Write ``failures`` failed outcomes, then ``successes`` successful ones.

    Written straight to the store's JSONL file: the point is the READ path, and
    seeding 5100 events through the hash chain would take minutes.
    """
    from core.learning.learning_events import EventType, LearningEvent

    events_dir = home / "tenants" / TENANT / "learning" / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for index in range(failures + successes):
        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id="os.delegation_router",
            tenant_id=TENANT,
            signal={
                "task_id": f"t{index}",
                "status": "completed" if index >= failures else "failed",
                "success": index >= failures,
                "seq": index,
            },
            lom="core/console/tests/test_learning_recent_outcomes_window.py",
        )
        record = event.to_dict()
        record["timestamp"] = "2026-09-06T00:00:00Z"
        lines.append(json.dumps(record, separators=(",", ":")))
    (events_dir / "2026-09-06.jsonl").write_text("\n".join(lines) + "\n")


def test_feedback_route_sees_the_newest_outcomes_past_the_query_limit(tmp_path):
    """5000 old failures + 100 recent successes ⇒ the route reports 10/10."""
    with _console(tmp_path) as (client, home):
        _seed_outcomes(home, failures=5000, successes=100)

        res = client.post(
            "/v1/console/learning/feedback",
            json={"task_id": "t-latest", "outcome_quality": "good", "would_repeat": True},
        )
        assert res.status_code == 200, res.text

        recent = res.json()["recent_outcomes"]
        assert recent == {"successes": 10, "total": 10}, (
            "the optimizer must see the ten MOST RECENT outcomes; "
            f"got {recent} (0/10 means it is reading outcomes #4991–#5000 again)"
        )


def test_the_window_advances_when_a_new_outcome_is_recorded(tmp_path):
    """Past the limit, the window must still MOVE — that is what froze before."""
    with _console(tmp_path) as (client, home):
        _seed_outcomes(home, failures=5100, successes=0)

        first = client.post(
            "/v1/console/learning/feedback",
            json={"task_id": "t-a", "outcome_quality": "bad", "would_repeat": False},
        )
        assert first.status_code == 200, first.text
        assert first.json()["recent_outcomes"] == {"successes": 0, "total": 10}

        # Ten successes arrive after the 5100 failures.
        _seed_outcomes(home, failures=5100, successes=10)

        second = client.post(
            "/v1/console/learning/feedback",
            json={"task_id": "t-b", "outcome_quality": "good", "would_repeat": True},
        )
        assert second.status_code == 200, second.text
        assert second.json()["recent_outcomes"] == {"successes": 10, "total": 10}, (
            "a new outcome must enter the optimizer's window"
        )
