"""F-A9 (2026-09-07): GDPR Art. 17 coverage guard + the new handlers + the CCC wiring.

1. ``LearningEventHandler`` / ``InfiniteSessionHandler`` are in the real chain
   and actually erase the subject's data from a temp CORVIN_HOME.
2. The CCC ``/erase`` route (``chat_router.dispatch``) drives the REAL
   ``ErasureOrchestrator`` (registry dispatch is the real boundary for the
   router: ``chat_runtime`` calls ``dispatch()`` with the session's tenant).
3. Coverage guard: every directory the reachable writers create under a temp
   tenant home is claimed by a handler (``COVERED_DIRS``) or listed as
   content-free (``NON_PERSONAL_DIRS``) — a new persistent store cannot ship
   without an erasure path.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]
for _p in (_REPO / "operator" / "bridges" / "shared", _REPO / "operator" / "forge",
           _REPO / "core" / "console"):
    if str(_p) not in sys.path:
        sys.path.append(str(_p))


@pytest.fixture
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv("CORVIN_HOME", str(h))
    monkeypatch.delenv("VOICE_AUDIT_PATH", raising=False)
    monkeypatch.delenv("FORGE_ROOT", raising=False)
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")
    monkeypatch.setenv("CORVIN_AUDIT_ANCHOR_KEY", str(tmp_path / "anchor.key"))
    from forge import security_events as se
    monkeypatch.setattr(se, "_ANCHOR_KEY", None)
    monkeypatch.setattr(se, "_ANCHOR_KEY_LOADED", False)
    return h


SUBJECT = "web:sess-erase-me"
OTHER = "web:sess-keep"


def _seed_learning(home: Path) -> Path:
    from datetime import datetime
    from core.learning.event_persistence import EventStore
    from core.learning.event_schema import LearningEvent, LearningEventType

    store = EventStore("_default")
    for uid in (SUBJECT, OTHER, SUBJECT):
        ev = LearningEvent(event_type=list(LearningEventType)[0], tenant_id="_default",
                           instance_id="inst-1", skill_name="os.test", session_id=uid,
                           timestamp_utc=datetime.utcnow(), user_id=uid, payload={"count": 1})
        asyncio.run(store.write_event(ev, tenant_id="_default"))
    side = home / "tenants" / "_default" / "learning" / "decisions.jsonl"
    side.write_text(json.dumps({"user_id": SUBJECT, "decision": "a"}) + "\n"
                    + json.dumps({"user_id": OTHER, "decision": "b"}) + "\n")
    return store.events_dir


def _seed_infinite_session(home: Path) -> None:
    isr = home / "tenants" / "_default" / "infinite_session"
    (isr / "snapshots" / SUBJECT).mkdir(parents=True)
    (isr / "snapshots" / SUBJECT / "phase-1.json").write_text(json.dumps({"session_id": SUBJECT, "state": {}}))
    (isr / "snapshots" / "task-x").mkdir(parents=True)
    (isr / "snapshots" / "task-x" / "phase-1.json").write_text(json.dumps({"session_id": SUBJECT, "state": {}}))
    (isr / "snapshots" / "task-x" / "phase-2.json").write_text(json.dumps({"session_id": OTHER, "state": {}}))
    (isr / "rollback").mkdir(parents=True)
    (isr / "rollback" / "log.jsonl").write_text(
        json.dumps({"session_id": SUBJECT, "op": 1}) + "\n" + json.dumps({"session_id": OTHER, "op": 2}) + "\n")
    ck = home / "tenants" / "_default" / "sessions" / SUBJECT / "checkpoints"
    ck.mkdir(parents=True)
    (ck / "c1.json").write_text("{}")


def test_learning_handler_erases_subject_only(home):
    events_dir = _seed_learning(home)
    from erasure_handlers import LearningEventHandler

    r = LearningEventHandler(tenant_id="_default").purge(SUBJECT, "er-test")
    assert r.status.value == "applied" and r.count >= 3, r
    text = "\n".join(p.read_text() for p in events_dir.glob("*.jsonl"))
    assert SUBJECT not in text and OTHER in text
    side = (home / "tenants" / "_default" / "learning" / "decisions.jsonl").read_text()
    assert SUBJECT not in side and OTHER in side
    assert LearningEventHandler(tenant_id="_default").purge(SUBJECT, "er-2").status.value == "skipped"


def test_infinite_session_handler_erases_subject_only(home):
    _seed_infinite_session(home)
    from erasure_handlers import InfiniteSessionHandler

    r = InfiniteSessionHandler(tenant_id="_default").purge(SUBJECT, "er-test")
    assert r.status.value == "applied", r
    isr = home / "tenants" / "_default" / "infinite_session"
    assert not (isr / "snapshots" / SUBJECT).exists()
    assert not (isr / "snapshots" / "task-x" / "phase-1.json").exists()
    assert (isr / "snapshots" / "task-x" / "phase-2.json").exists()
    log = (isr / "rollback" / "log.jsonl").read_text()
    assert SUBJECT not in log and OTHER in log
    assert not (home / "tenants" / "_default" / "sessions" / SUBJECT).exists()


def test_real_chain_includes_the_new_layers():
    from erasure_handlers import real_handler_chain
    ids = [h.layer_id for h in real_handler_chain(tenant_id="_default")]
    assert "L-learning" in ids and "L-infinite-session" in ids


def test_ccc_erase_dispatch_runs_the_real_orchestrator(home):
    _seed_learning(home)
    _seed_infinite_session(home)
    from corvin_console import chat_router
    from entity_extract import EntityPlan  # type: ignore

    plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": SUBJECT}, confidence=1.0)
    result = asyncio.run(chat_router.dispatch(plan, "_default"))
    assert result.entity_type == "erasure_request"
    assert result.status == "created", result
    assert result.entity_id and result.entity_id.startswith("er-")
    assert SUBJECT not in json.dumps(result.payload)  # subject never in the payload
    layers = {l["layer_id"]: l for l in result.payload["layers"]}
    assert layers["L-learning"]["status"] == "applied"
    assert layers["L-infinite-session"]["status"] == "applied"
    trail = home / "tenants" / "_default" / "global" / "erasure" / f"{result.entity_id}.json"
    assert trail.exists()
    chain = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    types = [json.loads(l)["event_type"] for l in chain.read_text().splitlines()]
    assert "erasure.requested" in types and "erasure.completed" in types
    from forge import security_events as se
    ok, problems = se.verify_chain(chain)
    assert ok, problems


def test_ccc_erase_rejects_invalid_subject(home):
    from corvin_console import chat_router
    from entity_extract import EntityPlan  # type: ignore

    plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": "alice@example.com"}, confidence=1.0)
    result = asyncio.run(chat_router.dispatch(plan, "_default"))
    assert result.status == "error" and result.entity_id is None


def test_every_written_directory_is_claimed_by_a_handler(home):
    """Boot the writers reachable in-process into a temp home and check coverage."""
    _seed_learning(home)
    (home / "tenants" / "_default" / "sessions").mkdir(parents=True, exist_ok=True)
    # Bridge-side stores the L28/L39/L41/L42 handlers cover:
    tg = home / "tenants" / "_default" / "global"
    for d in ("memory", "web_chat/sessions", "social", "grants", "orgs", "ulo", "forge", "erasure"):
        (tg / d).mkdir(parents=True, exist_ok=True)
    (home / "tenants" / "_default" / "workflow_runs").mkdir(parents=True, exist_ok=True)
    # infinite-session real writers (they create their own dirs)
    from core.infinite_session.rollback_manager import RollbackManager
    RollbackManager("_default", corvin_home=home)

    from erasure_handlers import COVERED_DIRS, NON_PERSONAL_DIRS, real_handler_chain

    claimed = set().union(*COVERED_DIRS.values()) | set(NON_PERSONAL_DIRS)
    chain_ids = {h.layer_id for h in real_handler_chain(tenant_id="_default")}
    for layer in COVERED_DIRS:
        assert layer in chain_ids, f"COVERED_DIRS names a layer not in the real chain: {layer}"

    troot = home / "tenants" / "_default"
    created: set[str] = set()
    for child in troot.iterdir():
        if child.name == "global":
            for g in child.iterdir():
                created.add(f"global/{g.name}")
        else:
            created.add(child.name)
    unclaimed = sorted(c for c in created if c not in claimed)
    assert not unclaimed, (
        f"directories under the tenant home with NO GDPR Art. 17 erasure path: {unclaimed} — "
        "add a handler to erasure_handlers.real_handler_chain and claim the directory in "
        "COVERED_DIRS (or, only if it holds no personal data by construction, NON_PERSONAL_DIRS)"
    )
