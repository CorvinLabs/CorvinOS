"""ClaudeCodeEngine — wraps `claude -p --output-format stream-json --verbose`.

Phase 1 (ADR-0001): minimal-invasive engine that spawns Claude Code as a
non-interactive subprocess and yields normalised StreamEvents.

Phase 2.1 (ADR-0002): feature-complete `_build_args` static method
(extracted from `adapter.py::_build_claude_args`), plus the full spawn
surface the adapter needs — system prompt, permission_mode,
allowed/disallowed tools, MCP config path, add_dirs, prompt-via-stdin,
continue-session, and `ADAPTER_FAKE_CLAUDE` / `ADAPTER_FAKE_ARGS_DUMP`
fixture support so the existing test suite runs against the engine
unchanged.

Phase 2.3 (done): `inject(text)` for mid-stream `/btw` via stream-json
stdin. ECI `command_manifest` (ADR-0069) exposes `/e:resume` and routes
`/btw` through `CommandDispatcher` with transport=stdin_json.

Stream event shape from `claude -p --output-format stream-json --verbose`:

    {"type":"system","subtype":"init","session_id":"...","model":"...","tools":[...]}
    {"type":"system","subtype":"hook_started","hook_name":"SessionStart:startup",...}
    {"type":"system","subtype":"hook_response","exit_code":0,...}
    {"type":"assistant","message":{"content":[{"type":"text","text":"..."}],"usage":{...}}}
    {"type":"rate_limit_event","rate_limit_info":{...}}
    {"type":"result","subtype":"success","is_error":false,"duration_ms":2367,"result":"pong","usage":{...}}

Mapping (see ADR-0001):
    system.init                              -> session_started
    assistant.message.content[*].text        -> text_delta
    result (subtype=success)                 -> turn_completed
    result (is_error=true | subtype != ok)   -> error
"""

from __future__ import annotations

import json
import os
import shutil
import re
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, IO, Iterator

from . import StreamEvent, parse_jsonl_line

# ── debug logging ───────────────────────────────────────────────────────
# Engine traces (argv, spawn, stderr-tail, naked-error enrichment) go to
# the unified `corvin.engine.claude_code` channel so they sit alongside
# the bridge-adapter records. Best-effort: if the helper isn't reachable
# we degrade silently — engine correctness must not depend on logging.
try:
    import sys as _sys
    _shared = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if _shared not in _sys.path:
        _sys.path.insert(0, _shared)
    from debug_logging import get_logger as _corvin_get_logger  # type: ignore
    _engine_log = _corvin_get_logger("engine.claude_code")
except Exception:  # pragma: no cover
    import logging as _logging  # noqa: PLC0415
    _engine_log = _logging.getLogger("engine.claude_code")
    _engine_log.addHandler(_logging.NullHandler())


CLAUDE_BIN = os.environ.get("CLAUDE_BIN", "claude")


def _configured_claude_bin() -> str:
    """Canonical source for the claude binary name/path, resolved fresh.

    Priority order (highest first):
      1. ``CORVIN_CLAUDE_BIN`` — the canonical pin that ``bridge.sh``
         resolves + exports and that the adapter / guard-tests treat as
         authoritative (see [Engine-Autodetect Stripped-PATH]).
      2. ``CLAUDE_BIN`` — legacy override, kept for back-compat.
      3. ``"claude"`` — bare name, left to PATH / fallback resolution.

    Read fresh (not the import-time ``CLAUDE_BIN`` constant) so a pin
    exported after import — or set by a test before constructing the
    engine — is honoured. Empty-string env values are treated as unset.
    """
    return (
        os.environ.get("CORVIN_CLAUDE_BIN")
        or os.environ.get("CLAUDE_BIN")
        or "claude"
    )

# Fallback locations searched when the configured binary name is bare
# (no path separator) AND ``shutil.which()`` cannot find it on the
# current PATH. This protects against the dominant failure mode
# observed in production: an adapter started under systemd / a stripped
# shell environment whose PATH is missing ``~/.local/bin`` or
# ``/usr/local/bin`` — places where Claude Code typically installs the
# CLI. Each fallback is probed in order; the first executable hit wins.
# Override the list via ``CORVIN_CLAUDE_BIN_FALLBACKS=path1:path2:…``.
_DEFAULT_BIN_FALLBACKS = (
    "~/.local/bin/claude",
    "/usr/local/bin/claude",
    "/usr/bin/claude",
    "/opt/homebrew/bin/claude",
)


def _resolve_claude_bin(name: str) -> str:
    """Return the absolute path to the claude binary, or ``name`` unchanged.

    Resolution order:
      1. If ``name`` is already absolute (or contains a path separator),
         return it as-is — the operator was explicit.
      2. ``shutil.which(name)`` against the current PATH.
      3. Fallback paths (configurable, see ``_DEFAULT_BIN_FALLBACKS``).
      4. Return the original name and let ``Popen`` raise
         ``FileNotFoundError`` — the engine surfaces a typed error so
         the adapter can include the configured PATH in the failure
         message (see ``_format_binary_not_found_error``).
    """
    if not name:
        return name
    if os.sep in name or "/" in name:
        return name
    found = shutil.which(name)
    if found:
        return found
    # Fallbacks only fire when the caller asked for the default name —
    # an operator pin like CLAUDE_BIN=my-fork-claude is honoured as-is
    # so the FileNotFoundError surfaces with the right binary in the
    # error string.
    if name not in ("claude", "claude.exe"):
        return name
    extra = os.environ.get("CORVIN_CLAUDE_BIN_FALLBACKS", "")
    candidates: tuple[str, ...] = _DEFAULT_BIN_FALLBACKS
    if extra:
        candidates = tuple(p for p in extra.split(os.pathsep) if p) + candidates
    for cand in candidates:
        expanded = os.path.expanduser(cand)
        if os.path.isfile(expanded) and os.access(expanded, os.X_OK):
            return expanded
    return name


def _format_binary_not_found_error(binary: str) -> str:
    """Build a diagnostic ``binary not found`` error string for the engine event.

    Includes the current PATH and the fallback locations that were
    probed, so an operator can read the adapter log and immediately see
    *why* the lookup failed.
    """
    path_env = os.environ.get("PATH", "")
    fallbacks = ", ".join(_DEFAULT_BIN_FALLBACKS)
    return (
        f"claude binary not found: {binary!r}; "
        f"PATH={path_env!r}; tried fallbacks: {fallbacks}"
    )

# Stderr tail buffer cap (chars). Large enough to hold a typical
# Anthropic error body, small enough that a runaway log line can't
# consume the engine process's RSS.
_STDERR_TAIL_CHARS = 4096

# Errors that should be enriched with the stderr tail. The bare HTTP
# status case (`error == "400"`) is the load-bearing one — the CLI
# emits the numeric status into `api_error_status` without a body, so
# the adapter has nothing to classify against. Symbolic short tokens
# (`rate_limited`, `overloaded_error`) get the same treatment.
_NAKED_HTTP_RE = re.compile(r"^\s*\d{3}\s*$")
_SHORT_ERROR_TOKENS = frozenset({
    "rate_limited", "overloaded_error", "internal_server_error",
    "service_unavailable", "result-error",
})


class ClaudeCodeEngine:
    """Anthropic Claude Code CLI as a backend-agnostic engine."""

    name = "claude_code"

    capabilities: dict[str, Any] = {
        "mid_stream_inject": True,      # via stream-json stdin (Phase 2.3)
        "hooks": True,                  # ~/.claude/settings.json hooks
        "skills": "skills_tool",        # ECI schema key: native Skill tool API
        "skills_tool": True,            # native Skill tool API
        "mcp": True,
        "stream_json": True,
        "permission_modes": ["default", "plan", "acceptEdits", "bypassPermissions"],
        "add_system_prompt": True,
        "version_flag": "--version",
        "session_pinning": True,        # ADR-0049: supports --resume <session-id>
    }

    # ECI manifest (ADR-0069 M6) — declared lazily to avoid import-time
    # circular dependency with the eci package.
    @staticmethod
    def _build_command_manifest():  # type: ignore[return]
        from eci import EngineCommandManifest, NativeCommandSpec
        return EngineCommandManifest(
            mid_stream_inject="stdin_json",
            cancel="sigterm",
            compact="flag",
            native_commands={
                "resume": NativeCommandSpec(
                    description="Resume a prior session by ID",
                    handler_method="eci_resume_session",
                    usage="<session-id>",
                ),
            },
        )

    @property
    def command_manifest(self):  # type: ignore[return]
        return self._build_command_manifest()

    def eci_resume_session(self, args: str) -> object:
        """ECI handler for /e:resume — stores a session ID to --resume on next spawn."""
        from eci import CommandResult
        session_id = args.strip()
        if not session_id:
            return CommandResult(success=False, message="✗ /e:resume: Session-ID fehlt")
        # Store for the next spawn; the adapter reads _eci_resume_session_id
        # via engine.eci_resume_session_id when building the argv.
        self._eci_resume_session_id = session_id
        return CommandResult(
            success=True,
            message=f"📌 Session-ID gesetzt: {session_id} — wirkt beim nächsten Turn",
        )

    def __init__(self, *, binary: str | None = None) -> None:
        # Resolve the binary once at construction time so subsequent
        # spawns reuse the same absolute path even if the operator's
        # PATH changes between turns (rare, but cheap to harden against).
        self.binary = _resolve_claude_bin(binary or _configured_claude_bin())
        self._proc: subprocess.Popen | None = None
        self._stdin: IO | None = None
        self._stdin_guard = threading.Lock()
        self._stderr_buf: deque[str] = deque()
        self._stderr_buf_chars: int = 0
        self._stderr_guard = threading.Lock()
        self._stderr_thread: threading.Thread | None = None

    # ------------------------------------------------------------------
    # Static argv builder (Phase 2.1 — extracted from adapter.py)
    # ------------------------------------------------------------------

    @staticmethod
    def _build_args(
        prompt: str,
        *,
        binary: str = "claude",
        system: str | None = None,
        mode: str = "unrestricted",
        permission_mode: str | None = None,
        dangerously_skip_permissions: bool | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        model: str | None = None,
        mcp_config_path: str | None = None,
        add_dirs: list[str] | None = None,
        add_dir: str | None = None,
        prompt_via_stdin: bool = False,
        continue_session: bool = False,
        resume_session_id: str | None = None,
        streaming: bool = False,
        extra_args: list[str] | None = None,
    ) -> list[str]:
        """Construct the `claude` argv list.

        This is the single source of truth for argv shape across the
        legacy adapter path and the engine-driven path. Argv ordering
        matches the historical `adapter.py::_build_claude_args` byte-for-
        byte so existing `ADAPTER_FAKE_ARGS_DUMP` snapshot tests keep
        working.

        media-mode (`mode`):
            "unrestricted" — no tool cap; profile fields apply
            "read"         — `--allowedTools Read` (overrides profile)
            "restricted"   — `--disallowedTools *` (overrides profile)

        `prompt_via_stdin=True`: drop the positional prompt arg; caller
        feeds the prompt via stream-json stdin. Must be combined with
        `streaming=True` to add `--input-format stream-json`.

        `continue_session=True`: insert `--continue` between the binary
        and the `-p` flag (matches adapter.py's slice insertion).

        `resume_session_id`: insert `--resume <id>` instead of
        `--continue`. Mutually exclusive with `continue_session`.
        ADR-0049 — worker session pinning.

        `streaming=True`: append `--output-format stream-json --verbose`
        and (when `prompt_via_stdin=True`) `--input-format stream-json`.
        """
        if continue_session and resume_session_id:
            raise ValueError(
                "continue_session and resume_session_id are mutually exclusive"
            )
        args: list[str] = [binary]
        if resume_session_id:
            args += ["--resume", resume_session_id]
        elif continue_session:
            args.append("--continue")
        args.append("-p")
        if not prompt_via_stdin:
            args.append(prompt)

        if system:
            args += ["--append-system-prompt", system]

        # Permission-Mode + Tool-Caps. Media-modes "read" / "restricted"
        # always win because they're set for a concrete image / document
        # processing turn.
        if mode == "read":
            args += ["--allowedTools", "Read"]
        elif mode == "restricted":
            args += ["--disallowedTools", "*"]
        else:
            # Unrestricted: profile may apply.
            if dangerously_skip_permissions is True or (
                dangerously_skip_permissions is None
                and (permission_mode == "bypassPermissions"
                     or permission_mode is None)
            ):
                # Legacy behaviour: full access via dangerously-skip.
                args += ["--dangerously-skip-permissions"]
            elif permission_mode:
                args += ["--permission-mode", permission_mode]
            if allowed_tools:
                args += ["--allowedTools",
                         " ".join(str(t) for t in allowed_tools)]
            if disallowed_tools:
                args += ["--disallowedTools",
                         " ".join(str(t) for t in disallowed_tools)]

        if isinstance(model, str) and model.strip():
            args += ["--model", model.strip()]

        if mcp_config_path:
            args += ["--mcp-config", mcp_config_path]

        if add_dirs:
            for d in add_dirs:
                args += ["--add-dir", str(d)]

        if add_dir:
            args += ["--add-dir", str(add_dir)]

        if extra_args:
            args += list(extra_args)

        if streaming:
            if prompt_via_stdin:
                args += ["--input-format", "stream-json"]
            args += ["--output-format", "stream-json", "--verbose"]

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
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
        # Phase 2.1 additions — full adapter spawn surface
        mode: str = "unrestricted",
        permission_mode: str | None = None,
        dangerously_skip_permissions: bool | None = None,
        allowed_tools: list[str] | None = None,
        disallowed_tools: list[str] | None = None,
        mcp_config_path: str | None = None,
        add_dirs: list[str] | None = None,
        add_dir: str | None = None,
        persona: str | None = None,
        channel: str | None = None,
        chat_key: str | None = None,
        prompt_via_stdin: bool = False,
        continue_session: bool = False,
        resume_session_id: str | None = None,
        streaming: bool = True,
    ) -> Iterator[StreamEvent]:
        if resume_session_id and continue_session:
            raise ValueError(
                "continue_session and resume_session_id are mutually exclusive"
            )
        args = self._build_args(
            prompt,
            binary=self.binary,
            system=system,
            mode=mode,
            permission_mode=permission_mode,
            dangerously_skip_permissions=dangerously_skip_permissions,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            model=model,
            mcp_config_path=mcp_config_path,
            add_dirs=add_dirs,
            add_dir=add_dir,
            prompt_via_stdin=prompt_via_stdin,
            continue_session=continue_session,
            resume_session_id=resume_session_id,
            streaming=streaming,
            extra_args=extra_args,
        )

        spawn_env = os.environ.copy()
        if env:
            spawn_env.update(env)

        # Test fixture: ADAPTER_FAKE_CLAUDE=1 short-circuits the binary
        # spawn and emits a synthetic event sequence so the adapter's
        # streaming path can be exercised without API credits.
        if os.environ.get("ADAPTER_FAKE_CLAUDE") == "1":
            yield from self._fake_stream(
                args=args, prompt=prompt,
                channel=channel, chat_key=chat_key,
            )
            return

        cwd = str(working_dir) if working_dir else None
        start_time = time.time()
        _engine_log.debug(
            "spawn binary=%s cwd=%s channel=%s chat=%s model=%s "
            "permission_mode=%s mode=%s streaming=%s continue=%s resume=%s argc=%d",
            self.binary, cwd, channel, chat_key, model, permission_mode,
            mode, streaming, continue_session,
            bool(resume_session_id), len(args),
        )

        # When prompt_via_stdin is True, we open stdin pipe so the
        # caller (and `inject()`, Phase 2.3) can write user-message
        # JSONL lines into the live stream. text=True keeps the
        # adapter's `inject_btw` (which writes str) wire-compatible.
        stdin_pipe = subprocess.PIPE if prompt_via_stdin else None

        # Windows: npm installs the `claude` CLI as a `claude.cmd` shim, and
        # CreateProcess cannot launch a .cmd/.bat directly (FileNotFoundError /
        # WinError 193) — so it runs through cmd.exe. A LIST `["cmd","/c",...]`
        # is NOT safe: Popen routes it through list2cmdline, cmd.exe re-parses,
        # and a user-prompt metachar (`" & payload`) breaks out (host RCE).
        # windows_shim_command builds a cmd.exe-safe command STRING instead; on
        # POSIX / non-.cmd it returns `args` unchanged (snapshots intact).
        from ._win_shim import windows_shim_command
        spawn_args = windows_shim_command(args)

        try:
            self._proc = subprocess.Popen(
                spawn_args,
                stdin=stdin_pipe,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=spawn_env,
                start_new_session=True,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError:
            yield StreamEvent(
                type="error",
                error=_format_binary_not_found_error(self.binary),
            )
            return

        # Drain stderr concurrently so the pipe buffer can never fill
        # (4096-byte default on Linux) and stall the subprocess. The
        # tail is consulted when the CLI surfaces a naked HTTP-status
        # error — see `_enrich_naked_error`.
        self._start_stderr_drain()

        # Layer 13 stream-json input: feed the initial user message
        # before yielding any events so a racing `inject()` never lands
        # ahead of the original prompt.
        if prompt_via_stdin and self._proc.stdin is not None:
            with self._stdin_guard:
                self._stdin = self._proc.stdin
                try:
                    init_msg = {
                        "type": "user",
                        "message": {"role": "user", "content": prompt},
                    }
                    self._proc.stdin.write(
                        json.dumps(init_msg, ensure_ascii=False) + "\n"
                    )
                    self._proc.stdin.flush()
                except (BrokenPipeError, OSError) as e:
                    yield StreamEvent(
                        type="error",
                        error=f"claude stdin unreachable: {e}",
                    )
                    self._cleanup_proc()
                    return

        try:
            yield from self._iter_stream(start_time, timeout, prompt_via_stdin)
        finally:
            self._cleanup_proc()

    def inject(self, text: str) -> bool:
        """Write a user-message JSONL line into the live process stdin.

        Phase 2.3 entry-point for `/btw` mid-stream injection. Returns
        True on successful write+flush, False when no live stdin is
        registered (the streaming loop closed the pipe after the first
        result event, or no spawn is running).

        Thread-safe: holds `_stdin_guard` for the whole write+flush so
        the streaming loop's stdin-close on result can't race the
        injection.
        """
        text = (text or "").strip()
        if not text:
            return False
        with self._stdin_guard:
            stdin = self._stdin
            if stdin is None:
                return False
            try:
                payload = {
                    "type": "user",
                    "message": {"role": "user", "content": text},
                }
                line = json.dumps(payload, ensure_ascii=False) + "\n"
                # spawn() opens the pipe with text=True — write str.
                stdin.write(line)
                stdin.flush()
                return True
            except (BrokenPipeError, ValueError, OSError):
                return False

    def close_stdin(self) -> None:
        """Close the stdin pipe so claude EOFs cleanly. Idempotent.

        The streaming loop calls this on the first `result` event so a
        post-result `inject()` falls back to the queue path instead of
        writing into a pipe whose owner is about to exit.
        """
        with self._stdin_guard:
            stdin = self._stdin
            self._stdin = None
        if stdin is not None:
            try:
                if not stdin.closed:
                    stdin.close()
            except Exception:
                pass

    def _cleanup_proc(self) -> None:
        self.close_stdin()
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
        # Stderr-drain thread exits naturally on EOF after proc dies;
        # join with a small deadline so it can't outlive the caller.
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
    # Stderr tail (used to enrich naked HTTP-status errors)
    # ------------------------------------------------------------------

    def _start_stderr_drain(self) -> None:
        """Spawn a daemon thread that drains proc.stderr into a ring buffer.

        Keeps at most `_STDERR_TAIL_CHARS` of the most recent stderr
        output. Best-effort: a read error silently terminates the
        thread; the engine still functions, just without stderr tail
        for error enrichment.
        """
        with self._stderr_guard:
            self._stderr_buf.clear()
            self._stderr_buf_chars = 0
        proc = self._proc
        if proc is None or proc.stderr is None:
            return
        t = threading.Thread(
            target=self._stderr_drain_loop,
            name="claude-stderr-drain",
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
        """Return the last `max_chars` characters of stderr seen so far.

        Public so callers (the streaming loop, tests) can enrich error
        diagnostics. Best-effort: returns "" when nothing has been
        captured yet.
        """
        with self._stderr_guard:
            joined = "".join(self._stderr_buf)
        joined = joined.strip()
        if len(joined) > max_chars:
            joined = joined[-max_chars:]
        return joined

    def _enrich_naked_error(self, event: StreamEvent) -> StreamEvent:
        """Append stderr tail to error events that carry only a status.

        Anthropic's CLI surfaces transient API failures as either a
        bare HTTP status ("400") or a short symbolic token
        ("rate_limited"). Without the body, the adapter cannot
        distinguish a context-overflow from a tool-use mismatch from
        a rate-limit. Adding the tail keeps `engine streaming returned
        error: ...` actionable in the journal.
        """
        if event.type != "error":
            return event
        err = (event.error or "").strip()
        is_naked = bool(
            _NAKED_HTTP_RE.match(err)
            or err.lower() in _SHORT_ERROR_TOKENS
            or len(err) <= 4
        )
        if not is_naked:
            return event
        # Brief settle window — stderr may lag stdout by a few ms when
        # both pipes drain concurrently.
        time.sleep(0.05)
        tail = self.stderr_tail(max_chars=500)
        if not tail:
            _engine_log.warning(
                "naked error %r — no stderr tail captured; raw=%.800r",
                err, event.raw,
            )
            return event
        _engine_log.warning(
            "naked error enriched err=%r tail_chars=%d", err, len(tail)
        )
        return StreamEvent(
            type="error",
            error=f"{err}: {tail}",
            raw=event.raw,
            usage=event.usage,
        )

    def cancel(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.terminate()
            except Exception:
                pass

    @property
    def proc(self) -> subprocess.Popen | None:
        """The live subprocess, or None when no spawn is running.

        Exposed read-only so adapter consumers can register the proc
        with the cancel registry / process-table without owning the
        stdin lifecycle. The engine still owns spawn / cleanup.
        """
        return self._proc

    # ------------------------------------------------------------------
    # Internal: stream parser
    # ------------------------------------------------------------------

    def _iter_stream(
        self, start_time: float, timeout: float,
        prompt_via_stdin: bool,
    ) -> Iterator[StreamEvent]:
        proc = self._proc
        assert proc is not None and proc.stdout is not None

        completed = False
        terminal = False  # error event = stop the iterator immediately
        for raw_line in proc.stdout:
            if time.time() - start_time > timeout:
                yield StreamEvent(type="error", error="claude stream timeout")
                return

            # text=True spawn → str; parse_jsonl_line handles both
            # bytes and str transparently.
            obj = parse_jsonl_line(raw_line)
            if obj is None:
                continue

            for event in self._normalise_all(obj):
                if event.type == "error":
                    event = self._enrich_naked_error(event)
                yield event
                if event.type == "turn_completed":
                    # Stream-json input keeps stdin open across turns
                    # so mid-stream `/btw` injections can produce
                    # further turn_completed events. Close stdin on
                    # the FIRST completion (idempotent guard inside
                    # close_stdin) so claude EOFs once any pending
                    # buffered output has drained — but do NOT break.
                    if prompt_via_stdin and self._stdin is not None:
                        self.close_stdin()
                    completed = True
                elif event.type == "error":
                    if prompt_via_stdin and self._stdin is not None:
                        self.close_stdin()
                    completed = True
                    terminal = True
                    break
            if terminal:
                break

        if not completed:
            # The drain thread is still consuming proc.stderr; ask it
            # for the buffered tail instead of racing on a direct read.
            stderr_text = self.stderr_tail(max_chars=1000)
            err = stderr_text or (
                f"claude exited without result (exit_code={proc.poll()})"
            )
            yield StreamEvent(type="error", error=err)

    @classmethod
    def _normalise_all(cls, obj: dict[str, Any]) -> list[StreamEvent]:
        """Map one raw JSONL object to a list of normalised events.

        An `assistant` message can contain BOTH text blocks and
        tool_use blocks; each becomes its own event so consumers
        (tool-use status callbacks, voice TTS) get a clean per-class
        signal. Order is preserved: text before tool_use within the
        same message.
        """
        kind = obj.get("type")
        if kind == "system" and obj.get("subtype") == "init":
            return [StreamEvent(type="session_started", raw=obj)]

        if kind == "assistant":
            msg = obj.get("message") or {}
            content = msg.get("content") or []
            text_pieces: list[str] = []
            tool_blocks: list[dict[str, Any]] = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                btype = block.get("type")
                if btype == "text":
                    text_pieces.append(block.get("text", ""))
                elif btype == "tool_use":
                    tool_blocks.append(block)
            events: list[StreamEvent] = []
            text = "".join(text_pieces)
            if text:
                events.append(StreamEvent(type="text_delta", text=text, raw=obj))
            if tool_blocks:
                # Single tool_call event carries the full assistant
                # message so the consumer can iterate over every
                # tool_use block. `raw["message"]["content"]` retains
                # ordering and inputs.
                events.append(StreamEvent(type="tool_call", raw=obj))
            return events

        if kind == "result":
            is_error = bool(obj.get("is_error"))
            subtype = obj.get("subtype")
            usage = obj.get("usage") or {}
            text = obj.get("result", "") or ""
            if is_error or subtype not in ("success", None):
                # Precedence: machine-readable api_error_status wins
                # (rate_limited, etc.). Fall back to the human-readable
                # `result` text — claude surfaces session-corruption
                # and similar diagnostics there. Subtype is the last
                # resort for the otherwise-empty case.
                err = (
                    obj.get("api_error_status")
                    or text
                    or subtype
                    or "result-error"
                )
                return [StreamEvent(type="error", error=str(err),
                                    raw=obj, usage=usage)]
            return [StreamEvent(
                type="turn_completed", text=text, usage=usage, raw=obj,
            )]

        # Hooks, rate-limit-events et al — surface as raw-only events
        # so a future adapter consumer can inspect them without the
        # engine spec growing a flag for every case.
        return []

    # Back-compat: the original single-event normaliser. Keeps the
    # existing test surface stable. Returns the FIRST event of
    # `_normalise_all`, or None when the message doesn't normalise.
    @classmethod
    def _normalise(cls, obj: dict[str, Any]) -> StreamEvent | None:
        events = cls._normalise_all(obj)
        return events[0] if events else None

    # ------------------------------------------------------------------
    # Test-fixture: ADAPTER_FAKE_CLAUDE=1
    # ------------------------------------------------------------------

    @staticmethod
    def _fake_stream(
        *, args: list[str], prompt: str,
        channel: str | None, chat_key: str | None,
    ) -> Iterator[StreamEvent]:
        try:
            delay = float(os.environ.get("ADAPTER_FAKE_DELAY", "0.5"))
        except ValueError:
            delay = 0.5

        dump = os.environ.get("ADAPTER_FAKE_ARGS_DUMP")
        if dump:
            try:
                with open(dump, "a") as fh:
                    fh.write(json.dumps({
                        "channel": channel, "chat_key": chat_key,
                        "args": args,
                        "streaming": True,
                        "engine": "claude_code",
                    }, ensure_ascii=False) + "\n")
            except OSError:
                pass

        time.sleep(delay)

        ch = channel or "?"
        ck = chat_key or "?"
        fake_text = f"[fake-stream] {ch}:{ck} :: {prompt[:60]}"

        yield StreamEvent(
            type="session_started",
            raw={"type": "system", "subtype": "init", "session_id": "fake"},
        )
        yield StreamEvent(
            type="turn_completed",
            text=fake_text,
            usage={"input_tokens": 0, "output_tokens": 0},
            raw={"type": "result", "subtype": "success",
                 "is_error": False, "result": fake_text},
        )
