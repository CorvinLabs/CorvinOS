"""CodexCliEngine — wraps `codex exec --json`.

Phase 1 (ADR-0001): minimal-invasive engine that spawns the OpenAI Codex
CLI as a non-interactive subprocess. New backend, no existing code path
to interop with. Exists primarily to validate the WorkerEngine protocol
against a non-Claude backend.

Stream event shape from `codex exec --json` (codex-cli 0.125.0):

    {"type":"thread.started","thread_id":"019e..."}
    {"type":"turn.started"}
    {"type":"item.completed","item":{"id":"item_0","type":"agent_message","text":"pong"}}
    {"type":"turn.completed","usage":{"input_tokens":23267,"output_tokens":16,...}}

Mapping (see ADR-0001):
    thread.started                                       -> session_started
    item.completed (item.type=agent_message)             -> text_delta
    turn.completed                                       -> turn_completed
    everything else (turn.started, thinking, ...)        -> dropped

Codex notes
-----------

* Unlike Claude Code's stream, Codex does NOT emit incremental text
  deltas — the agent_message arrives complete in one item.completed.
* Codex's `usage` shape differs (input_tokens/cached_input_tokens/
  output_tokens vs. Claude's input_tokens/cache_creation_input_tokens/
  cache_read_input_tokens/output_tokens). Engine surfaces both shapes
  verbatim under `event.usage`; consumers normalize at the call site.
* Default sandbox for the engine is `read-only`. Callers that need
  workspace-write or full-access pass `--sandbox` via `extra_args`.
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Iterator

from . import StreamEvent, parse_jsonl_line


# Stderr tail buffer cap (chars). Mirrors ClaudeCodeEngine: large enough
# to hold a typical provider error body, small enough that a runaway log
# line can't grow the engine's RSS without bound. See claude_code.py.
_STDERR_TAIL_CHARS = 4096


# Path resolution: try $CODEX_BIN, then the canonical nvm location
# (where `npm install -g @openai/codex` lands when nvm-managed Node is
# active). Fall back to the bare name and let PATH resolution try.
def _resolve_codex_binary() -> str:
    if (override := os.environ.get("CODEX_BIN")):
        return override
    nvm_candidate = Path.home() / ".nvm/versions/node/v22.22.0/bin/codex"
    if nvm_candidate.exists():
        return str(nvm_candidate)
    return "codex"


class CodexCliEngine:
    """OpenAI Codex CLI as a backend-agnostic engine."""

    name = "codex_cli"

    capabilities: dict[str, Any] = {
        "mid_stream_inject": "buffered",  # M2: buffered /btw queue for next turn
        "hooks": "teb_brokered",  # M4: synthetic hooks via TEB
        "skills": "append_system_prompt",  # M3: skill compilation
        "skills_tool": False,           # no first-class skill API; emulate via prompt
        "mcp": True,                    # `codex mcp` subcommand
        "stream_json": True,
        "permission_modes": ["read-only", "workspace-write", "danger-full-access"],
        "add_system_prompt": False,     # no --append-system-prompt; use prompt-prefix
        "version_flag": "--version",
        "session_pinning": False,       # ADR-0049: no --resume equivalent
    }

    def __init__(self, *, binary: str | None = None) -> None:
        self.binary = binary or _resolve_codex_binary()
        self._proc: subprocess.Popen[bytes] | None = None
        # /stop can arrive AFTER the adapter registered this engine but BEFORE
        # spawn() creates _proc. Without this latch, cancel() would no-op on a
        # None _proc while the adapter reports "aborted", then the turn spawns
        # and runs to completion (false-positive ACK + false-negative cancel).
        # spawn() checks it right after Popen to honour a pre-spawn cancel.
        self._cancel_requested = False
        # Stderr ring buffer, drained concurrently with stdout so a child
        # writing >64KiB to stderr mid-run can never deadlock (H9). Mirror
        # of ClaudeCodeEngine's mechanism.
        self._stderr_buf: deque[str] = deque()
        self._stderr_buf_chars: int = 0
        self._stderr_guard = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    _SANDBOX_MAP: dict[str, str] = {
        "bypassPermissions": "danger-full-access",
        "acceptEdits": "workspace-write",
        "default": "read-only",
        "plan": "read-only",
    }

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        working_dir: Path | None = None,
        timeout: float = 120.0,
        permission_mode: str | None = None,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        session_dir: Path | None = None,  # M2: for buffered /btw dequeue
    ) -> Iterator[StreamEvent]:
        # M2: Check for buffered /btw injections from prior turns
        user_prompt = prompt
        if session_dir:
            try:
                from eci.transport_buffered import dequeue_all_injections
                buffered = dequeue_all_injections(session_dir)
                if buffered:
                    user_prompt = buffered + "\n\n" + prompt
            except ImportError:
                pass

        # Resolve --sandbox from permission_mode; default to "read-only".
        # Filter any --sandbox already present in extra_args to avoid the
        # "argument used multiple times" error when callers pass it there.
        sandbox = self._SANDBOX_MAP.get(permission_mode or "default", "read-only")
        filtered_extra: list[str] = []
        if extra_args:
            it = iter(extra_args)
            for arg in it:
                if arg == "--sandbox":
                    # consume the value token and let our sandbox win
                    next(it, None)
                else:
                    filtered_extra.append(arg)

        # codex exec is the non-interactive subcommand.
        args = [
            self.binary, "exec",
            "--json",
            "--skip-git-repo-check",
            "--ephemeral",
            "--sandbox", sandbox,
        ]
        if model:
            args += ["--model", model]
        if working_dir:
            args += ["-C", str(working_dir)]
        if filtered_extra:
            args += filtered_extra

        # Codex has no `--append-system-prompt`. Emulate via a prepended
        # SYSTEM block in the prompt itself. Adapter callers that want
        # tighter integration should switch to MCP-server-based system
        # injection (Phase 2+).
        if system:
            full_prompt = f"<SYSTEM>\n{system}\n</SYSTEM>\n\n{user_prompt}"
        else:
            full_prompt = user_prompt

        # Windows fresh-install fix (same bug class as ClaudeCodeEngine —
        # see claude_code.py's spawn() docstring): `full_prompt` carries the
        # same merged system prompt content (persona + memory + skills,
        # routinely 10k+ characters in real bridge usage) that broke every
        # Claude-engine turn on Windows once passed inline through
        # windows_shim_command's single `cmd /c "<line>"` string — cmd.exe's
        # own ~8191-char internal buffer, far below what CreateProcess
        # allows directly. `codex exec --help` documents that the [PROMPT]
        # positional is read from STDIN when omitted or given as `-` — no
        # length ceiling at all, not just a higher one, and it sidesteps
        # argv/cmd.exe entirely rather than needing a temp file. Using it
        # unconditionally (not just for large prompts) keeps this one
        # code path correct regardless of content size.
        args.append("-")

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)

        cwd = str(working_dir) if working_dir else None
        start_time = time.time()

        from ._win_shim import windows_shim_command
        try:
            self._proc = subprocess.Popen(
                # Windows: npm ships this CLI as a .cmd shim — CreateProcess can't
                # launch it directly (WinError 193). windows_shim_command builds
                # a cmd.exe-SAFE command string (a bare ["cmd","/c",*args] list
                # lets a user-prompt metachar break out → RCE); no-op on POSIX.
                windows_shim_command(args),
                stdin=subprocess.PIPE,   # the prompt goes via stdin — see the
                                          # "-" positional arg comment above.
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=spawn_env,
                # H1: own session/process-group so adapter._cancel_chat's
                # os.killpg(getpgid(pid)) targets ONLY this turn's process
                # tree — not the bridge's own group. On Windows this kwarg
                # is a no-op (ignored), matching ClaudeCodeEngine.
                start_new_session=True,
            )
        except FileNotFoundError:
            yield StreamEvent(
                type="error",
                error=f"codex binary not found: {self.binary!r}",
            )
            return

        # Write the full prompt (system block + user turn) to stdin and
        # close it — codex reads until EOF since the positional arg is "-".
        # A write/close failure here means the child died immediately
        # (e.g. sandbox rejection); let the stream/stderr-tail path below
        # surface the real error instead of raising here.
        if self._proc.stdin is not None:
            try:
                self._proc.stdin.write(full_prompt.encode("utf-8"))
                self._proc.stdin.close()
            except (BrokenPipeError, OSError):
                pass

        # Honour a /stop that arrived in the register→spawn window: _proc now
        # exists, so terminate it and emit the same aborted signal a mid-stream
        # cancel would, instead of streaming a turn the user already stopped.
        if self._cancel_requested:
            self._cleanup_proc()
            yield StreamEvent(type="error", error="cancelled")
            return

        # H9: drain stderr concurrently so the pipe buffer can't fill and
        # stall the child mid-stream. The tail is consulted on the
        # no-turn.completed error path below.
        self._start_stderr_drain()

        try:
            yield from self._iter_stream(start_time, timeout)
        finally:
            self._cleanup_proc()

    def _cleanup_proc(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        # Stderr-drain thread exits on EOF once the proc dies; join with a
        # small deadline so it can't outlive the caller.
        t = self._stderr_thread
        if t is not None and t.is_alive():
            try:
                t.join(timeout=2)
            except Exception:
                pass
        for fh in (proc.stdout, proc.stderr):
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass

    # ------------------------------------------------------------------
    # Stderr drain (concurrent, deadlock-safe) — mirror of ClaudeCodeEngine
    # ------------------------------------------------------------------

    def _start_stderr_drain(self) -> None:
        """Spawn a daemon thread that drains proc.stderr into a ring buffer.

        Keeps at most ``_STDERR_TAIL_CHARS`` of the most recent stderr.
        Best-effort: a read error silently terminates the thread; the
        engine still functions, just without a stderr tail.
        """
        with self._stderr_guard:
            self._stderr_buf.clear()
            self._stderr_buf_chars = 0
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        t = threading.Thread(
            target=self._stderr_drain_loop,
            name="codex-stderr-drain",
            daemon=True,
        )
        self._stderr_thread = t
        t.start()

    def _stderr_drain_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        try:
            while True:
                chunk = proc.stderr.readline()
                if not chunk:
                    return
                if isinstance(chunk, bytes):
                    chunk = chunk.decode("utf-8", errors="replace")
                self._push_stderr(chunk)
        except Exception:
            return

    def _push_stderr(self, chunk: str) -> None:
        if not chunk:
            return
        with self._stderr_guard:
            self._stderr_buf.append(chunk)
            self._stderr_buf_chars += len(chunk)
            while (
                self._stderr_buf
                and self._stderr_buf_chars > _STDERR_TAIL_CHARS
            ):
                popped = self._stderr_buf.popleft()
                self._stderr_buf_chars -= len(popped)

    def stderr_tail(self, *, max_chars: int = 500) -> str:
        """Return the last ``max_chars`` characters of stderr seen so far."""
        with self._stderr_guard:
            joined = "".join(self._stderr_buf)
        joined = joined.strip()
        if len(joined) > max_chars:
            joined = joined[-max_chars:]
        return joined

    def cancel(self) -> None:
        # Latch first so a cancel racing ahead of spawn()'s Popen is not lost —
        # spawn() checks _cancel_requested right after creating _proc.
        self._cancel_requested = True
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ECI manifest (ADR-0069 M2+M6)
    @property
    def command_manifest(self):  # type: ignore[return]
        from eci import EngineCommandManifest
        return EngineCommandManifest(
            mid_stream_inject="buffered",  # M2: buffered queue for /btw
            cancel="sigterm",
            compact=None,
            native_commands={},
        )

    # ------------------------------------------------------------------
    # Internal: stream parser
    # ------------------------------------------------------------------

    def _iter_stream(
        self, start_time: float, timeout: float
    ) -> Iterator[StreamEvent]:
        proc = self._proc
        assert proc is not None and proc.stdout is not None

        completed = False
        accumulated_text: list[str] = []

        for raw_line in proc.stdout:
            if time.time() - start_time > timeout:
                yield StreamEvent(type="error", error="codex stream timeout")
                return

            obj = parse_jsonl_line(raw_line)
            if obj is None:
                continue

            event = self._normalise(obj, accumulated_text)
            if event is None:
                continue
            yield event
            if event.type == "turn_completed":
                completed = True
                break
            if event.type == "error":
                completed = True
                break

        if not completed:
            # The drain thread owns proc.stderr; read the buffered tail
            # instead of racing a direct proc.stderr.read() (which would
            # return empty because the thread already consumed it).
            err = self.stderr_tail(max_chars=1000) or (
                f"codex exited without turn.completed (exit_code={proc.poll()})"
            )
            yield StreamEvent(type="error", error=err)

    @staticmethod
    def _normalise(
        obj: dict[str, Any], accumulated_text: list[str]
    ) -> StreamEvent | None:
        kind = obj.get("type")

        if kind == "thread.started":
            return StreamEvent(type="session_started", raw=obj)

        if kind == "item.completed":
            item = obj.get("item") or {}
            if item.get("type") == "agent_message":
                text = item.get("text", "") or ""
                if text:
                    accumulated_text.append(text)
                    return StreamEvent(type="text_delta", text=text, raw=obj)
            return None

        if kind == "turn.completed":
            usage = obj.get("usage") or {}
            # Codex doesn't include final-text in turn.completed — pull
            # from accumulated_text so collect() ends up with the same
            # final_text shape regardless of engine.
            final_text = "".join(accumulated_text)
            return StreamEvent(
                type="turn_completed",
                text=final_text,
                usage=usage,
                raw=obj,
            )

        if kind == "turn.failed":
            err = obj.get("error") or obj.get("message") or "turn.failed"
            return StreamEvent(type="error", error=str(err), raw=obj)

        # turn.started, item.in_progress, thread.completed, ... → ignore.
        return None
