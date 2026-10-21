"""Console execution of DAG-engine workflows (ADR-0188 M7, ADR-2273).

The console's own streaming executor (``workflows._stream_run``) only knows
agent / fan_out / delegation_loop / deliver. A graph that uses ``code``, ``merge``,
``route``, ``answer``, ``ask_human`` or ``approval`` is run here instead, by the
SAME ``corvin_workflows.DAGRunner`` the ``corvin-flow`` CLI, the scheduler and the
MCP server use - one set of node semantics, one checkpoint/resume store.

Shape of a run:

* ``start`` snapshots the workflow YAML next to the run, then runs ``DAGRunner`` in a
  worker thread. Every event (``node_started`` / ``node_completed`` / ``node_failed`` /
  ``node_skipped`` / ``node_awaiting_approval`` / ``node_awaiting_reply`` / ``run_paused`` /
  ``run_completed``) is appended to the run's JSONL log, the single source the run history
  reads too.
* the SSE stream is a TAIL of that log. It stays open while the run is paused, so the
  approval bar / reply panel keep their stream and see the events of the resume, which runs
  in another request (``resume`` below) and appends to the same log.
* the run id IS the checkpoint key (``DAGRunner.run(run_id=rid)``); the checkpoint's
  ``workflow_path`` is the snapshot, so editing the workflow while a run waits cannot change
  what the resume executes.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, AsyncIterator, Callable

_log = logging.getLogger(__name__)

# Node types the console's own executor does not implement (see workflows._stream_run).
DAG_ONLY_TYPES = frozenset({"code", "merge", "route", "answer", "ask_human", "approval"})

_MAX_OUTPUT_CHARS = 50_000
_TERMINAL_EVENTS = frozenset({"run_completed", "error"})
_TAIL_POLL_S = 0.25
_KEEPALIVE_S = 15.0


def needs_dag_engine(graph: list[Any]) -> bool:
    return any(isinstance(n, dict) and str(n.get("type", "agent")) in DAG_ONLY_TYPES for n in graph)


class _NullEngine:
    """Engine for a graph with no engine-requiring node. spawn() is unreachable; if a node
    classification is ever wrong, fail loudly instead of returning an empty result."""

    name = "none"

    def spawn(self, call: Any) -> dict[str, Any]:
        raise RuntimeError(f"internal: engine-free workflow reached engine.spawn (agent={call.agent!r})")


def default_engine(tenant_id: str, graph: list[dict[str, Any]]) -> Any:
    """The worker engine for a console run. The real one is the gated ``claude -p`` engine
    (every spawn passes the four pre-spawn gates, WFB-R1-09); it is only constructed when a
    node needs it, so a deterministic workflow runs on a host without the CLI."""
    from corvin_workflows import node_requires_engine
    from corvin_workflows.engines_claude import ClaudeCliEngine

    if not any(node_requires_engine(n) for n in graph if isinstance(n, dict)):
        return _NullEngine()
    return ClaudeCliEngine(tenant_id=tenant_id)


# Tests replace this; production never does.
ENGINE_FACTORY: Callable[[str, list[dict[str, Any]]], Any] = default_engine

_ACTIVE: dict[tuple[str, str, str], threading.Event] = {}
_ACTIVE_LOCK = threading.Lock()


def cancel_event_for(tenant_id: str, wid: str, rid: str) -> threading.Event:
    with _ACTIVE_LOCK:
        return _ACTIVE.setdefault((tenant_id, wid, rid), threading.Event())


def try_begin(tenant_id: str, wid: str, rid: str) -> threading.Event | None:
    """Claim the right to drive this run (a resume). None when something already is: two
    approve clicks, or an approve racing a typed reply, must not both resume it."""
    with _ACTIVE_LOCK:
        key = (tenant_id, wid, rid)
        if key in _ACTIVE:
            return None
        ev = threading.Event()
        _ACTIVE[key] = ev
        return ev


def forget(tenant_id: str, wid: str, rid: str) -> None:
    _forget(tenant_id, wid, rid)


def request_cancel(tenant_id: str, wid: str, rid: str) -> bool:
    with _ACTIVE_LOCK:
        ev = _ACTIVE.get((tenant_id, wid, rid))
    if ev is None:
        return False
    ev.set()
    return True


def _forget(tenant_id: str, wid: str, rid: str) -> None:
    with _ACTIVE_LOCK:
        _ACTIVE.pop((tenant_id, wid, rid), None)


def is_active(tenant_id: str, wid: str, rid: str) -> bool:
    with _ACTIVE_LOCK:
        return (tenant_id, wid, rid) in _ACTIVE


def _render(value: Any) -> str:
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            text = str(value)
    return text[:_MAX_OUTPUT_CHARS]


class RunRecorder:
    """Writes one run's events and keeps its meta file in step. Used by the first pass and by
    every resume, so both leave the same trail."""

    def __init__(
        self,
        *,
        tenant_id: str,
        wid: str,
        rid: str,
        log_path: Path,
        read_meta: Callable[[], dict[str, Any]],
        write_meta: Callable[[dict[str, Any]], None],
        node_types: dict[str, str],
        audit: Callable[[dict[str, Any]], None] | None = None,
        alive: Callable[[], bool] = lambda: True,
    ) -> None:
        # `alive` is False once the run record was deleted: a worker still finishing its
        # current node must not resurrect the log or the meta of a deleted run.
        self._alive = alive
        self.tenant_id = tenant_id
        self.wid = wid
        self.rid = rid
        self.log_path = log_path
        self._read_meta = read_meta
        self._write_meta = write_meta
        self._node_types = node_types
        self._audit = audit
        self._t0: dict[str, float] = {}

    # -- low level -----------------------------------------------------------------
    def emit(self, event: dict[str, Any]) -> None:
        if not self._alive():
            return
        line = json.dumps({**event, "ts": event.get("ts", time.time())}, ensure_ascii=False)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")

    def update_meta(self, **fields: Any) -> None:
        if not self._alive():
            return
        meta = self._read_meta()
        meta.update(fields)
        meta["heartbeat_at"] = time.time()
        self._write_meta(meta)

    # -- runner hooks --------------------------------------------------------------
    def audit_sink(self, rec: dict[str, Any]) -> None:
        if rec.get("event") == "node.started":
            nid = str(rec.get("node_id"))
            self._t0[nid] = time.time()
            self.emit({"type": "node_started", "node_id": nid, "node_type": rec.get("node_type")})
            self.update_meta()
        if self._audit is not None:
            try:
                self._audit(rec)
            except Exception:  # noqa: BLE001 - shaping an audit record never aborts a run
                _log.warning("workflow audit sink raised", exc_info=True)

    def observe(self, result: Any) -> None:
        nid = result.node_id
        elapsed = round(result.wall_s, 3)
        if result.status == "success":
            out = _render(result.output)
            self.emit({
                "type": "node_completed", "node_id": nid, "node_type": result.node_type,
                "elapsed_s": elapsed, "tokens": 0, "output_preview": out[:200], "output": out,
            })
        elif result.status == "failed":
            self.emit({
                "type": "node_failed", "node_id": nid, "node_type": result.node_type,
                "error": result.error or "failed", "elapsed_s": elapsed,
            })
        else:
            self.emit({"type": "node_skipped", "node_id": nid, "node_type": result.node_type})

    # -- end of a pass -------------------------------------------------------------
    def finish(self, result: Any, *, checkpoint: dict[str, Any] | None) -> None:
        if result.state == "paused":
            node_id = result.paused_at_node or ""
            kind = self._node_types.get(node_id, "ask_human")
            expires = (checkpoint or {}).get("expires_at_ms")
            awaiting = {
                "node_id": node_id, "kind": kind, "message": result.paused_prompt or "",
                "expires_at_ms": expires,
            }
            self.emit({
                "type": "node_awaiting_approval" if kind == "approval" else "node_awaiting_reply",
                "node_id": node_id, "message": result.paused_prompt or "",
                "timeout_s": (max(0.0, (expires / 1000.0) - time.time()) if expires else None),
            })
            self.emit({"type": "run_paused", "node_id": node_id})
            self.update_meta(status="paused", awaiting=awaiting, ok=None, error=None)
            return
        ok = result.state == "complete"
        self.emit({"type": "run_completed", "ok": ok, "dry_run": False, "budget": {},
                   **({} if ok else {"error": result.error or result.state})})
        self.update_meta(
            status="complete" if ok else "failed", ok=ok, error=None if ok else (result.error or result.state),
            finished_at=time.time(), awaiting=None,
        )

    def fail(self, message: str) -> None:
        self.emit({"type": "error", "message": message})
        self.update_meta(status="failed", ok=False, error=message, finished_at=time.time(), awaiting=None)

    def expire_if_due(self) -> bool:
        """A paused run past its deadline can no longer be resumed: end it as failed."""
        meta = self._read_meta()
        awaiting = meta.get("awaiting") or {}
        exp = awaiting.get("expires_at_ms")
        if meta.get("status") != "paused" or not exp or exp > time.time() * 1000:
            return False
        self.emit({"type": "node_failed", "node_id": awaiting.get("node_id"),
                   "error": "No reply before the deadline - the pause expired"})
        self.emit({"type": "run_completed", "ok": False, "error": "pause expired"})
        self.update_meta(status="failed", ok=False, error="pause expired", finished_at=time.time(),
                         awaiting=None)
        return True


def _checkpoint_of(rid: str, tenant_id: str) -> dict[str, Any] | None:
    from corvin_workflows import checkpoint as _ckpt

    try:
        return _ckpt.load(rid, tenant_id=tenant_id)
    except Exception:  # noqa: BLE001
        return None


def run_first_pass(
    *, snapshot_path: Path, inputs: dict[str, Any], recorder: RunRecorder, cancel_event: threading.Event,
    graph: list[dict[str, Any]],
) -> None:
    """Thread body of a fresh run."""
    from corvin_workflows import DAGRunner
    from corvin_workflows.storage import load_workflow

    try:
        engine = ENGINE_FACTORY(recorder.tenant_id, graph)
        doc = load_workflow(snapshot_path)
        runner = DAGRunner(
            doc, engine=engine, audit_sink=recorder.audit_sink, tenant_id=recorder.tenant_id,
            observer=recorder.observe, console_run=True, cancel_event=cancel_event,
        )
        result = runner.run(inputs=inputs, run_id=recorder.rid)
        recorder.finish(result, checkpoint=_checkpoint_of(recorder.rid, recorder.tenant_id))
    except BaseException as exc:  # noqa: BLE001 - the thread must always leave a terminal state
        _log.warning("workflow run %s/%s failed: %s", recorder.wid, recorder.rid, exc)
        recorder.fail(_public_error(exc))
    finally:
        _forget(recorder.tenant_id, recorder.wid, recorder.rid)


def run_resume(
    *, reply: str, recorder: RunRecorder, cancel_event: threading.Event, graph: list[dict[str, Any]],
) -> Any:
    """Resume a paused run synchronously and record the outcome. Lets the typed errors of
    ``resume_workflow`` (unknown run, already claimed, expired, changed) propagate untouched,
    BEFORE any event is written; everything after the claim is recorded."""
    from corvin_workflows.runner import resume_workflow

    try:
        engine = ENGINE_FACTORY(recorder.tenant_id, graph)
        recorder.update_meta(status="running", awaiting=None)
        try:
            result = resume_workflow(
                recorder.rid, reply, engine=engine, tenant_id=recorder.tenant_id, replier=None,
                audit_sink=recorder.audit_sink, observer=recorder.observe, cancel_event=cancel_event,
            )
        except BaseException:
            # nothing ran (or the claim was released): the run is waiting as before
            _restore_paused(recorder)
            raise
        recorder.finish(result, checkpoint=_checkpoint_of(recorder.rid, recorder.tenant_id))
        return result
    finally:
        _forget(recorder.tenant_id, recorder.wid, recorder.rid)


def _restore_paused(recorder: RunRecorder) -> None:
    ckpt = _checkpoint_of(recorder.rid, recorder.tenant_id)
    if ckpt is None:
        return
    recorder.update_meta(status="paused", awaiting={
        "node_id": ckpt.get("paused_at_node"),
        "kind": recorder._node_types.get(str(ckpt.get("paused_at_node")), "ask_human"),
        "message": ckpt.get("prompt") or "", "expires_at_ms": ckpt.get("expires_at_ms"),
    })


def _public_error(exc: BaseException) -> str:
    """Operator-readable, no internals: the type for engine setup problems, the message only
    for the errors this module raises itself."""
    name = type(exc).__name__
    if name == "ClaudeEngineError":
        return f"Worker engine unavailable: {exc}"
    return f"Run failed ({name})"


def run_resume_background(
    *, reply: str, recorder: RunRecorder, cancel_event: threading.Event, graph: list[dict[str, Any]],
) -> None:
    """Thread body of an approve/reject resume: nobody is waiting on the request, so every
    outcome - including the typed refusals - ends in the run's log."""
    from corvin_workflows import checkpoint as _ckpt
    from corvin_workflows.runner import PauseExpired, WorkflowChanged

    try:
        run_resume(reply=reply, recorder=recorder, cancel_event=cancel_event, graph=graph)
    except _ckpt.AlreadyClaimedError:
        _log.info("resume of %s/%s lost the claim race", recorder.wid, recorder.rid)
    except PauseExpired:
        recorder.expire_if_due() or recorder.fail("The pause expired before it was answered")
    except WorkflowChanged:
        recorder.fail("The workflow was edited while this run was waiting - start a new run")
    except BaseException as exc:  # noqa: BLE001
        _log.warning("background resume %s/%s failed: %s", recorder.wid, recorder.rid, exc)
        recorder.fail(_public_error(exc))


def start_thread(target: Callable[[], None], name: str) -> threading.Thread:
    t = threading.Thread(target=target, name=name, daemon=True)
    t.start()
    return t


async def tail_events(
    recorder: RunRecorder, *, thread: threading.Thread | None = None,
) -> AsyncIterator[str]:
    """SSE text for every event of the run's log until a terminal event. Stays open while the
    run is paused. If the client goes away mid-run the worker is told to stop at the next node;
    a PAUSED run is left waiting (it can be answered from the run history)."""
    offset = 0
    pending = b""
    last_sent = time.time()
    try:
        while True:
            terminal = False
            try:
                with open(recorder.log_path, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read()
            except FileNotFoundError:
                chunk = b""
            if chunk:
                offset += len(chunk)
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for raw in lines:
                    if not raw.strip():
                        continue
                    yield f"data: {raw.decode('utf-8', 'replace')}\n\n"
                    last_sent = time.time()
                    try:
                        if json.loads(raw).get("type") in _TERMINAL_EVENTS:
                            terminal = True
                    except ValueError:
                        pass
            if terminal:
                return
            if recorder.expire_if_due():
                continue
            if thread is not None and not thread.is_alive() and not chunk:
                meta = recorder._read_meta()
                if meta.get("status") in ("complete", "failed"):
                    return
            if time.time() - last_sent > _KEEPALIVE_S:
                yield ": keep-alive\n\n"
                last_sent = time.time()
            await asyncio.sleep(_TAIL_POLL_S)
    finally:
        meta = recorder._read_meta()
        if meta.get("status") == "running":
            request_cancel(recorder.tenant_id, recorder.wid, recorder.rid)


def snapshot_workflow(yaml_text: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".tmp")
    tmp.write_text(yaml_text, encoding="utf-8")
    os.replace(tmp, dest)
    return dest
