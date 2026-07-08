"""Worker pool daemon for M2 task execution (ADR-0081, ADR-0101).

ADR-0101 refactor:
  M1  — path-traversal guard in _workdir_for_chat_key()
  M4  — audit-first + ClaudeCodeEngine._build_args() + L34/L35 gates
  M6  — real abort via asyncio.Process.terminate() + module registry
  M7  — dynamic multi-tenant discovery (no hardcoded '_default')
  M9  — correct shutdown drain via asyncio.wait()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
import time
import unicodedata
from pathlib import Path
from typing import AsyncIterator, Optional

from corvin_console.task_queue import TaskQueue, TaskStatus

logger = logging.getLogger(__name__)

# ── Optional imports (fail gracefully so missing packages don't break the pool) ──

# L16 audit chain (forge security_events)
try:
    from forge.security_events import write_event as _forge_write_event  # type: ignore
    _AUDIT_AVAILABLE = True
except ImportError:
    _AUDIT_AVAILABLE = False
    _forge_write_event = None

# ADR-0171 — universal engine span for the task-daemon spawn (role=worker). The
# shared dir is added to sys.path below (for agents.claude_code); engine_span
# lives there too. Guarded: missing module → legacy task.* events only.
try:
    import engine_span as _espan  # type: ignore
except Exception:  # noqa: BLE001
    try:
        _shared_es = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                  "..", "..", "operator", "bridges", "shared")
        if _shared_es not in sys.path:
            sys.path.insert(0, os.path.abspath(_shared_es))
        import engine_span as _espan  # type: ignore
    except Exception:  # noqa: BLE001
        _espan = None  # type: ignore[assignment]


def _emit_task_engine_span(kind, audit_path, *, task_id, status="ok", duration_ms=0):
    """Best-effort engine.span.start/end for the task-daemon worker (role=worker)."""
    if _espan is None or not _AUDIT_AVAILABLE or _forge_write_event is None:
        return
    try:
        span_id = f"spn-task-{task_id}"
        if kind == "start":
            _forge_write_event(audit_path, _espan.ENGINE_SPAN_START,
                               details=_espan.start_details(
                                   span_id=span_id, role="worker",
                                   engine_id="claude_code", run_id=task_id))
        else:
            _forge_write_event(audit_path, _espan.ENGINE_SPAN_END,
                               details=_espan.end_details(
                                   span_id=span_id, role="worker",
                                   engine_id="claude_code", run_id=task_id,
                                   status=status, duration_ms=int(duration_ms)))
    except Exception:  # noqa: BLE001
        pass


def _notify_task_done(task_id: str, *, ok: bool, summary: str) -> None:
    """Signal the durable completion queue that this task finished.

    No-op unless a producer registered a notification for this task_id (via
    completion_notify.register at task-creation, where the ORIGINATING messenger
    channel/chat_id was captured). This keeps routing PII OUT of the task JSONL
    log (ADR-0101 M3 / GDPR): the worker only passes task_id + a metadata
    summary; completion_notify holds the routing context under CORVIN_HOME and
    the adapter/bg_monitor pollers deliver it to the messenger. Best-effort:
    any failure is swallowed so it never affects task execution.
    """
    try:
        import completion_notify as _cn  # type: ignore  # shared dir on sys.path
        _cn.mark_done(task_id, text=summary, ok=ok)
    except Exception as e:  # noqa: BLE001
        logger.debug("completion_notify mark_done skipped for %s: %s", task_id, e)


# Runtime-home resolver — MUST match the enqueue WRITER (routes/tasks_impl.py uses
# forge.paths.tenant_global_dir). A bare Path.home()/.corvin here ignored
# CORVIN_HOME → the worker polled ~/.corvin while the console enqueued under the
# pinned <repo>/.corvin → zero tasks ever ran (path-audit 2026-06-25 #CRITICAL2).
from forge import paths as _forge_paths  # type: ignore  # noqa: E402

# ClaudeCodeEngine argv builder
try:
    _shared = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    _agents_dir = os.path.join(_shared, "..", "..", "operator", "bridges", "shared")
    if _agents_dir not in sys.path:
        sys.path.insert(0, os.path.abspath(_agents_dir))
    from agents.claude_code import ClaudeCodeEngine as _ClaudeCodeEngine  # type: ignore
    _ENGINE_AVAILABLE = True
except ImportError:
    _ENGINE_AVAILABLE = False
    _ClaudeCodeEngine = None

# L34 data-classification + L35 egress are NOT imported here directly any more.
# The full pre-spawn gate (L44 + ADR-0141 capability + L34 + L35) runs through
# the console SSOT chokepoint ``_spawn_gates.check_console_spawn_or_refusal``
# (see ``_pre_spawn_gate`` below), which imports the canonical ``spawn_gates``
# SSOT internally. Keeping a second, divergent partial copy of the L34/L35
# loaders here was the structural origin of findings #2/#6 (partial + fail-open).

# ── Module-level abort registry (M6) ─────────────────────────────────────────
# Maps task_id -> running asyncio.subprocess.Process.
# abort_task_handler (tasks_impl.py) calls signal_abort() to terminate it.
_active_procs: dict[str, "asyncio.subprocess.Process"] = {}


def signal_abort(task_id: str) -> bool:
    """Signal a running task subprocess to terminate (M6).

    Called by abort_task_handler. Returns True if a process was found and
    SIGTERM was sent; False if no running process was registered.
    """
    proc = _active_procs.get(task_id)
    if proc is None:
        return False
    try:
        proc.terminate()
        return True
    except (ProcessLookupError, OSError):
        return False


# ── Audit helpers (M4) ────────────────────────────────────────────────────────

_AUDIT_ALLOWED: dict[str, frozenset[str]] = {
    "task.spawn_started":  frozenset({"task_id", "tenant_id", "chat_key_prefix", "engine"}),
    "task.spawn_terminal": frozenset({"task_id", "tenant_id", "state", "duration_ms", "exit_code"}),
    "task.spawn_denied":   frozenset({"task_id", "tenant_id", "reason"}),
}


def _task_audit_emit(
    event: str,
    audit_path: Path,
    *,
    task_id: str,
    tenant_id: str,
    **details,
) -> None:
    """Emit a task.* audit event to the L16 hash chain.

    Allow-list enforced; fields not in the list are silently dropped to
    prevent accidental instruction-text leakage into the chain.
    """
    if not _AUDIT_AVAILABLE or _forge_write_event is None:
        return
    allowed = _AUDIT_ALLOWED.get(event, frozenset())
    safe = {k: v for k, v in details.items() if k in allowed}
    safe["task_id"] = task_id
    safe["tenant_id"] = tenant_id
    try:
        _forge_write_event(audit_path, event, details=safe)
    except Exception:
        logger.exception("audit emit failed for %s", event)


def _audit_path_for_tenant(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "audit.jsonl"


# ── Instruction sanitization (M4) ────────────────────────────────────────────

_MAX_INSTRUCTION_BYTES = 32_000


def _sanitize_instruction(raw: str) -> str:
    """NFKC-normalize, strip control chars, enforce size cap.

    Mirrors the a2a_worker sanitization (ADR-0101 M4).
    """
    if not isinstance(raw, str):
        raise ValueError("instruction must be a string")
    # NFKC normalization
    text = unicodedata.normalize("NFKC", raw)
    # Strip ASCII control chars (except \t \n \r)
    text = "".join(
        ch for ch in text
        if ch in ("\t", "\n", "\r") or not unicodedata.category(ch).startswith("C")
    )
    # Size re-check after normalization
    encoded = text.encode("utf-8")
    if len(encoded) > _MAX_INSTRUCTION_BYTES:
        raise ValueError(
            f"instruction too large after sanitization: "
            f"{len(encoded)} bytes (max {_MAX_INSTRUCTION_BYTES})"
        )
    return text


# ── Worker pool ───────────────────────────────────────────────────────────────

class TaskWorkerPool:
    """Independent worker pool processing tasks from queue.

    ADR-0101 changes:
    - Spawns via ClaudeCodeEngine._build_args() for correct argv (hooks, permission_mode)
    - Emits task.spawn_started to L16 chain BEFORE subprocess spawn (audit-first)
    - Checks L34 data classification before spawn (fail-open on import errors)
    - Instruction loaded from payload file (M3), sanitized before spawn (M4)
    - Stores subprocess reference in _active_procs for real abort (M6)
    - Dynamic multi-tenant discovery (M7)
    - Correct shutdown drain via asyncio.wait() (M9)
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        taskmanager_factory=None,
        pubsub_factory=None,
        max_workers: Optional[int] = None,
    ):
        self.task_queue = task_queue
        self.taskmanager_factory = taskmanager_factory
        self.pubsub_factory = pubsub_factory
        self.max_workers = max_workers or int(os.getenv("CORVIN_TASK_MAX_WORKERS", "5"))
        self.semaphore = asyncio.Semaphore(self.max_workers)
        self._shutdown = False
        # M6: running task handles for abort
        self._running_tasks: dict[str, asyncio.Task] = {}

    @property
    def active_workers(self) -> int:
        return len(self._running_tasks)

    async def run(self, poll_interval_ms: float = 100) -> None:
        """Main worker pool loop."""
        logger.info("Worker pool started: max_workers=%d", self.max_workers)

        cleanup_task = asyncio.create_task(self._cleanup_loop())
        poll_interval = poll_interval_ms / 1000.0

        try:
            while not self._shutdown:
                try:
                    for tenant_id in self._discover_tenants():
                        task = self.task_queue.dequeue(tenant_id)
                        if task:
                            t = asyncio.create_task(self._execute_task(task))
                            self._running_tasks[task.task_id] = t
                            t.add_done_callback(
                                lambda fut, tid=task.task_id: self._running_tasks.pop(tid, None)
                            )
                    await asyncio.sleep(poll_interval)
                except Exception:
                    logger.exception("Error in worker pool loop")
                    await asyncio.sleep(poll_interval)
        finally:
            cleanup_task.cancel()
            logger.info("Worker pool shutdown")

    def _discover_tenants(self) -> list[str]:
        """M7: Dynamically discover tenants that have task queue files."""
        tenants_dir = _forge_paths.corvin_home() / "tenants"
        if not tenants_dir.exists():
            return ["_default"]
        result = []
        try:
            for d in tenants_dir.iterdir():
                if not d.is_dir():
                    continue
                global_dir = d / "global"
                if any(global_dir.glob("tasks_*.jsonl")):
                    result.append(d.name)
        except OSError:
            pass
        return result or ["_default"]

    def _pre_spawn_gate(self, instruction: str, task) -> Optional[str]:
        """Run the full console SSOT pre-spawn gate (findings #2/#6).

        Returns ``None`` when the spawn is PERMITTED, else a user-facing refusal
        string. Delegates to ``_spawn_gates.check_console_spawn_or_refusal`` so
        this daemon path runs the IDENTICAL L44 / capability / L34 / L35 gate
        sequence every authenticated console route runs — each gate is
        internally fail-closed and audit-first (its L16 deny event lands on the
        per-tenant chain BEFORE the refusal string is returned).

        FAIL-CLOSED at the import boundary too: if the gate module cannot be
        imported or the call raises unexpectedly, we REFUSE (return a string) —
        an acceptable-use / data-flow guarantee must never evaporate into a
        spawn just because the chokepoint failed to load. (Contrast the old
        partial-L34 block, which fell through to spawn on any error.)
        """
        chat_key = task.chat_key or ""
        try:
            from . import _spawn_gates  # local import: keep daemon import light
        except Exception as exc:  # noqa: BLE001 — gate absent → fail closed
            logger.error(
                "Task %s: pre-spawn gate module import failed (%s) — fail-closed deny",
                task.task_id, type(exc).__name__,
            )
            return ("[security] Pre-spawn safety gate unavailable — request "
                    "blocked (fail-closed).")
        try:
            return _spawn_gates.check_console_spawn_or_refusal(
                instruction,
                tenant_id=task.tenant_id,
                persona="assistant",
                channel="task-worker",
                chat_key=chat_key,
                engine_id="claude_code",
            )
        except Exception as exc:  # noqa: BLE001 — orchestration error → fail closed
            logger.error(
                "Task %s: pre-spawn gate raised (%s) — fail-closed deny",
                task.task_id, type(exc).__name__,
            )
            return ("[security] Pre-spawn safety gate error — request blocked "
                    "(fail-closed).")

    async def _execute_task(self, task) -> None:
        """Execute a single task: audit-first → gates → spawn → stream → audit."""
        async with self.semaphore:
            start_time = time.time()
            audit_path = _audit_path_for_tenant(task.tenant_id)
            _span_started = False  # ADR-0171: gate span.end so a pre-start
                                   # failure (e.g. mkdir) can't emit an orphan end

            try:
                logger.debug("Starting task %s: %s", task.task_id, task.chat_key)

                # Load instruction from payload file (M3)
                instruction_raw = self.task_queue.load_instruction(task.task_id)
                if instruction_raw is None:
                    logger.warning("Task %s: payload file missing, skipping", task.task_id)
                    self.task_queue.update_status(task.task_id, TaskStatus.FAILED, exit_code=125)
                    return

                # Sanitize instruction (M4)
                try:
                    instruction = _sanitize_instruction(instruction_raw)
                except ValueError as e:
                    logger.warning("Task %s: instruction sanitization failed: %s", task.task_id, e)
                    _task_audit_emit(
                        "task.spawn_denied", audit_path,
                        task_id=task.task_id, tenant_id=task.tenant_id,
                        reason="instruction-sanitization-failed",
                    )
                    self.task_queue.update_status(task.task_id, TaskStatus.FAILED, exit_code=125)
                    return

                # ── Full fail-closed, audit-first pre-spawn gate (findings #2/#6) ──
                # The previous gate here ran ONLY a partial L34 (SECRET→cloud deny)
                # and fail-OPEN on any error — no L44 acceptable-use, no ADR-0141
                # capability presence, no L35 egress. That is a divergent partial
                # copy of the gate every authenticated console route already runs.
                # Route this daemon spawn path through the SAME console SSOT
                # chokepoint (``_spawn_gates.check_console_spawn_or_refusal``):
                #   (a) L44 house-rules (ADR-0143, fail-closed, audit-first)
                #   (b) ADR-0141 Tier-3 capability presence
                #   (c) L34 data-classification flow guard (ADR-0042)
                #   (d) L35 network egress lockdown (ADR-0043)
                # On a non-None refusal we DENY (fail-closed) — the gate already
                # wrote its own L16 deny event(s) synchronously (audit-first); we
                # add the task.spawn_denied lifecycle record (metadata-only) so the
                # task chain reflects the block. This runs BEFORE the audit-first
                # task.spawn_started and BEFORE create_subprocess_exec, so the path
                # is gated even before the tasks router/daemon are wired.
                _gate_refusal = self._pre_spawn_gate(instruction, task)
                if _gate_refusal is not None:
                    logger.warning("Task %s: pre-spawn gate blocked the spawn", task.task_id)
                    _task_audit_emit(
                        "task.spawn_denied", audit_path,
                        task_id=task.task_id, tenant_id=task.tenant_id,
                        reason="pre-spawn-gate-denied",
                    )
                    self.task_queue.update_status(task.task_id, TaskStatus.FAILED, exit_code=125)
                    return

                # Derive working directory with path-traversal guard (M1)
                try:
                    workdir = self._workdir_for_chat_key(task.chat_key)
                except ValueError as e:
                    logger.warning("Task %s: invalid chat_key: %s", task.task_id, e)
                    _task_audit_emit(
                        "task.spawn_denied", audit_path,
                        task_id=task.task_id, tenant_id=task.tenant_id,
                        reason="invalid-chat-key",
                    )
                    self.task_queue.update_status(task.task_id, TaskStatus.FAILED, exit_code=125)
                    return

                workdir.mkdir(parents=True, exist_ok=True)

                # Audit-first: write task.spawn_started BEFORE spawning (M4, load-bearing)
                _task_audit_emit(
                    "task.spawn_started", audit_path,
                    task_id=task.task_id,
                    tenant_id=task.tenant_id,
                    chat_key_prefix=task.chat_key[:8] if task.chat_key else "",
                    engine="claude_code",
                )
                # ADR-0171 — engine.span.start (role=worker), audit-first like above.
                _emit_task_engine_span("start", audit_path, task_id=task.task_id)
                _span_started = True

                # Build argv via ClaudeCodeEngine._build_args() for correct hook config (M4).
                # _build_args() is a @staticmethod — we resolve the binary separately so
                # CLAUDE_BIN / PATH fallback logic runs exactly once at spawn time.
                if _ENGINE_AVAILABLE and _ClaudeCodeEngine is not None:
                    _binary = _ClaudeCodeEngine().binary  # resolves CLAUDE_BIN + PATH fallback
                    argv = _ClaudeCodeEngine._build_args(
                        instruction,
                        binary=_binary,
                        permission_mode="bypassPermissions",
                        streaming=True,
                    )
                else:
                    argv = [
                        os.environ.get("CLAUDE_BIN", "claude"),
                        "-p", instruction,
                        "--output-format", "stream-json", "--verbose",
                    ]

                self.task_queue.update_status(task.task_id, TaskStatus.RUNNING)

                tm_dir = workdir / "tasks"
                tm = self.taskmanager_factory(tm_dir) if self.taskmanager_factory else None
                if tm:
                    tm.record_event(
                        task.task_id,
                        {"event": "task.started", "engine": "claude_code", "pid": os.getpid()},
                    )

                # Spawn subprocess (M4: uses engine-built argv)
                proc = await asyncio.create_subprocess_exec(
                    *argv,
                    stdin=asyncio.subprocess.DEVNULL,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    cwd=str(workdir),
                )

                # Register for abort (M6)
                _active_procs[task.task_id] = proc

                # Stream output
                event_count = 0
                result_text = ""  # captured for the completion notification
                async for line in self._stream_lines(proc.stdout):
                    try:
                        event = json.loads(line)
                        if tm:
                            tm.record_event(task.task_id, event)
                        if self.pubsub_factory:
                            await self.pubsub_factory().publish(
                                task.tenant_id, task.task_id, event,
                            )
                        # Capture the final result text so the messenger
                        # notification carries the actual outcome, not just
                        # "completed in N ms" metadata.
                        if (event.get("type") == "result"
                                and isinstance(event.get("result"), str)):
                            result_text = event["result"]
                        event_count += 1
                    except json.JSONDecodeError:
                        pass

                rc = await proc.wait()
                duration_ms = int((time.time() - start_time) * 1000)

                if rc == 0:
                    self.task_queue.update_status(task.task_id, TaskStatus.COMPLETED, exit_code=0)
                    if tm:
                        tm.record_event(
                            task.task_id,
                            {"event": "task.completed", "exit_code": 0,
                             "duration_ms": duration_ms, "event_count": event_count},
                        )
                    _task_audit_emit(
                        "task.spawn_terminal", audit_path,
                        task_id=task.task_id, tenant_id=task.tenant_id,
                        state="completed", duration_ms=duration_ms, exit_code=0,
                    )
                    _emit_task_engine_span("end", audit_path, task_id=task.task_id,
                                           status="ok", duration_ms=duration_ms)
                    _notify_task_done(
                        task.task_id, ok=True,
                        summary=(result_text.strip()[:1500]
                                 or f"completed in {duration_ms} ms "
                                    f"({event_count} events)."))
                    logger.info("Task %s completed (%dms)", task.task_id, duration_ms)
                else:
                    state = "cancelled" if rc == -15 else "failed"
                    final_status = (
                        TaskStatus.CANCELLED if rc == -15 else TaskStatus.FAILED
                    )
                    self.task_queue.update_status(task.task_id, final_status, exit_code=rc)
                    if tm:
                        tm.record_event(
                            task.task_id,
                            {"event": "task.failed", "exit_code": rc, "duration_ms": duration_ms},
                        )
                    _task_audit_emit(
                        "task.spawn_terminal", audit_path,
                        task_id=task.task_id, tenant_id=task.tenant_id,
                        state=state, duration_ms=duration_ms, exit_code=rc,
                    )
                    _emit_task_engine_span("end", audit_path, task_id=task.task_id,
                                           status="error", duration_ms=duration_ms)
                    _notify_task_done(task.task_id, ok=False,
                                      summary=f"{state} (exit code {rc}).")
                    logger.warning("Task %s %s (rc=%d)", task.task_id, state, rc)

            except asyncio.CancelledError:
                logger.info("Task %s asyncio-cancelled", task.task_id)
                self.task_queue.update_status(task.task_id, TaskStatus.CANCELLED)
                _notify_task_done(task.task_id, ok=False, summary="was cancelled.")
                _task_audit_emit(
                    "task.spawn_terminal", audit_path,
                    task_id=task.task_id, tenant_id=task.tenant_id,
                    state="cancelled", duration_ms=int((time.time() - start_time) * 1000),
                    exit_code=-1,
                )
                if _span_started:
                    _emit_task_engine_span("end", audit_path, task_id=task.task_id,
                                           status="error",
                                           duration_ms=int((time.time() - start_time) * 1000))
                raise
            except Exception:
                logger.exception("Task %s error", task.task_id)
                self.task_queue.update_status(task.task_id, TaskStatus.FAILED, exit_code=1)
                _notify_task_done(task.task_id, ok=False,
                                  summary="failed with an internal error.")
                _task_audit_emit(
                    "task.spawn_terminal", audit_path,
                    task_id=task.task_id, tenant_id=task.tenant_id,
                    state="failed", duration_ms=int((time.time() - start_time) * 1000),
                    exit_code=1,
                )
                if _span_started:
                    _emit_task_engine_span("end", audit_path, task_id=task.task_id,
                                           status="error",
                                           duration_ms=int((time.time() - start_time) * 1000))
            finally:
                _active_procs.pop(task.task_id, None)

    async def _cleanup_loop(self) -> None:
        """Background cleanup: mark expired tasks as FAILED."""
        try:
            while not self._shutdown:
                try:
                    for tenant_id in self._discover_tenants():
                        count = self.task_queue.cleanup_expired_tasks(tenant_id)
                        if count > 0:
                            logger.info("Marked %d expired tasks as FAILED (tenant=%s)", count, tenant_id)
                    await asyncio.sleep(60)
                except Exception:
                    logger.exception("Cleanup error")
                    await asyncio.sleep(60)
        except asyncio.CancelledError:
            pass

    async def shutdown(self) -> None:
        """Graceful shutdown: wait for active workers (M9)."""
        logger.info("Shutdown initiated")
        self._shutdown = True
        pending = list(self._running_tasks.values())
        if pending:
            logger.info("Waiting for %d active tasks to finish (max 30s)...", len(pending))
            done, still_running = await asyncio.wait(pending, timeout=30.0)
            if still_running:
                logger.warning(
                    "Shutdown: %d tasks still running after 30s — forcing cancel",
                    len(still_running),
                )
                for t in still_running:
                    t.cancel()
        logger.info("Worker pool shutdown complete")

    async def _stream_lines(self, reader) -> AsyncIterator[str]:
        """Stream lines from subprocess stdout."""
        while True:
            line = await reader.readline()
            if not line:
                break
            yield line.decode(errors="replace").rstrip("\n")

    def _workdir_for_chat_key(self, chat_key: str) -> Path:
        """Derive working directory from chat_key with path-traversal guard (M1).

        Raises:
            ValueError: if chat_key attempts path traversal.
        """
        corvin_home = _forge_paths.corvin_home()
        sessions_root = (corvin_home / "sessions").resolve()
        # Sanitise the chat_key into a single safe path component: ``:`` is
        # illegal on Windows (WinError 267) and ``/`` would traverse. POSIX
        # no-op for normal keys, so existing dirs are unchanged.
        safe_key = _forge_paths.fs_safe_component(chat_key)
        candidate = (corvin_home / "sessions" / safe_key).resolve()
        # Ensure the resolved path stays inside sessions_root
        if not str(candidate).startswith(str(sessions_root) + os.sep) \
                and candidate != sessions_root:
            raise ValueError(
                f"chat_key rejected (path traversal): {chat_key!r}"
            )
        return candidate


async def main():
    """CLI entry point for worker daemon."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    from corvin_console.task_queue import TaskQueue
    from corvin_console.task_manager import TaskManager

    tenant_global_dir = _forge_paths.tenant_global_dir("_default")
    queue = TaskQueue(tenant_global_dir)

    def make_taskmanager(tasks_dir):
        return TaskManager(tasks_dir)

    pool = TaskWorkerPool(queue, taskmanager_factory=make_taskmanager)

    loop = asyncio.get_running_loop()

    def signal_handler():
        asyncio.create_task(pool.shutdown())

    # loop.add_signal_handler is NOT implemented on the Windows ProactorEventLoop
    # (raises NotImplementedError) — it would crash this standalone worker at boot.
    # POSIX: wire SIGTERM/SIGINT to a graceful shutdown; Windows: rely on
    # KeyboardInterrupt / process termination.
    if sys.platform != "win32":
        import signal as _signal
        for _sig in (_signal.SIGTERM, _signal.SIGINT):
            loop.add_signal_handler(_sig, signal_handler)

    await pool.run()


if __name__ == "__main__":
    asyncio.run(main())
