"""OpenCodeEngine — wraps `opencode run --format json`.

Third backend after ClaudeCodeEngine and CodexCliEngine. Spawns the
upstream opencode CLI (https://github.com/anomalyco/opencode) as a
non-interactive subprocess. Opt-in via CORVIN_ENGINE=opencode (or the
adapter's engine_factory injection point) — default stays Claude Code.

OpenCode is provider-agnostic: the `--model provider/model` flag
selects the backing LLM. The intended local-first path uses Ollama
through opencode's openai-compatible provider config (see
`~/.config/opencode/opencode.json` with a `provider.ollama` block
pointing at `http://localhost:11434/v1`).

Event shape emitted by `opencode run --format json`:

    {"type":"step_start","timestamp":...,"sessionID":"ses_...","part":{...}}
    {"type":"text","timestamp":...,"sessionID":"ses_...","part":{"type":"text","text":"PINGOK","time":{"start":...,"end":...}}}
    {"type":"tool_use","timestamp":...,"sessionID":"ses_...","part":{"type":"tool","state":{"status":"completed",...},...}}
    {"type":"step_finish","timestamp":...,"sessionID":"ses_...","part":{...}}
    {"type":"reasoning","timestamp":...,"sessionID":"ses_...","part":{"text":"..."}}
    {"type":"error","timestamp":...,"sessionID":"ses_...","error":{"name":"...","data":{"message":"..."}}}

Mapping (see ADR-0001):
    first step_start            -> session_started
    text (with part.text)       -> text_delta
    tool_use                    -> tool_call (raw part in `raw`)
    error                       -> error
    stdout EOF                  -> synthesized turn_completed
    reasoning / step_finish     -> dropped (raw preserved on demand)

OpenCode notes
--------------

* Unlike Claude Code, opencode does NOT stream text deltas — `text`
  events arrive with `part.time.end` set, i.e. the complete text
  block. Same as Codex.
* Stream-end signal is stdout EOF (opencode's internal `session.status
  idle` breaks the JSON loop but is not emitted itself).
* `--dangerously-skip-permissions` is the only sanctioned bypass flag;
  there is no `--permission-mode` equivalent.
* No `--append-system-prompt`. System content is prepended into the
  prompt as a `<SYSTEM>` block, mirror of the Codex emulation.
* Mid-stream injection (`/btw`) is NOT supported — opencode has no
  streaming-stdin entry point.
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


def _resolve_opencode_binary() -> str:
    """Locate the opencode binary.

    Resolution order:
      1. $OPENCODE_BIN env override
      2. ~/.opencode/bin/opencode (the curl-script default)
      3. fall through to PATH lookup
    """
    if (override := os.environ.get("OPENCODE_BIN")):
        return override
    candidate = Path.home() / ".opencode" / "bin" / "opencode"
    if candidate.exists():
        return str(candidate)
    return "opencode"


class OpenCodeEngine:
    """Provider-agnostic open-source coding agent (anomalyco/opencode)."""

    name = "opencode"

    capabilities: dict[str, Any] = {
        "mid_stream_inject": "buffered",  # M2: buffered /btw queue for next turn
        "hooks": "teb_brokered",  # M4: synthetic hooks via TEB
        "skills": "append_system_prompt",  # M3: skill compilation
        "skills_tool": False,           # uses --agent instead of a Skill tool
        "mcp": True,                    # `opencode mcp` subcommand wires MCP
        "stream_json": True,
        "permission_modes": ["default", "bypassPermissions"],
        "add_system_prompt": False,     # no --append-system-prompt; prefix the prompt
        "version_flag": "--version",
        "session_pinning": False,       # ADR-0049: no --resume equivalent
    }

    def __init__(self, *, binary: str | None = None) -> None:
        self.binary = binary or _resolve_opencode_binary()
        self._proc: subprocess.Popen[bytes] | None = None
        self._override_model: str | None = None  # set via /e:model ECI handler
        # Stderr ring buffer, drained concurrently with stdout so a child
        # writing >64KiB to stderr mid-run can never deadlock (H9). Mirror
        # of ClaudeCodeEngine's mechanism.
        self._stderr_buf: deque[str] = deque()
        self._stderr_buf_chars: int = 0
        self._stderr_guard = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # argv composition (static so tests can golden-snapshot it)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(
        *,
        binary: str,
        prompt: str,
        model: str | None = None,
        agent: str | None = None,
        working_dir: Path | None = None,
        continue_session: bool = False,
        session_id: str | None = None,
        fork: bool = False,
        permission_mode: str | None = None,
        file_attachments: list[str] | None = None,
        extra_args: list[str] | None = None,
    ) -> list[str]:
        args = [binary, "run", "--format", "json"]

        if permission_mode == "bypassPermissions" or permission_mode is None:
            # Default to permission-bypass for bridge / scripted use. The
            # adapter's path-gate hook is the structural enforcement; the
            # CLI flag is just the "don't ask me" signal to opencode.
            args.append("--dangerously-skip-permissions")
        elif permission_mode in ("plan",):
            # opencode's `plan` agent is the read-only equivalent.
            args += ["--agent", "plan"]
        # other modes (default / acceptEdits) fall through with no flag.

        if model:
            args += ["--model", model]
        if agent and permission_mode != "plan":
            # An explicit agent overrides the plan-shortcut above.
            args += ["--agent", agent]
        if working_dir:
            args += ["--dir", str(working_dir)]
        if continue_session:
            args += ["-c"]
        elif session_id:
            args += ["-s", session_id]
        if fork:
            args += ["--fork"]
        for fpath in file_attachments or []:
            args += ["-f", str(fpath)]
        if extra_args:
            args += list(extra_args)

        # Prompt is the trailing positional. opencode also accepts
        # `--prompt`, but the positional form is the documented happy
        # path and survives shell-quirks better.
        if prompt:
            args.append(prompt)

        return args

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

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

        # System prompt emulation: opencode has no --append-system-prompt,
        # so prepend a <SYSTEM> block to the user prompt. Mirror of the
        # Codex pattern in codex_cli.py.
        if system:
            full_prompt = f"<SYSTEM>\n{system}\n</SYSTEM>\n\n{user_prompt}"
        else:
            full_prompt = user_prompt

        # Honour ECI /e:model override when caller doesn't supply a model.
        effective_model = model or self._override_model
        args = self._build_args(
            binary=self.binary,
            prompt=full_prompt,
            model=effective_model,
            working_dir=working_dir,
            permission_mode=permission_mode,
            extra_args=extra_args,
        )

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)

        cwd = str(working_dir) if working_dir else None
        start_time = time.time()

        try:
            self._proc = subprocess.Popen(
                # Windows .cmd-shim wrap — same rationale as agents/claude_code.py
                # (WinError 193 on npm-installed CLIs).
                (["cmd", "/c", *args] if (os.name == "nt" and args
                    and str(args[0]).lower().endswith((".cmd", ".bat"))) else args),
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
                error=f"opencode binary not found: {self.binary!r}",
            )
            return

        # H9: drain stderr concurrently so the pipe buffer can't fill and
        # stall the child mid-stream. The tail is consulted on the
        # process-died-non-zero error path below.
        self._start_stderr_drain()

        try:
            yield from self._iter_stream(start_time, timeout)
        finally:
            self._cleanup_proc()

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    # ECI manifest (ADR-0069 M2+M6)
    @property
    def command_manifest(self):  # type: ignore[return]
        from eci import EngineCommandManifest, NativeCommandSpec
        return EngineCommandManifest(
            mid_stream_inject="buffered",  # M2: buffered queue for /btw
            cancel="sigterm",
            compact=None,
            native_commands={
                "model": NativeCommandSpec(
                    description="Switch provider/model (provider/model format)",
                    handler_method="eci_set_model",
                    usage="<provider/model>",
                ),
            },
        )

    def eci_set_model(self, args: str) -> object:
        from eci import CommandResult
        model = args.strip()
        if not model:
            return CommandResult(success=False, message="✗ /e:model: Modell-Argument fehlt")
        self._override_model = model
        return CommandResult(success=True, message=f"🔄 OpenCode-Modell gesetzt: {model}")

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
            name="opencode-stderr-drain",
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

    # ------------------------------------------------------------------
    # Internal: stream parser
    # ------------------------------------------------------------------

    def _iter_stream(
        self, start_time: float, timeout: float
    ) -> Iterator[StreamEvent]:
        proc = self._proc
        assert proc is not None and proc.stdout is not None

        accumulated_text: list[str] = []
        saw_session_started = False
        error_seen: str | None = None

        for raw_line in proc.stdout:
            if time.time() - start_time > timeout:
                yield StreamEvent(type="error", error="opencode stream timeout")
                return

            obj = parse_jsonl_line(raw_line)
            if obj is None:
                continue

            for event in self._normalise_all(
                obj,
                accumulated_text=accumulated_text,
                saw_session_started=saw_session_started,
            ):
                if event.type == "session_started":
                    saw_session_started = True
                if event.type == "error":
                    error_seen = event.error or "opencode error"
                yield event
                # Unlike Claude Code we do NOT break on error events:
                # opencode keeps streaming text after a recoverable
                # session.error in some cases. The terminal signal is
                # stdout EOF.

        # stdout EOF: synthesize the terminal event. Codex does the same.
        if error_seen and not accumulated_text:
            # Pure failure — surface as error terminal.
            yield StreamEvent(type="error", error=error_seen)
            return

        if proc.poll() not in (0, None) and not accumulated_text:
            # Process died non-zero without yielding text. Read the tail
            # from the concurrent drain buffer instead of racing a direct
            # proc.stderr.read() (which the drain thread already consumed).
            err = self.stderr_tail(max_chars=1000) or (
                f"opencode exited with code {proc.poll()}"
            )
            yield StreamEvent(type="error", error=err)
            return

        yield StreamEvent(
            type="turn_completed",
            text="".join(accumulated_text),
            usage={},  # opencode does not surface usage on the JSON stream
        )

    @staticmethod
    def _normalise_all(
        obj: dict[str, Any],
        *,
        accumulated_text: list[str],
        saw_session_started: bool,
    ) -> list[StreamEvent]:
        kind = obj.get("type")
        events: list[StreamEvent] = []

        if kind == "step_start":
            # First step_start carries the new sessionID — treat as
            # session_started. Subsequent step_starts inside the same
            # turn (multi-step tool loops) are dropped: the adapter
            # doesn't need a per-step marker.
            if not saw_session_started:
                events.append(StreamEvent(type="session_started", raw=obj))
            return events

        if kind == "text":
            part = obj.get("part") or {}
            text = (part.get("text") or "").strip() if isinstance(part, dict) else ""
            if text:
                accumulated_text.append(text)
                events.append(StreamEvent(type="text_delta", text=text, raw=obj))
            return events

        if kind == "tool_use":
            events.append(StreamEvent(type="tool_call", raw=obj))
            return events

        if kind == "error":
            err_obj = obj.get("error") or {}
            if isinstance(err_obj, dict):
                # Drill down the {name, data: {message}} envelope.
                data = err_obj.get("data") or {}
                msg = (
                    (isinstance(data, dict) and data.get("message"))
                    or err_obj.get("message")
                    or err_obj.get("name")
                    or "opencode error"
                )
            else:
                msg = str(err_obj) or "opencode error"
            events.append(StreamEvent(type="error", error=str(msg), raw=obj))
            return events

        # reasoning / step_finish / unknown → dropped.
        return events
