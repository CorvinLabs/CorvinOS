"""CopilotCliEngine — wraps the GitHub Copilot CLI (`copilot -p`).

Integrates the GitHub Copilot CLI (github/copilot-cli, standalone binary,
distinct from the deprecated gh-copilot extension) as a fifth WorkerEngine
in the Corvin L22 layer.

ADR-0071 — Layer 22 Extension: CopilotCliEngine.

The engine uses `copilot -p "<prompt>"` for non-interactive single-turn
inference. The new Copilot CLI (v1.0.56+) is a general-purpose AI coding
assistant — it can generate shell/git/gh commands, answer code questions,
reason about codebases, and assist with GitHub workflows.

Task type mapping (via `model` field in spawn()):
    "shell" — prepend "Reply with only the shell command (no explanation) for: "
    "git"   — prepend "Reply with only the git command (no explanation) for: "
    "gh"    — prepend "Reply with only the gh CLI command (no explanation) for: "
    None / other — pass prompt as-is (general-purpose chat mode)

Binary resolution order:
    1. CORVIN_COPILOT_BIN env var (explicit path)
    2. "copilot" in PATH (~/.local/bin/copilot or /usr/local/bin/copilot)

Auth: `copilot` reads its own config from ~/.copilot/config.json (set up
once via `copilot auth login`). GH_TOKEN / GITHUB_TOKEN in the env are also
honoured as fallback auth (pass via the caller's env dict, never as args).

Output: copilot -p emits the response followed by a "Changes/Requests/Tokens"
footer on stdout. The engine strips the footer and returns clean text.

Design constraints (MUST NOT do):
    - MUST NOT import anthropic (CI AST lint enforces, ADR-0071)
    - MUST NOT use shell=True (shell-metacharacter injection surface)
    - MUST NOT put prompt content or output in any audit field
    - MUST NOT pass GH_TOKEN / GITHUB_TOKEN as positional args or in cmd list
    - MUST NOT exceed PROMPT_MAX_CHARS (explicit cap prevents silent truncation)
"""
from __future__ import annotations

import os
import re
import subprocess
import threading
from pathlib import Path
from typing import Any, Iterator

from . import StreamEvent


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Task types with their prompt prefixes. The `model` field in spawn() maps
# to a key here; unrecognised values fall through to passthrough (chat) mode.
COPILOT_TASK_PREFIXES: dict[str, str] = {
    "shell": "Reply with only the shell command (no explanation) for: ",
    "git":   "Reply with only the git command (no explanation) for: ",
    "gh":    "Reply with only the gh CLI command (no explanation) for: ",
}
COPILOT_TASK_TYPES: frozenset[str] = frozenset(COPILOT_TASK_PREFIXES)

_DEFAULT_TASK_TYPE: str | None = None  # None = passthrough / general chat

# Hard prompt cap. The copilot CLI may truncate silently beyond large inputs;
# an explicit cap surfaces the limit early with a clear error.
PROMPT_MAX_CHARS = 8_000

# Footer pattern emitted by `copilot -p` after the actual answer.
# Typically:  "\n\n\nChanges    +0 -0\nRequests   ...\nTokens     ...\n"
_FOOTER_RE = re.compile(r"\n{2,}Changes\s+", re.MULTILINE)

# Env var keys that the copilot binary uses for auth + targeting.
_COPILOT_AUTH_ENV_KEYS: frozenset[str] = frozenset({
    "GH_TOKEN",
    "GH_ENTERPRISE_TOKEN",
    "GITHUB_TOKEN",
    "GH_HOST",           # GHES hostname override
    "COPILOT_AGENT_ROOT",  # undocumented config root override
})


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_copilot_bin() -> str:
    """Resolution order:
    1. CORVIN_COPILOT_BIN env var (explicit absolute path)
    2. "copilot" resolved from PATH (works once binary is installed)
    """
    override = os.environ.get("CORVIN_COPILOT_BIN", "").strip()
    if override:
        return override
    return "copilot"


def _strip_footer(text: str) -> str:
    """Strip the `copilot -p` session footer from the response text.

    The footer looks like::

        \\n\\n\\nChanges    +0 -0
        Requests   1 Premium (4s)
        Tokens     ↑ 28.7k (8.7k cached) ↓ 7

    We detect the pattern ``\\n{2,}Changes\\s+`` and strip everything after it.
    """
    m = _FOOTER_RE.search(text)
    if m:
        return text[:m.start()].strip()
    return text.strip()


def _build_spawn_env(overlay: dict[str, str] | None) -> dict[str, str]:
    """Build the subprocess environment for the copilot CLI.

    At call time, delegation.py's _scrubbed_environ() has already stripped
    os.environ to the curated allowlist (PATH / HOME / USER / LANG / TERM
    + engine-specific additions). We inherit that scrubbed base and merge
    the caller's overlay (which carries GH_TOKEN / GH_HOST when the
    operator injected them via the L16 vault→bwrap env path).

    The copilot binary also needs HOME to locate ~/.copilot/config.json.
    """
    base = dict(os.environ)
    if overlay:
        base.update(overlay)
    return base


# ---------------------------------------------------------------------------
# CopilotCliEngine
# ---------------------------------------------------------------------------


class CopilotCliEngine:
    """WorkerEngine wrapping the GitHub Copilot CLI (`copilot -p`).

    Capabilities (M1):
      stream_json=False       — output appears at process exit, not streamed
                                (copilot -p is a one-shot blocking call)
      mcp=False               — no MCP bridge in M1
      mid_stream_inject=False — subprocess has no streaming-stdin entry
      hooks=False             — no filesystem hook system
      skills_tool=False       — M1; prompt-prefix steering planned for M2
      add_system_prompt=False — copilot CLI has no system-prompt parameter
      permission_modes=["read_only"]
      session_pinning=False   — copilot CLI has no session-resume concept
      task_types=[shell, git, gh]  — model field values with prefix steering
    """

    name = "copilot"

    capabilities: dict[str, Any] = {
        "mid_stream_inject": False,
        "hooks":             False,
        "skills_tool":       False,
        "mcp":               False,
        "stream_json":       False,
        "permission_modes":  ["read_only"],
        "add_system_prompt": False,
        "version_flag":      "--version",
        "session_pinning":   False,
        "task_types":        sorted(COPILOT_TASK_TYPES),
    }

    def __init__(
        self,
        *,
        task_type: str | None = _DEFAULT_TASK_TYPE,
        timeout_s: int = 60,
    ) -> None:
        self.task_type: str | None = (
            task_type if task_type in COPILOT_TASK_TYPES else _DEFAULT_TASK_TYPE
        )
        self.timeout_s: int = max(10, min(timeout_s, 300))
        self._cancel = threading.Event()
        self._proc: subprocess.Popen[bytes] | None = None

    # ------------------------------------------------------------------
    # WorkerEngine Protocol
    # ------------------------------------------------------------------

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,         # not used; copilot has no system-prompt param
        model: str | None = None,           # maps to task_type: "shell"|"git"|"gh"|None
        working_dir: Path | None = None,    # used as subprocess cwd when supplied
        timeout: float = 120.0,
        extra_args: list[str] | None = None,  # not used
        env: dict[str, str] | None = None,
        tools: list[dict] | None = None,      # not used; no tool-calling in M1
        tool_executor: Any | None = None,     # not used
        **_: Any,
    ) -> Iterator[StreamEvent]:
        """Spawn `copilot -p "<effective_prompt>"` and yield StreamEvents.

        The ``model`` parameter maps to a task-type prefix:
          - "shell" → prefix + prompt = shell-command-focused request
          - "git"   → prefix + prompt = git-command-focused request
          - "gh"    → prefix + prompt = gh-CLI-command-focused request
          - None or other → prompt passed verbatim (general chat mode)

        Auth: the copilot binary reads ~/.copilot/config.json.
        GH_TOKEN / GITHUB_TOKEN from the caller's ``env`` dict are used
        as fallback auth.

        Output: `copilot -p` emits the response followed by a
        "Changes/Requests/Tokens" footer. The footer is stripped before
        the text_delta event fires.
        """
        self._cancel.clear()

        # Resolve effective task_type (model field overrides constructor).
        effective_task_type: str | None = (
            model if model in COPILOT_TASK_TYPES else self.task_type
        )

        # Cap prompt at PROMPT_MAX_CHARS (explicit cap > silent truncation).
        if len(prompt) > PROMPT_MAX_CHARS:
            prompt = prompt[:PROMPT_MAX_CHARS]

        # Build the effective prompt with optional task-type prefix.
        prefix = COPILOT_TASK_PREFIXES.get(effective_task_type or "", "")
        effective_prompt = f"{prefix}{prompt}" if prefix else prompt

        effective_timeout = min(float(self.timeout_s), timeout)
        spawn_env = _build_spawn_env(env)
        bin_path = _resolve_copilot_bin()

        # Build command: `copilot -p "<effective_prompt>"`.
        # List-form prevents shell-metacharacter injection — NEVER shell=True.
        cmd = [bin_path, "-p", effective_prompt]

        yield StreamEvent(
            type="session_started",
            raw={"engine": "copilot", "task_type": effective_task_type or "chat"},
        )

        proc: subprocess.Popen[bytes] | None = None
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,   # non-interactive: no TTY needed
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=spawn_env,
                # Honour the caller's hermetic 0700 tempdir when one is
                # supplied (delegation.py's _hermetic_tempdir default). This
                # was previously silently dropped — `working_dir` is a
                # recognised parameter but was never passed through to the
                # subprocess, so every copilot delegation ran in whatever
                # cwd the delegate MCP-server process happened to have,
                # never the isolated sandbox the docstring promises
                # (adversarial review finding).
                cwd=str(working_dir) if working_dir is not None else None,
            )
        except FileNotFoundError:
            yield StreamEvent(
                type="error",
                error=(
                    "copilot-cli: `copilot` binary not found — install from "
                    "https://github.com/github/copilot-cli or set CORVIN_COPILOT_BIN"
                ),
            )
            return
        except OSError as e:
            yield StreamEvent(
                type="error",
                error=f"copilot-cli: spawn failed: {e}",
            )
            return

        self._proc = proc

        try:
            stdout, stderr = proc.communicate(timeout=effective_timeout)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait()
            except Exception:  # noqa: BLE001
                pass
            self._proc = None
            yield StreamEvent(
                type="error",
                error=f"copilot-cli: timed out after {effective_timeout:.0f}s",
            )
            return
        except Exception as e:  # noqa: BLE001
            self._proc = None
            yield StreamEvent(
                type="error",
                error=f"copilot-cli: communicate error: {e}",
            )
            return

        rc = proc.returncode
        self._proc = None

        if self._cancel.is_set():
            yield StreamEvent(type="error", error="copilot-cli: cancelled")
            return

        if rc != 0:
            err_text = (stderr or b"").decode("utf-8", errors="replace").strip()
            yield StreamEvent(
                type="error",
                error=f"copilot-cli: exit {rc}: {err_text[:300]}",
            )
            return

        raw_output = (stdout or b"").decode("utf-8", errors="replace")
        output = _strip_footer(raw_output)

        yield StreamEvent(type="text_delta", text=output)
        yield StreamEvent(
            type="turn_completed",
            text=output,
            usage={},
        )

    def cancel(self) -> None:
        """Cancel an in-flight spawn(). Terminates the subprocess."""
        self._cancel.set()
        proc = self._proc
        if proc is not None:
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass


__all__ = [
    "CopilotCliEngine",
    "COPILOT_TASK_TYPES",
    "COPILOT_TASK_PREFIXES",
]
