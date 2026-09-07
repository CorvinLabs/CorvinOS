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


def _boot_real_writers(home: Path) -> None:
    """Drive the REAL writers so the directories they create are the ones the
    guard inspects (R2-A7).

    The previous version hand-seeded ``mkdir``s that mirrored what the writers
    were believed to do — so it could only ever confirm the test author's own
    picture, and it missed four live stores (workflows transcripts, browser
    profiles, vibe checkpoints, datasource manifests) for exactly that reason.
    Each writer below is the production code path, imported and called.
    """
    import sys as _sys
    _repo = Path(__file__).resolve().parents[2]
    for _p in (_repo, _repo / "core" / "console", _repo / "operator" / "forge",
               _repo / "operator" / "bridges" / "shared"):
        if str(_p) not in _sys.path:
            _sys.path.append(str(_p))

    # 1. Workflow authoring transcript + metadata — the real route helpers.
    from corvin_console.routes import workflows as _wf
    _wf._append_chat_line("_default", "wf-guard", {
        "role": "user", "content": "hello", "ts": 1.0, "speaker": SUBJECT,
    })
    _wf._write_atomic(_wf._meta_path("_default", "wf-guard"),
                      {"id": "wf-guard", "title": "t", "created_by": SUBJECT})

    # 2. Vibe checkpoints — the real CheckpointManager.
    from core.vibe_engineering.checkpoint_manager import CheckpointManager
    _cm = CheckpointManager(tenant_id="_default")
    # create_checkpoint() builds the state; save() is what puts it on disk.
    _cm.save(_cm.create_checkpoint(
        task_id="task-guard", session_id=SUBJECT, phase="build", trigger="manual",
        iteration_num=1, task_state={}, context_essentials={}, learning_state={},
        open_subgoals=[], artifacts=[],
    ))

    # 3. Infinite-session rollback WAL — the real RollbackManager.
    from core.infinite_session.rollback_manager import RollbackManager
    RollbackManager("_default", corvin_home=home)

    # 4. Browser session profile. The directory is created by
    #    ``BrowserSession._launch`` right before it starts Chromium
    #    (``self._home / "sessions" / self.session_id``); launching a real
    #    browser is out of scope for this guard, so the REAL path resolver is
    #    used and the profile dir created exactly as that line does.
    from corvin_console.routes.browser import _home as _browser_home
    (_browser_home("_default") / "sessions" / SUBJECT).mkdir(parents=True, exist_ok=True)

    # 5. Datasource connection manifest — the real registry's path resolver.
    from core.compute.corvin_compute.fabric.datasources.registry import (
        DataSourceRegistry,
    )
    conn = (DataSourceRegistry(corvin_home=home)._home / "tenants" / "_default"
            / "datasource_connections")
    conn.mkdir(parents=True, exist_ok=True)
    (conn / "ds1.json").write_text(json.dumps({"name": "ds1", "owner": SUBJECT}))


def test_every_written_directory_is_claimed_by_a_handler(home):
    """Boot the writers reachable in-process into a temp home and check coverage."""
    _seed_learning(home)
    (home / "tenants" / "_default" / "sessions").mkdir(parents=True, exist_ok=True)
    # Bridge-side stores the L28/L39/L41/L42 handlers cover:
    tg = home / "tenants" / "_default" / "global"
    for d in ("memory", "web_chat/sessions", "social", "grants", "orgs", "ulo", "forge", "erasure"):
        (tg / d).mkdir(parents=True, exist_ok=True)
    (home / "tenants" / "_default" / "workflow_runs").mkdir(parents=True, exist_ok=True)
    _boot_real_writers(home)

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


class TestTheNewHandlersActuallyErase:
    """R2-A7: a claimed directory must be claimed by a handler that WORKS.

    Claiming a directory in COVERED_DIRS satisfies the guard above; it does not
    erase anything. Each handler is driven here against data the real writers
    produced, and each must leave another subject's data untouched.
    """

    def test_workflow_transcript_is_erased(self, home):
        _boot_real_writers(home)
        from erasure_handlers import WorkflowChatHandler

        wf = home / "tenants" / "_default" / "workflows"
        (wf / "other.chat.jsonl").write_text(
            json.dumps({"role": "user", "content": "keep", "speaker": OTHER}) + "\n")
        r = WorkflowChatHandler(tenant_id="_default").purge(SUBJECT, "er-wf")
        assert r.status.value == "applied", r
        assert not (wf / "wf-guard.chat.jsonl").exists()
        assert OTHER in (wf / "other.chat.jsonl").read_text()

    def test_browser_profile_is_erased(self, home):
        _boot_real_writers(home)
        from erasure_handlers import BrowserSessionHandler

        sessions = home / "tenants" / "_default" / "browser" / "sessions"
        (sessions / OTHER).mkdir(parents=True, exist_ok=True)
        r = BrowserSessionHandler(tenant_id="_default").purge(SUBJECT, "er-br")
        assert r.status.value == "applied", r
        assert not (sessions / SUBJECT).exists()
        assert (sessions / OTHER).exists()

    def test_vibe_checkpoint_is_erased(self, home):
        _boot_real_writers(home)
        from erasure_handlers import VibeCheckpointHandler

        r = VibeCheckpointHandler(tenant_id="_default").purge(SUBJECT, "er-vibe")
        assert r.status.value == "applied", r
        left = list((home / "tenants" / "_default" / "vibe").rglob("*.json"))
        assert all(SUBJECT not in p.read_text() for p in left)

    def test_datasource_manifest_is_erased(self, home):
        _boot_real_writers(home)
        from erasure_handlers import DatasourceConnectionHandler

        conn = home / "tenants" / "_default" / "datasource_connections"
        (conn / "ds2.json").write_text(json.dumps({"name": "ds2", "owner": OTHER}))
        r = DatasourceConnectionHandler(tenant_id="_default").purge(SUBJECT, "er-ds")
        assert r.status.value == "applied", r
        assert not (conn / "ds1.json").exists()
        assert (conn / "ds2.json").exists()

    def test_a_subject_named_only_under_speaker_is_found(self, home):
        """The exact attribution gap: the subject sat under ``speaker`` and
        every handler walked past it."""
        from erasure_handlers import _mentions_subject

        assert _mentions_subject({"speaker": SUBJECT, "content": "hi"}, SUBJECT)
        assert not _mentions_subject({"speaker": OTHER, "content": "hi"}, SUBJECT)


def _seam_records(home: Path) -> list[dict]:
    """Every ``erasure.chain_seam`` record on any chain under *home*.

    Path-agnostic on purpose: which chain a handler's audit_event lands on
    (tenant-scoped vs the resolver default) is not what this test is about."""
    out: list[dict] = []
    for chain in home.rglob("audit.jsonl"):
        for line in chain.read_text().splitlines():
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("event_type") == "erasure.chain_seam":
                out.append(rec)
    return out


class TestInfiniteSessionIndexAndSeam:
    def test_index_entries_are_dropped_and_a_seam_is_recorded(self, home):
        """R2-A7: erasing a snapshot left ``index.json`` naming a file that no
        longer exists (the filter read ``path``; the metadata key is
        ``file_path``), and the resulting ``seq`` gap was indistinguishable from
        tampering. Now the index is rewritten and the gap recorded as a seam."""
        isr = home / "tenants" / "_default" / "infinite_session" / "snapshots" / "task-x"
        isr.mkdir(parents=True)
        snap = isr / "s1.json"
        snap.write_text(json.dumps({"session_id": SUBJECT, "state": {}}))
        keep = isr / "s2.json"
        keep.write_text(json.dumps({"session_id": OTHER, "state": {}}))
        (isr / "index.json").write_text(json.dumps([
            {"snapshot_id": "s1", "seq": 1, "file_path": str(snap), "session_id": SUBJECT},
            {"snapshot_id": "s2", "seq": 2, "file_path": str(keep), "session_id": OTHER},
        ]))

        from erasure_handlers import InfiniteSessionHandler

        r = InfiniteSessionHandler(tenant_id="_default").purge(SUBJECT, "er-is")
        assert r.status.value == "applied", r
        index = json.loads((isr / "index.json").read_text())
        ids = [m["snapshot_id"] for m in index]
        assert ids == ["s2"], index
        assert not snap.exists()
        assert keep.exists()

        assert _seam_records(home), "no erasure.chain_seam record on any chain"

    def test_the_seam_record_carries_no_subject_id(self, home):
        """The seam explains a lawful gap; writing the erased identifier into an
        append-only chain would undo the erasure it documents."""
        isr = home / "tenants" / "_default" / "infinite_session" / "snapshots" / "task-y"
        isr.mkdir(parents=True)
        snap = isr / "s1.json"
        snap.write_text(json.dumps({"session_id": SUBJECT}))
        (isr / "index.json").write_text(json.dumps([
            {"snapshot_id": "s1", "seq": 1, "file_path": str(snap), "session_id": SUBJECT},
        ]))
        from erasure_handlers import InfiniteSessionHandler

        InfiniteSessionHandler(tenant_id="_default").purge(SUBJECT, "er-is2")
        seams = _seam_records(home)
        assert seams, "no erasure.chain_seam record on any chain"
        for rec in seams:
            assert SUBJECT not in json.dumps(rec), rec
