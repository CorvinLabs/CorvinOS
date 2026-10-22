"""DAG runner — topological execution with node-type dispatch.

The runner is single-process and synchronous. Same-level nodes execute
sequentially in this MVP; parallelism is a Phase-2 concern (ThreadPoolExecutor
analog to L25-compute's ParallelDriver).
"""
from __future__ import annotations

import logging
import secrets
import time as _time_mod
from dataclasses import dataclass, field
from time import perf_counter
from typing import Any, Callable

from .node_types import NODE_TYPES, WorkflowPaused
from .storage import WorkflowDoc

_log = logging.getLogger("corvin_workflows.runner")


@dataclass
class NodeResult:
    node_id: str
    node_type: str
    started_at: float
    finished_at: float
    output: dict[str, Any]
    error: str | None = None
    status: str = "success"  # "success" | "failed" | "skipped"
    attempts: int = 1

    @property
    def wall_s(self) -> float:
        return self.finished_at - self.started_at


@dataclass
class RunResult:
    workflow: str
    state: str  # "complete" | "failed" | "paused" | "cancelled"
    inputs: dict[str, Any]
    nodes: dict[str, NodeResult] = field(default_factory=dict)
    final_state: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    audit: list[dict[str, Any]] = field(default_factory=list)
    run_id: str | None = None
    paused_at_node: str | None = None
    paused_prompt: str | None = None

    @property
    def total_wall_s(self) -> float:
        if not self.nodes:
            return 0.0
        return sum(n.wall_s for n in self.nodes.values())


@dataclass
class ResumeContext:
    """Carries a loaded checkpoint into `DAGRunner.run(resume=...)` (ADR-0188 M5)."""

    run_id: str
    state: dict[str, Any]
    completed_ids: set[str]
    skipped_ids: set[str]
    paused_at_node: str
    reply: str
    failed_ids: set[str] = field(default_factory=set)
    # The checkpoint being resumed (prompt / channel / approver), so a failed resume can write
    # a fresh one that keeps the progress of that pass (A-05).
    checkpoint: dict[str, Any] | None = None


def _topo_levels(graph: list[dict[str, Any]]) -> list[list[str]]:
    """Kahn's algorithm — returns a list of execution levels."""
    incoming: dict[str, set[str]] = {n["id"]: set(n.get("depends_on") or []) for n in graph}
    levels: list[list[str]] = []
    remaining = dict(incoming)
    while remaining:
        ready = sorted([nid for nid, deps in remaining.items() if not deps])
        if not ready:
            raise RuntimeError("topo_levels: cycle detected (validator should have caught this)")
        levels.append(ready)
        for r in ready:
            del remaining[r]
        for deps in remaining.values():
            deps.difference_update(ready)
    return levels


def _direct_dependents(graph: list[dict[str, Any]]) -> dict[str, list[str]]:
    """node_id -> [ids of nodes whose depends_on includes node_id]."""
    out: dict[str, list[str]] = {n["id"]: [] for n in graph}
    for n in graph:
        for dep in n.get("depends_on") or []:
            out.setdefault(dep, []).append(n["id"])
    return out


def _propagate_skip(
    node_specs: dict[str, dict[str, Any]],
    dependents: dict[str, list[str]],
    origins: set[str],
    dead: set[str],
) -> set[str]:
    """Nodes that must be skipped because `origins` died (skipped by a route branch or
    failed under `fail_branch`). `dead` is every node already skipped or failed and is
    extended in place; the return value is the newly skipped set, origins excluded.

    ADR-0188 M3/M4 + WF-05: a node whose dependency died is skipped, EXCEPT a `merge`
    node, which is the join point of a branch: it is skipped only when ALL of its
    dependencies are dead. Propagating through a join (the previous behaviour) skipped
    everything downstream of every route -> merge diamond, and still reported the run
    as complete."""
    newly: set[str] = set()
    dead |= origins
    stack = list(origins)
    while stack:
        cur = stack.pop()
        for nid in dependents.get(cur, []):
            if nid in dead:
                continue
            spec = node_specs[nid]
            if spec.get("type") == "merge":
                deps = spec.get("depends_on") or []
                if not all(d in dead for d in deps):
                    continue
            newly.add(nid)
            dead.add(nid)
            stack.append(nid)
    return newly


_DEFAULT_ERROR_STRATEGY = "abort"
_ERROR_STRATEGIES = {"abort", "fail_branch"}

# WF-A4: hard upper bounds against resource-exhaustion DoS from a crafted
# workflow. A workflow author can request retries/intervals, but never more than
# these ceilings — a `max_retries: 100000` or `retry_interval_s: 1e9` must not
# turn one node into an unbounded busy/sleep loop.
_MAX_RETRIES_CAP = 10
_MAX_RETRY_INTERVAL_S = 60.0

# WF-A4: node types whose executor performs an externally-visible side effect
# (writes to the bridge outbox). Retrying them on a post-side-effect exception
# would double-deliver, so they are NEVER retried regardless of the workflow's
# declared retry block. `ask_human` is already excluded structurally (it raises
# WorkflowPaused, caught before the retry path).
# WF-20: ask_human writes to the shared outbox, so a retry would send the message twice.
# ADR-2276: peer_agent hands a task to another organisation; a repeat is a second task there.
_NON_RETRYABLE_TYPES = {"deliver", "answer", "ask_human", "approval", "peer_agent"}


def _retry_config(node_spec: dict[str, Any]) -> tuple[int, float, str]:
    """Parse the optional `retry:` block (ADR-0188 M4). Absent -> unchanged
    legacy behavior: 1 attempt, abort the whole run on failure. Requested
    retries/intervals are clamped to hard caps (WF-A4)."""
    cfg = node_spec.get("retry") or {}
    requested_retries = int(cfg.get("max_retries", 0))
    max_retries = max(0, min(requested_retries, _MAX_RETRIES_CAP))
    if requested_retries > _MAX_RETRIES_CAP:
        _log.warning(
            "workflow node %r requested max_retries=%d — clamped to cap %d (WF-A4)",
            node_spec.get("id"), requested_retries, _MAX_RETRIES_CAP,
        )
    requested_interval = float(cfg.get("retry_interval_s", 0))
    interval_s = max(0.0, min(requested_interval, _MAX_RETRY_INTERVAL_S))
    if requested_interval > _MAX_RETRY_INTERVAL_S:
        _log.warning(
            "workflow node %r requested retry_interval_s=%s — clamped to cap %s (WF-A4)",
            node_spec.get("id"), requested_interval, _MAX_RETRY_INTERVAL_S,
        )
    strategy = cfg.get("error_strategy", _DEFAULT_ERROR_STRATEGY)
    if strategy not in _ERROR_STRATEGIES:
        raise ValueError(
            f"retry.error_strategy must be one of {sorted(_ERROR_STRATEGIES)}, got {strategy!r}"
        )
    # Side-effecting nodes are never retried (double-delivery guard).
    if node_spec.get("type", "agent") in _NON_RETRYABLE_TYPES:
        max_retries = 0
    return max_retries, interval_s, strategy


def _is_spawn_refusal(exc: BaseException) -> bool:
    """True for a gate-refused worker spawn (WF-20). Imported lazily: the Claude
    engine module is optional for stub-only use."""
    try:
        from .engines_claude import SpawnRefusedError
    except Exception:  # noqa: BLE001
        return False
    return isinstance(exc, SpawnRefusedError)


def _share_output(node_spec: dict[str, Any], output: dict[str, Any]) -> dict[str, Any]:
    """Apply the YAML `share_output: [field1, field2]` projection.

    If `share_output` is unset, the whole output is published; otherwise only
    the listed fields. Mirrors AWP's DAG semantics.
    """
    share = node_spec.get("share_output")
    if share is None:
        return dict(output)
    if not isinstance(share, list):
        raise ValueError("share_output must be a list of field names")
    return {k: output.get(k) for k in share}


class DAGRunner:
    """Synchronous DAG runner.

    Construction:
        runner = DAGRunner(doc, engine=stub_engine)

    Each run:
        result = runner.run(inputs={...})
    """

    def __init__(
        self,
        doc: WorkflowDoc,
        *,
        engine: Any,
        audit_sink: Callable[[dict[str, Any]], None] | None = None,
        tenant_id: str | None = None,
        observer: "Callable[[NodeResult], None] | None" = None,
        console_run: bool = False,
        cancel_event: Any = None,
        executor_overrides: "dict[str, Callable[..., Any]] | None" = None,
    ) -> None:
        self.doc = doc
        self.engine = engine
        self._audit_sink = audit_sink
        # WF-03: called with every finished NodeResult (success, failed, skipped) so a
        # streaming caller (the console) can show each node's output as it lands. The
        # audit stream deliberately carries no node output.
        self._observer = observer
        # WF-02: the run was started from the console — `ask_human` with no resolvable
        # chat pauses for the console operator instead of failing.
        self._console_run = console_run
        # WF-07: a threading.Event-like (`is_set()`); checked before each node so a
        # stopped run does not keep spawning engines in the background.
        self._cancel_event = cancel_event
        # Per-runner executor replacements keyed by node type. The readiness check
        # (readiness.py) swaps the side-effecting node types for recording stubs this
        # way; the global NODE_TYPES registry is never mutated, so a concurrent real
        # run is unaffected.
        self._executor_overrides = dict(executor_overrides or {})
        # WF-A2: the tenant a run belongs to. Threaded into checkpoint.save so a
        # paused run's checkpoint is stored under ITS tenant, not always _default
        # (which made non-default tenants unable to resume — console resume loads
        # with rec.tenant_id — and colocated paused state cross-tenant in
        # _default). None keeps the legacy _default location for back-compat.
        self._tenant_id = tenant_id
        self._audit_buffer: list[dict[str, Any]] = []

    def _audit(self, event: str, **details: Any) -> None:
        rec = {"event": event, **details}
        self._audit_buffer.append(rec)
        if self._audit_sink is not None:
            self._audit_sink(rec)

    def _observe(self, result: "NodeResult") -> None:
        if self._observer is None:
            return
        try:
            self._observer(result)
        except Exception:  # noqa: BLE001 — a display hook never decides a run's outcome
            _log.warning("workflow observer raised for node %r", result.node_id, exc_info=True)

    def run(
        self,
        inputs: dict[str, Any] | None = None,
        *,
        resume: "ResumeContext | None" = None,
        run_id: str | None = None,
    ) -> RunResult:
        import time as _time

        inputs = dict(inputs or {})
        graph = self.doc.graph
        node_specs = {n["id"]: n for n in graph}
        levels = _topo_levels(graph)
        dependents = _direct_dependents(graph)

        # `run_id` lets a caller (e.g. the orchestration MCP server's
        # wall-clock-budget wrapper) pre-assign the run's id BEFORE calling
        # run() — so a timeout response returned before this method returns
        # can still hand back a real, later-pollable run_id under the SAME
        # value a background completion-notification gets registered under.
        # Falls back to the original random id when the caller doesn't care.
        run = RunResult(
            workflow=self.doc.name,
            state="complete",
            inputs=inputs,
            audit=self._audit_buffer,
            run_id=resume.run_id if resume else (run_id or secrets.token_hex(8)),
        )
        if resume:
            state: dict[str, Any] = dict(resume.state)
            state["__replies__"] = {resume.paused_at_node: resume.reply}
            done: set[str] = set(resume.completed_ids)
            skipped: set[str] = set(resume.skipped_ids)
            failed: set[str] = set(resume.failed_ids)
        else:
            state = {"__console_run__": True} if self._console_run else {}
            done = set()
            skipped = set()
            failed = set()

        self._audit(
            "run.started" if not resume else "run.resumed",
            workflow=self.doc.name,
            run_id=run.run_id,
            # WF-01: a run record names the engine, so a synthetic (stub) run is
            # recognisable afterwards.
            engine=getattr(self.engine, "name", "unknown"),
            input_keys=sorted(inputs.keys()),
            levels=len(levels),
            nodes=len(graph),
        )

        for level_index, level in enumerate(levels):
            self._audit("run.level", level=level_index, nodes=list(level))
            for nid in level:
                if nid in done:
                    continue  # already executed in a prior (pre-pause) pass

                node = node_specs[nid]
                ntype = node.get("type", "agent")

                if self._cancel_event is not None and self._cancel_event.is_set():
                    run.state = "cancelled"
                    run.error = "cancelled"
                    run.final_state = state
                    self._audit("run.terminal", state="cancelled", node_id=nid)
                    return run

                if nid in skipped:
                    t_now = perf_counter()
                    run.nodes[nid] = NodeResult(
                        node_id=nid, node_type=ntype,
                        started_at=t_now, finished_at=t_now,
                        output={}, status="skipped",
                    )
                    self._audit("node.skipped", node_id=nid, node_type=ntype)
                    self._observe(run.nodes[nid])
                    continue

                executor = self._executor_overrides.get(ntype) or NODE_TYPES[ntype]["execute"]
                try:
                    max_retries, interval_s, error_strategy = _retry_config(node)
                except Exception as e:  # noqa: BLE001 - a bad retry block fails this node
                    max_retries, interval_s, error_strategy = 0, 0.0, _DEFAULT_ERROR_STRATEGY
                    node = {**node, "__config_error__": str(e)}

                t0 = perf_counter()
                self._audit("node.started", node_id=nid, node_type=ntype)
                output: dict[str, Any] | None = None
                last_error: Exception | None = None
                attempts = 0
                for attempts in range(1, max_retries + 2):  # first try + N retries
                    try:
                        if "__config_error__" in node:
                            raise ValueError(node["__config_error__"])
                        output = executor(
                            node=node, engine=self.engine, state=state,
                            inputs=inputs, audit=self._audit,
                        )
                        # A-03: whatever the engine handed back must be a mapping; a list /
                        # null / scalar is a node failure, not an AssertionError after the
                        # fact. The projection is computed here too so its errors count.
                        if not isinstance(output, dict):
                            raise TypeError(
                                f"node returned {type(output).__name__}, expected a JSON object"
                            )
                        shared = _share_output(node, output)
                        last_error = None
                        break
                    except WorkflowPaused as p:
                        # Not a failure — never retried. Checkpoint and stop.
                        # Exclude `skipped` from completed_ids: a node that was
                        # skipped (not executed) before the pause must resume
                        # via the `skipped` branch below, not the `done`
                        # short-circuit — overlapping the two sets means a
                        # previously-skipped node silently vanishes from the
                        # resumed run's `nodes` (verified: `if nid in done`
                        # is checked before `if nid in skipped`).
                        done_now = (set(run.nodes.keys()) | done) - skipped
                        self._checkpoint_and_pause(
                            run=run, pause=p, node_specs=node_specs,
                            state=state, inputs=inputs,
                            done_now=done_now,
                            skipped_now=skipped,
                            failed_now=failed,
                        )
                        return run
                    except Exception as e:  # noqa: BLE001 — every node failure is reported
                        last_error = e
                        self._audit(
                            "node.attempt_failed", node_id=nid, node_type=ntype,
                            attempt=attempts, error=f"{type(e).__name__}: {e}",
                        )
                        # WF-20: a spawn the pre-spawn gates refused is final; asking
                        # again only repeats the refusal (and its audit record).
                        if _is_spawn_refusal(e):
                            break
                        if attempts <= max_retries:
                            if interval_s > 0:
                                _time.sleep(interval_s)
                            continue

                t1 = perf_counter()

                if last_error is not None:
                    err_text = f"{type(last_error).__name__}: {last_error}"
                    self._audit(
                        "node.failed", node_id=nid, node_type=ntype,
                        error=err_text, attempts=attempts, error_strategy=error_strategy,
                    )
                    run.nodes[nid] = NodeResult(
                        node_id=nid, node_type=ntype,
                        started_at=t0, finished_at=t1,
                        output={}, error=err_text, status="failed", attempts=attempts,
                    )
                    self._observe(run.nodes[nid])
                    if error_strategy == "fail_branch":
                        # Contain the failure to this branch: skip everything
                        # downstream of it, keep running the rest of the graph.
                        failed.add(nid)
                        newly_skipped = _propagate_skip(
                            node_specs, dependents, {nid}, skipped | failed)
                        skipped |= newly_skipped
                        self._audit(
                            "node.branch_failed", node_id=nid,
                            skipped=sorted(newly_skipped),
                        )
                        continue
                    # Default "abort" strategy — unchanged legacy behavior.
                    run.state = "failed"
                    run.error = f"node {nid!r}: {err_text}"
                    run.final_state = state
                    self._audit("run.terminal", state="failed", node_id=nid)
                    if resume is not None and resume.checkpoint:
                        if _is_spawn_refusal(last_error):
                            # ADR-2271: a run whose resumed node a gate refused is not left
                            # resumable — retrying would repeat the refusal (and its deny
                            # record) with the same reply. The checkpoint goes.
                            from . import checkpoint as _checkpoint  # noqa: PLC0415

                            _checkpoint.delete(run.run_id, tenant_id=self._tenant_id)
                            self._audit("run.resume_refused", node_id=nid)
                        else:
                            # A-05: the nodes this pass completed (answer / deliver sent their
                            # message) must not run again when the operator retries the resume.
                            self._save_failed_resume_progress(
                                run=run, resume=resume, state=state, inputs=inputs,
                                done=(set(run.nodes) | done) - skipped - {nid},
                                skipped=skipped, failed=failed,
                            )
                    return run

                # Success — publish projection into state.
                state[nid] = shared
                run.nodes[nid] = NodeResult(
                    node_id=nid, node_type=ntype,
                    started_at=t0, finished_at=t1,
                    output=output, attempts=attempts,
                )
                self._audit(
                    "node.completed", node_id=nid, node_type=ntype,
                    wall_ms=int((t1 - t0) * 1000), attempts=attempts,
                )
                self._observe(run.nodes[nid])

                # route (ADR-0188 M3): skip every directly-dependent node
                # tagged `branch: <case>` that does not match the chosen case.
                if ntype == "route":
                    chosen = output.get("case")
                    for dep_id in dependents.get(nid, []):
                        dep_branch = node_specs[dep_id].get("branch")
                        if dep_branch is not None and dep_branch != chosen:
                            newly_skipped = {dep_id} | _propagate_skip(
                                node_specs, dependents, {dep_id}, skipped | failed)
                            skipped |= newly_skipped
                            self._audit(
                                "node.branch_skipped", node_id=nid, chosen=chosen,
                                skipped=sorted(newly_skipped),
                            )

        run.final_state = state
        failed_ct = sum(1 for n in run.nodes.values() if n.status == "failed")
        skipped_ct = sum(1 for n in run.nodes.values() if n.status == "skipped")
        self._audit(
            "run.terminal", state="complete", nodes=len(run.nodes),
            failed_branches=failed_ct, skipped=skipped_ct,
        )
        return run

    def _save_failed_resume_progress(
        self, *, run: "RunResult", resume: "ResumeContext", state: dict[str, Any],
        inputs: dict[str, Any], done: set[str], skipped: set[str], failed: set[str],
    ) -> None:
        from . import checkpoint as _checkpoint

        old = resume.checkpoint or {}
        # The pause deadline survives a failed resume: the re-saved checkpoint keeps the
        # remaining time, it does not become open-ended (review 2026-10-21).
        remaining_s: float | None = None
        old_exp = old.get("expires_at_ms")
        if old_exp is not None:
            import time as _t  # noqa: PLC0415

            remaining_s = max(1.0, (int(old_exp) - _t.time() * 1000) / 1000.0)
        _checkpoint.save(
            run.run_id,
            timeout_s=remaining_s,
            workflow_path=self.doc.source_path or old.get("workflow_path", ""),
            workflow_name=self.doc.name,
            inputs=inputs,
            state={k: v for k, v in state.items() if k != "__replies__"},
            completed_ids=sorted(done),
            skipped_ids=sorted(skipped),
            failed_ids=sorted(failed),
            paused_at_node=resume.paused_at_node,
            prompt=old.get("prompt", ""),
            channel=old.get("channel"),
            chat_id=old.get("chat_id"),
            expect=old.get("expect"),
            approver=old.get("approver"),
            tenant_id=self._tenant_id,
        )

    def _checkpoint_and_pause(
        self,
        *,
        run: RunResult,
        pause: WorkflowPaused,
        node_specs: dict[str, dict[str, Any]],
        state: dict[str, Any],
        inputs: dict[str, Any],
        done_now: set[str],
        skipped_now: set[str],
        failed_now: set[str] | None = None,
    ) -> None:
        """Persist the checkpoint and mutate `run` in place to state='paused'.
        Split out of run() so the (long) main loop stays readable."""
        from . import checkpoint as _checkpoint

        # `state` may carry a stale __replies__ from an earlier resume pass
        # (already consumed) — never persist it back into the checkpoint.
        persisted_state = {k: v for k, v in state.items() if k != "__replies__"}

        _checkpoint.save(
            run.run_id,
            workflow_path=self.doc.source_path or "",
            workflow_name=self.doc.name,
            inputs=inputs,
            state=persisted_state,
            completed_ids=sorted(done_now),
            skipped_ids=sorted(skipped_now),
            failed_ids=sorted(failed_now or ()),
            paused_at_node=pause.node_id,
            prompt=pause.prompt,
            channel=pause.channel,
            chat_id=pause.chat_id,
            expect=pause.expect,
            # WF-A3: bind the intended approver. The approval prompt was sent to
            # (channel, chat_id) — only that recipient (or a privileged/owner
            # caller passing replier=None) may answer on resume. Deny-by-default
            # is enforced in resume_workflow().
            approver=pause.chat_id,
            tenant_id=self._tenant_id,
            timeout_s=pause.timeout_s,
        )

        run.state = "paused"
        run.paused_at_node = pause.node_id
        run.paused_prompt = pause.prompt
        run.final_state = state
        self._audit(
            "run.paused", run_id=run.run_id, node_id=pause.node_id,
            channel=pause.channel, chat_id=pause.chat_id,
        )


class PauseExpired(RuntimeError):
    """WF-04: the pause's deadline (`timeout_minutes` / `timeout_s`) has passed. The
    checkpoint is kept for inspection but can no longer be resumed."""


class WorkflowChanged(RuntimeError):
    """WF-06: the workflow file was edited after the run paused. Resuming would replay the
    saved node results against a different graph (missing nodes, other branches)."""


class UnauthorizedReplier(PermissionError):
    """WF-A3: raised when a resume reply comes from an identity other than the
    approver the pause was directed to. Deny-by-default."""


def resume_workflow(
    run_id: str,
    reply: str,
    *,
    engine: Any,
    tenant_id: str | None = None,
    replier: str | None = None,
    audit_sink: Callable[[dict[str, Any]], None] | None = None,
    observer: "Callable[[NodeResult], None] | None" = None,
    cancel_event: Any = None,
) -> RunResult:
    """Load a paused run's checkpoint, reload its workflow, and continue
    execution with `reply` injected at the paused `ask_human` node
    (ADR-0188 M5). Deletes the checkpoint once the resumed run reaches a
    terminal state (complete/failed); leaves it in place if it pauses again
    at a second ask_human node.

    `replier` (WF-A3) is the identity answering the approval. When the caller is
    a specific chat participant (bridge path), it must equal the checkpoint's
    recorded `approver` — otherwise `UnauthorizedReplier` is raised. A privileged
    owner caller (console) passes `replier=None`, which is always authorized.

    CON-F2: the checkpoint is atomically CLAIMED for the duration of the resume,
    so a second concurrent resume of the same run fails fast instead of
    double-executing side-effecting nodes.
    """
    from . import checkpoint as _checkpoint
    from .storage import load_workflow

    ckpt = _checkpoint.load(run_id, tenant_id=tenant_id)
    if ckpt is None:
        raise KeyError(f"no paused run found for run_id={run_id!r}")
    if not ckpt.get("workflow_path"):
        raise RuntimeError(f"checkpoint {run_id!r} has no workflow_path — cannot resume")

    # WF-A3: enforce the approver binding BEFORE claiming/executing anything.
    approver = ckpt.get("approver")
    if replier is not None and approver is not None and str(replier) != str(approver):
        raise UnauthorizedReplier(
            f"reply from {replier!r} rejected: approval for run {run_id!r} was "
            f"directed to a different recipient"
        )

    saved_sha = ckpt.get("workflow_sha256")
    if saved_sha and _checkpoint.file_sha256(ckpt["workflow_path"]) != saved_sha:
        raise WorkflowChanged(
            f"run {run_id!r} paused from a workflow file that has since changed; "
            "restart the run instead of resuming it"
        )

    expires_at_ms = ckpt.get("expires_at_ms")
    if expires_at_ms is not None and int(expires_at_ms) < int(_time_mod.time() * 1000):
        raise PauseExpired(f"run {run_id!r} was waiting for a reply until its deadline, which has passed")

    # CON-F2: atomically claim so concurrent resumes can't double-execute.
    _checkpoint.claim(run_id, tenant_id=tenant_id)

    result: RunResult
    try:
        doc = load_workflow(ckpt["workflow_path"])
        runner = DAGRunner(doc, engine=engine, audit_sink=audit_sink, tenant_id=tenant_id,
                           observer=observer, cancel_event=cancel_event)
        result = runner.run(
            inputs=ckpt["inputs"],
            resume=ResumeContext(
                run_id=run_id,
                state=ckpt["state"],
                completed_ids=set(ckpt["completed_ids"]),
                skipped_ids=set(ckpt.get("skipped_ids") or []),
                failed_ids=set(ckpt.get("failed_ids") or []),
                checkpoint=ckpt,
                paused_at_node=ckpt["paused_at_node"],
                reply=reply,
            ),
        )
    except BaseException:
        # A resume that never reached a terminal state must release its claim so
        # the run stays re-resumable (transient engine/I-O errors, cancellation).
        _checkpoint.release(run_id, tenant_id=tenant_id)
        raise

    # Delete the checkpoint ONLY on clean completion. A "failed"/paused resume
    # (a transient engine timeout, a second ask_human) after the human already
    # replied must NOT erase all pre-pause work and the reply — that would make
    # the run unrecoverable. Release the claim so it can be re-resumed; a
    # genuinely-abandoned run is reaped by TTL/GC, not here.
    if result.state == "complete":
        _checkpoint.delete(run_id, tenant_id=tenant_id)
    else:
        # runner may have re-saved a fresh checkpoint (paused again) at the
        # canonical path; only release the claim sidecar if no fresh one exists.
        _checkpoint.release(run_id, tenant_id=tenant_id)
    return result
