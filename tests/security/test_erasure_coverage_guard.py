"""F-A9 (2026-09-07): GDPR Art. 17 coverage guard + the new handlers + the CCC wiring.

1. ``LearningEventHandler`` / ``InfiniteSessionHandler`` are in the real chain
   and actually erase the subject's data from a temp CORVIN_HOME.
2. The CCC ``/erase`` route (``chat_router.dispatch``) drives the REAL
   ``ErasureOrchestrator`` (registry dispatch is the real boundary for the
   router: ``chat_runtime`` calls ``dispatch()`` with the session's tenant).
3. Coverage guard (R4-F1): the set of tenant-home entries that must be covered
   is DERIVED — from an AST scan of the repo source for every
   ``tenant_home()/…`` / ``tenant_global_dir()/…`` / ``corvin_home()/"tenants"/…``
   path literal, plus the directories the reachable writers create in a temp
   home. The round-2 version enumerated only the five writers its author chose
   to import, which is why it was green while ``cel_anchors/`` and
   ``global/acs/`` had no Art. 17 path at all; a hand-written import list can no
   longer satisfy it.
4. Erase proof (R4-F4): every entry in ``COVERED_DIRS`` must DEMONSTRABLY erase
   a planted subject record when the real chain runs. ``skill-forge``, ``skills``
   and ``global/data`` were claimed by handlers that returned
   ``SKIPPED / not_applicable`` without a filesystem call; the round-2 guard
   asserted only that a handler with that ``layer_id`` was registered, so a
   permanent stub was indistinguishable from a working purge.
"""
from __future__ import annotations

import ast
import asyncio
import functools
import itertools
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



# ── R4-F1: the universe of tenant-home entries, DERIVED not enumerated ───────
#
# The round-2 guard's universe was "whatever the writers I imported happened to
# create". That is the failure mode its own comment said it had removed: it can
# only ever confirm the test author's picture, and it missed the CEL anchor
# store and the tenant-global ACS index — both live, both holding the operator's
# own conversation data, both surviving a "completed" erasure.
#
# The universe is now derived mechanically from the repo SOURCE: every path
# expression whose base is one of the canonical home resolvers and whose next
# component is a string literal. Adding a store means writing such an expression,
# so the store shows up here whether or not anyone remembers to import it.

_HOME_CALLS = frozenset({"corvin_home", "_corvin_home", "get_corvin_home"})
_TENANT_CALLS = frozenset({"tenant_home", "_tenant_home", "tenant_dir"})
_GLOBAL_CALLS = frozenset({"tenant_global_dir", "_tenant_global", "global_dir"})
_SCAN_ROOTS = ("core", "operator", "scripts")


def _flatten_div(node: ast.BinOp):
    parts = []
    cur = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Div):
        parts.append(cur.right)
        cur = cur.left
    parts.reverse()
    return cur, parts


def _call_name(node) -> str | None:
    if isinstance(node, ast.Call):
        f = node.func
        if isinstance(f, ast.Name):
            return f.id
        if isinstance(f, ast.Attribute):
            return f.attr
    return None


def _str_const(node) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _first_segment(literal: str, *, is_global: bool) -> str | None:
    seg = [x for x in literal.split("/") if x]
    if not seg:
        return None
    return ("global/" + seg[0]) if is_global else seg[0]


def _scan_module(path: Path) -> set[str]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError, UnicodeDecodeError):
        return set()
    found: set[str] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)):
            continue
        base, parts = _flatten_div(node)
        if _call_name(base) == "Path" and getattr(base, "args", None):
            base = base.args[0]
        name = _call_name(base)
        if name is None:
            continue
        lits = [_str_const(x) for x in parts]
        entry: str | None = None
        if name in _TENANT_CALLS and lits and lits[0]:
            if lits[0] == "global":
                entry = _first_segment(lits[1], is_global=True) if len(lits) > 1 and lits[1] else None
            else:
                entry = _first_segment(lits[0], is_global=False)
        elif name in _GLOBAL_CALLS and lits and lits[0]:
            entry = _first_segment(lits[0], is_global=True)
        elif (name in _HOME_CALLS and len(lits) >= 3
              and lits[0] == "tenants" and lits[1] is None and lits[2]):
            if lits[2] == "global":
                entry = _first_segment(lits[3], is_global=True) if len(lits) > 3 and lits[3] else None
            else:
                entry = _first_segment(lits[2], is_global=False)
        if entry:
            found.add(entry)
    return found


def _is_test_module(path: Path) -> bool:
    s = str(path)
    return ("/tests/" in s or "/test/" in s
            or path.name.startswith("test_") or path.name.endswith("_test.py"))


@functools.lru_cache(maxsize=1)
def source_derived_entries() -> frozenset[str]:
    """Every tenant-home entry the PRODUCTION source builds a path to."""
    out: set[str] = set()
    for f in itertools.chain(*((_REPO / d).rglob("*.py") for d in _SCAN_ROOTS)):
        if _is_test_module(f):
            continue
        out |= _scan_module(f)
    return frozenset(out)


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

    # 6. CEL load-bearing anchor store — the REAL writer (R4-F1). This is the
    #    store that held the operator's verbatim conversation text through a
    #    "completed" erasure; it was invisible to the round-2 guard because
    #    nothing imported it.
    if str(_repo / "operator") not in _sys.path:
        _sys.path.append(str(_repo / "operator"))
    from context_engineering import anchor as _anchor  # type: ignore
    _anchor.add_fact("_default", SUBJECT, "constraint", "synthetic guard fact")

    # 7. Tenant-global ACS run index — the REAL path resolver + manifest shape
    #    the live install has (``run_dir`` points into the subject's session).
    from acs_engine_adapter import _acs_runs_dir  # type: ignore
    _run = _acs_runs_dir("_default") / "acs-guard-1"
    _run.mkdir(parents=True, exist_ok=True)
    (_run / "manifest.json").write_text(json.dumps({
        "run_id": "acs-guard-1", "status": "success",
        "run_dir": str(home / "tenants" / "_default" / "sessions" / SUBJECT
                       / "acs" / "runs" / "acs-guard-1"),
    }))


def _classified() -> dict[str, set[str]]:
    from erasure_handlers import (
        COVERED_DIRS, NON_PERSONAL_DIRS, UNATTRIBUTABLE_DIRS,
    )
    return {
        "covered": set().union(*COVERED_DIRS.values()),
        "non_personal": set(NON_PERSONAL_DIRS),
        "unattributable": set(UNATTRIBUTABLE_DIRS),
    }


def test_every_source_derived_store_is_classified():
    """The universe comes from the SOURCE, not from a list of imports (R4-F1).

    A new persistent store is a new ``tenant_home()/"<name>"`` expression, and
    that expression alone makes the store appear here. Its author must then say
    which it is: covered by a handler, content-free, or holding personal data
    that cannot be attributed to a subject — the third answer is honest, the
    silence the round-2 guard permitted was not.
    """
    cls = _classified()
    known = cls["covered"] | cls["non_personal"] | cls["unattributable"]
    unclassified = sorted(source_derived_entries() - known)
    assert not unclassified, (
        "tenant-home stores written by the source with no GDPR Art. 17 "
        f"classification: {unclassified} — add a handler and claim the entry in "
        "erasure_handlers.COVERED_DIRS, or (only if it holds no personal data by "
        "construction) NON_PERSONAL_DIRS, or (only if it holds personal data that "
        "carries no per-subject attribution at all) UNATTRIBUTABLE_DIRS, which "
        "makes the orchestrator report it as skipped/not_erasable instead of "
        "counting it silently as completed"
    )


def test_the_three_registries_are_disjoint():
    """An entry cannot be both covered and exempt — that ambiguity is how a
    stub claim hides."""
    cls = _classified()
    for a, b in itertools.combinations(sorted(cls), 2):
        overlap = sorted(cls[a] & cls[b])
        assert not overlap, f"{a} and {b} both claim {overlap}"


def test_every_covered_layer_is_in_the_real_chain():
    from erasure_handlers import COVERED_DIRS, real_handler_chain
    chain_ids = {h.layer_id for h in real_handler_chain(tenant_id="_default")}
    missing = sorted(l for l in COVERED_DIRS if l not in chain_ids)
    assert not missing, f"COVERED_DIRS names layers not in the real chain: {missing}"


def test_every_written_directory_is_claimed_by_a_handler(home):
    """Boot the writers reachable in-process into a temp home and check coverage.

    Kept alongside the source scan: a writer that builds its path through a
    helper the scan cannot resolve statically still creates a real directory
    here.
    """
    _seed_learning(home)
    (home / "tenants" / "_default" / "sessions").mkdir(parents=True, exist_ok=True)
    # Bridge-side stores the L28/L39/L41/L42 handlers cover:
    tg = home / "tenants" / "_default" / "global"
    for d in ("memory", "web_chat/sessions", "social", "grants", "orgs", "ulo", "forge", "erasure"):
        (tg / d).mkdir(parents=True, exist_ok=True)
    (home / "tenants" / "_default" / "workflow_runs").mkdir(parents=True, exist_ok=True)
    _boot_real_writers(home)

    cls = _classified()
    claimed = cls["covered"] | cls["non_personal"] | cls["unattributable"]

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
        "COVERED_DIRS (or, only if it holds no personal data by construction, NON_PERSONAL_DIRS, "
        "or UNATTRIBUTABLE_DIRS if it holds personal data with no per-subject attribution)"
    )


def test_the_real_writers_create_the_two_stores_r4_found(home):
    """Regression on the specific blind spot: both live stores are now driven by
    the guard's own writer boot, so removing their claim fails the test above."""
    _boot_real_writers(home)
    troot = home / "tenants" / "_default"
    assert list((troot / "cel_anchors").glob("*.jsonl")), "CEL anchor writer did not write"
    assert (troot / "global" / "acs" / "runs" / "acs-guard-1" / "manifest.json").exists()


# ── R4-F4: a claim is a promise, and the promise is tested ───────────────────


def _plant(entry: str, subject: str, other: str) -> tuple[Path, Path]:
    """Plant a record attributed to ``subject`` (and one to ``other``) in
    ``entry``. Returns ``(subject_path, other_path)``.

    The planted shape is the one the generic attribution rule documents: a file
    NAMED after the subject whose payload also names them under an identity key.
    Whatever a handler's internal format, it must reach that — a store it cannot
    even see a subject-named file in is not covered.
    """
    from erasure_handlers import _entry_path

    path = _entry_path("_default", entry)
    if entry.endswith(".jsonl"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"user_id": subject, "n": 1}) + "\n"
                        + json.dumps({"user_id": other, "n": 2}) + "\n")
        return path, path
    if entry.endswith(".json"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({subject: {"user_id": subject},
                                    other: {"user_id": other}}))
        return path, path
    path.mkdir(parents=True, exist_ok=True)
    sp = path / f"{subject}.json"
    sp.write_text(json.dumps({"user_id": subject, "note": "synthetic guard record"}))
    op = path / "keep-other.json"
    op.write_text(json.dumps({"user_id": other, "note": "synthetic guard record"}))
    return sp, op


def _gone(path: Path, subject: str) -> bool:
    if not path.exists():
        return True
    try:
        return subject not in path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return False


def _covered_entries() -> list[str]:
    from erasure_handlers import COVERED_DIRS
    return sorted(set().union(*COVERED_DIRS.values()))


class TestEveryCoveredEntryActuallyErases:
    """R4-F4: ``COVERED_DIRS`` asserted only that a handler with that layer_id was
    registered. ``L7SkillForgeHandler`` and ``L24DataSnapshotHandler`` returned
    ``SKIPPED / not_applicable`` without a filesystem call, so ``skill-forge``,
    ``skills`` and ``global/data`` were "covered" by a permanent no-op while
    subject-named files survived a ``completed`` erasure. Every claim is now
    exercised against the real chain."""

    @pytest.mark.parametrize("entry", _covered_entries())
    def test_planted_subject_record_is_erased(self, home, entry):
        from erasure_handlers import real_handler_chain

        subject_path, other_path = _plant(entry, SUBJECT, OTHER)
        for handler in real_handler_chain(tenant_id="_default"):
            handler.purge(SUBJECT, "er-cover")
        assert _gone(subject_path, SUBJECT), (
            f"COVERED_DIRS claims {entry!r} but the real chain left the subject's "
            f"record at {subject_path} — either implement the purge or drop the claim"
        )
        assert other_path.exists() and OTHER in other_path.read_text(), (
            f"erasing one subject destroyed another's data in {entry!r} — "
            "over-matching is its own GDPR Art. 5 breach"
        )


def test_unattributable_store_is_reported_not_silently_completed(home):
    """A store that cannot be erased per subject must SAY so (R4-F1).

    ``global/acs_tmp`` holds the assembled system prompt in mkstemp-named plain
    text: no identity field, no subject-derived name. The orchestrator must
    return a controlled ``not_erasable`` code for it rather than folding it into
    a silent ``completed``."""
    from erasure_handlers import UNATTRIBUTABLE_DIRS, UnattributableStoreHandler, _entry_path

    assert UNATTRIBUTABLE_DIRS, "the registry exists so that gaps are stated, not hidden"
    entry = sorted(UNATTRIBUTABLE_DIRS)[0]
    path = _entry_path("_default", entry)
    path.mkdir(parents=True, exist_ok=True)
    (path / ".corvin-sysprompt-guard.txt").write_text("synthetic prompt body")

    r = UnattributableStoreHandler(tenant_id="_default").purge(SUBJECT, "er-un")
    assert r.status.value == "skipped"
    assert r.code == "not_erasable", r
    assert entry in r.reason


def test_unattributable_layer_reaches_the_real_erase_route(home):
    """Through the REAL /erase boundary: the not_erasable code lands in the
    per-layer payload and on the audit chain, so an operator reading the trail
    sees the store that was left standing."""
    from corvin_console import chat_router
    from entity_extract import EntityPlan  # type: ignore
    from erasure_handlers import UNATTRIBUTABLE_DIRS, _entry_path

    entry = sorted(UNATTRIBUTABLE_DIRS)[0]
    path = _entry_path("_default", entry)
    path.mkdir(parents=True, exist_ok=True)
    (path / ".corvin-sysprompt-guard.txt").write_text("synthetic prompt body")

    plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": SUBJECT}, confidence=1.0)
    result = asyncio.run(chat_router.dispatch(plan, "_default"))
    assert result.status == "created", result
    layers = {l["layer_id"]: l for l in result.payload["layers"]}
    lr = layers["L-unattributable-stores"]
    assert lr["status"] == "skipped" and lr["code"] == "not_erasable", lr


def test_every_covered_entry_survives_nothing_through_the_real_erase_route(home):
    """One request through the REAL boundary, every covered entry planted."""
    from corvin_console import chat_router
    from entity_extract import EntityPlan  # type: ignore

    planted = {e: _plant(e, SUBJECT, OTHER) for e in _covered_entries()}
    plan = EntityPlan(entity_type="erasure_request", slots={"subject_id": SUBJECT}, confidence=1.0)
    result = asyncio.run(chat_router.dispatch(plan, "_default"))
    assert result.status == "created", result
    assert result.payload["status"] in ("completed", "partial"), result.payload

    survivors = sorted(e for e, (sp, _op) in planted.items() if not _gone(sp, SUBJECT))
    assert not survivors, (
        f"the /erase route reported {result.payload['status']} while the "
        f"subject's data survived in {survivors}"
    )
    lost = sorted(e for e, (_sp, op) in planted.items()
                  if not (op.exists() and OTHER in op.read_text()))
    assert not lost, f"another subject's data was destroyed in {lost}"


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


# ── Live LLM E2E (R4-F1) ─────────────────────────────────────────────────────
#
# The CEL anchor store is written by an LLM-driven path: the outbound hook
# ``context_engineering.pipeline.maybe_capture_decision_point`` persists the
# assistant's VERBATIM reply block into ``cel_anchors/<safe_key>.jsonl`` on the
# way out of every turn. That is the store R4-F1 found surviving a "completed"
# erasure with the operator's own conversation text in it, so the erasure proof
# is only complete when the text came from a real model turn.


@pytest.mark.live
@pytest.mark.skipif(os.environ.get("CLAUDE_LIVE_E2E") != "1",
                    reason="live LLM E2E: set CLAUDE_LIVE_E2E=1")
def test_live_llm_reply_captured_by_cel_anchor_is_erased(home):
    """Real ``claude -p`` turn → real outbound capture hook → real /erase route."""
    import subprocess
    from types import SimpleNamespace

    sys.path.append(str(_REPO / "operator"))
    from corvin_console import chat_router
    from context_engineering import pipeline
    from entity_extract import EntityPlan  # type: ignore

    # The capture hook is gated on the real ``cel_load_bearing_anchor`` flag —
    # set it through the real console overlay, not by patching the gate.
    overlay = home / "tenants" / "_default" / "global" / "features.json"
    overlay.parent.mkdir(parents=True, exist_ok=True)
    overlay.write_text(json.dumps({"flags": {"cel_load_bearing_anchor": True}}))

    proc = subprocess.run(
        ["claude", "-p",
         "Output the following three lines verbatim and nothing else, "
         "no preamble, no commentary:\n"
         "Which release window do you want?\n"
         "Option 1: deploy on Friday\n"
         "Option 2: deploy on Monday",
         "--model", "haiku"],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-800:]
    reply = proc.stdout.strip()
    assert reply, "empty model reply"

    entry = pipeline.maybe_capture_decision_point(
        reply, "_default", SimpleNamespace(sid=SUBJECT))
    assert entry is not None, f"capture hook stored nothing for reply: {reply!r}"

    store = home / "tenants" / "_default" / "cel_anchors"
    written = list(store.glob("*.jsonl"))
    assert written, "the real CEL writer produced no store"
    probe = entry["text"].splitlines()[0][:40]
    assert any(probe in f.read_text() for f in written)

    plan = EntityPlan(entity_type="erasure_request",
                      slots={"subject_id": SUBJECT}, confidence=1.0)
    result = asyncio.run(chat_router.dispatch(plan, "_default"))
    assert result.status == "created", result
    layers = {l["layer_id"]: l for l in result.payload["layers"]}
    assert layers["L-cel-anchors"]["status"] == "applied", layers["L-cel-anchors"]

    survivors = [p for p in home.rglob("*")
                 if p.is_file() and p.suffix in (".json", ".jsonl", ".txt", ".md")
                 and probe in p.read_text(errors="ignore")]
    assert not survivors, f"model-authored text survived the erasure in {survivors}"
