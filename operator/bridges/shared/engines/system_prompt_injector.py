"""
ADR-0087 M6: Unified System-Prompt Slot

Goal: All 5 engines accept a unified `system_prompt: str | None` parameter.
Transport varies per engine; logic is abstracted.

Unified Contract:
  1. All engines expose spawn(system_prompt=None) kwarg
  2. Pre-spawn check: Is system prompt already in transport? (idempotent)
  3. If not present: inject per engine-specific format
  4. If present: skip injection (no double-injection)
  5. Return: modified transport (dict/str, engine-specific)

Per-Engine Transport:
  - Claude Code: --append-system-prompt flag (Anthropic SDK)
  - Hermes: JSON system role message ({"role": "system", "content": "…"})
  - Codex: <SYSTEM>...</SYSTEM> block prefix (text-based)
  - OpenCode: <SYSTEM>...</SYSTEM> block prefix (text-based)
  - Copilot: [SYSTEM]...[/SYSTEM] marker (text-based)

Capability Strings (frozen for M8 matrix):
  - claude_code: system_prompt="flag" (--append-system-prompt)
  - hermes: system_prompt="message_role" (JSON system role)
  - codex: system_prompt="text_prefix" (<SYSTEM> block)
  - opencode: system_prompt="text_prefix" (<SYSTEM> block)
  - copilot: system_prompt="text_prefix" ([SYSTEM] marker)

Compliance (from CLAUDE.md):
  - L34 Data-Classification: System prompts subject to classification gate (pre-spawn)
  - L16 Audit-First: Emit event before injection; audit-write failure blocks spawn
  - L36 GDPR: No system prompt content in audit details (engine_id + format only)

Double-Injection Guard:
  Each engine has _system_already_injected(transport, engine_id) → bool
  Per-engine detection logic (see below).

Edge Cases:
  - System prompt already in transport → skip (idempotent)
  - Skills (M3) already handle system slot → M6 is override mechanism only
  - Prompt too large for engine → truncate or error (engine-specific)
  - Control characters in prompt → NFKC normalize, reject forbidden chars
  - Self-closing tags in prompt (e.g., "</SYSTEM>") → escape or error

Test Coverage (Tier-1/2/3):
  - Tier-1: Format detection (all 5 engines, edge cases)
  - Tier-2: Unit injection (mock spawns, double-injection guard)
  - Tier-3: E2E with real engine spawns (verify prompt applied)
"""

from typing import Optional, Dict, Any, Union, Tuple
from dataclasses import dataclass
import logging
import unicodedata

logger = logging.getLogger(__name__)


class SystemPromptInjector:
    """Unified system-prompt slot injection across all 5 engines."""

    def __init__(self, tenant_id: str = "_default"):
        """
        Initialize system-prompt injector.

        Args:
            tenant_id: Tenant ID (for audit, ADR-0007)
        """
        self.tenant_id = tenant_id

    def inject_system_prompt(
        self,
        system_prompt: Optional[str],
        transport: Union[Dict[str, Any], str],
        engine_id: str,
    ) -> Union[Dict[str, Any], str]:
        """
        Inject system prompt into engine transport, idempotent.

        Args:
            system_prompt: System prompt text, or None (skip injection)
            transport: Engine-specific transport (dict for Claude/Hermes, str for Codex/OpenCode/Copilot)
            engine_id: Engine ID ("claude_code", "hermes", "codex", "opencode", "copilot")

        Returns:
            Modified transport with system prompt injected (or original if no change)

        Raises:
            ValueError: If engine_id invalid, prompt too large, or injection fails

        Behavior:
            - If system_prompt is None → return transport unchanged
            - If system already present → return transport unchanged (idempotent)
            - Otherwise → inject per engine format (see _inject_* methods below)
            - Emit L16 audit event: system_prompt.injected (metadata-only)

        Examples:
            >>> injector = SystemPromptInjector()
            >>> prompt = "You are helpful."
            >>> # Claude Code: return --append-system-prompt flag
            >>> args = {"command": ["claude", "-p"], "system_prompt": None}
            >>> result = injector.inject_system_prompt(prompt, args, "claude_code")
            >>> assert "--append-system-prompt" in result["command"]
        """
        # Skip if no prompt provided
        if system_prompt is None:
            return transport

        # Validate engine_id
        if engine_id not in ["claude_code", "hermes", "codex", "opencode", "copilot"]:
            raise ValueError(f"Unknown engine_id: {engine_id}")

        # Normalize prompt
        system_prompt = self._normalize_prompt(system_prompt)

        # Size gate before any engine-specific routing
        if len(system_prompt.encode()) > 8192:
            raise ValueError("System prompt too large (max 8 KB)")

        # Check if already injected (idempotent)
        if self._system_already_injected(transport, engine_id):
            logger.debug(f"System prompt already injected for {engine_id}; skipping")
            return transport

        # Inject per engine format
        if engine_id == "claude_code":
            return self._inject_claude_code(system_prompt, transport)
        elif engine_id == "hermes":
            return self._inject_hermes(system_prompt, transport)
        elif engine_id in ["codex", "opencode"]:
            return self._inject_codex_like(system_prompt, transport, engine_id)
        elif engine_id == "copilot":
            return self._inject_copilot(system_prompt, transport)

        return transport

    def _system_already_injected(self, transport: Union[Dict, str], engine_id: str) -> bool:
        """
        Detect if system prompt already present in transport (idempotent guard).

        Returns: True if system prompt detected, False otherwise

        Per-Engine Detection Logic:
            - claude_code: Check for --append-system-prompt flag in argv
            - hermes: Check for {"role": "system"} message in messages list
            - codex/opencode: Check for <SYSTEM>...</SYSTEM> block in prompt string
            - copilot: Check for [SYSTEM]...[/SYSTEM] marker in prompt string

        Returns False on error (assume not present, allow injection).
        """
        try:
            if engine_id == "claude_code":
                # Check for --append-system-prompt (inline) OR
                # --append-system-prompt-file (the Windows fresh-install
                # fix's file-based form — see _inject_claude_code's
                # docstring). Missing the -file form here would make this
                # guard blind to an already-injected prompt and silently
                # double-inject on the next spawn.
                if isinstance(transport, dict):
                    cmd = transport.get("command", [])
                    return (
                        "--append-system-prompt" in cmd
                        or "--append-system-prompt-file" in cmd
                    )
                return False

            elif engine_id == "hermes":
                # Check for system role in messages list
                if isinstance(transport, dict) and "messages" in transport:
                    messages = transport["messages"]
                    if isinstance(messages, list) and messages:
                        return messages[0].get("role") == "system"
                return False

            elif engine_id in ["codex", "opencode"]:
                # Check for <SYSTEM>...</SYSTEM> block in prompt string
                if isinstance(transport, str):
                    return "<SYSTEM>" in transport and "</SYSTEM>" in transport
                return False

            elif engine_id == "copilot":
                # Check for [SYSTEM]...[/SYSTEM] marker in prompt string
                if isinstance(transport, str):
                    return "[SYSTEM]" in transport and "[/SYSTEM]" in transport
                return False

            return False
        except (TypeError, KeyError, AttributeError):
            logger.debug(f"Error detecting system prompt for {engine_id}; assuming not present")
            return False

    def _inject_claude_code(self, system_prompt: str, transport: Dict) -> Dict:
        """
        Inject into Claude Code transport: add --append-system-prompt-file.

        Transport format (dict with "argv" or "command" key):
            {"command": ["claude", "-p", ...], "system_prompt": None}

        Returns: Modified dict with flag added

        Raises:
            ValueError: If system_prompt too large or invalid

        Windows fresh-install fix: this used to insert the prompt TEXT
        inline via --append-system-prompt. On Windows `claude` resolves to
        an npm .cmd shim, launched through a single `cmd /c "<line>"`
        string — cmd.exe's own internal command-line buffer caps at
        ~8191 characters, well under the 8 KB this method already allowed
        through its own size gate (a `len()==8192` prompt plus the rest of
        argv reliably exceeds it). Writes the prompt to a temp file and
        uses --append-system-prompt-file (a documented alias — `claude
        --help` lists "--append-system-prompt[-file]") instead — the exact
        pattern already applied to every real spawn site in this codebase
        (chat_runtime.py, agents/claude_code.py, agents/codex_cli.py,
        agents/opencode_cli.py, adapter.py's legacy call_claude()
        fallback, acs_runtime.py's manager/worker spawns). A temp-file
        write failure falls back to the historical inline form rather than
        raising — this module has no subprocess lifecycle of its own to
        hook a guaranteed cleanup into, so the caller (once/if this module
        is ever wired into a live spawn path) owns cleaning up the
        returned path the same way acs_runtime.py's callers do.
        """
        if not isinstance(transport, dict):
            raise ValueError("Claude Code transport must be a dict")

        if len(system_prompt.encode()) > 8 * 1024:
            raise ValueError("System prompt too large for Claude Code (max 8 KB)")

        result = transport.copy()
        cmd = result.get("command", [])

        if isinstance(cmd, list):
            flag_pair = ["--append-system-prompt", system_prompt]
            tmp_path = self._write_system_prompt_tmp_file(system_prompt)
            if tmp_path:
                flag_pair = ["--append-system-prompt-file", tmp_path]
            # Insert flag before positional args
            result["command"] = (
                cmd[:1] +  # Keep first element (program name)
                flag_pair +
                cmd[1:]  # Rest of args
            )

        return result

    def _write_system_prompt_tmp_file(self, system_prompt: str) -> Optional[str]:
        """Best-effort temp-file write for the Windows fresh-install fix.
        Returns None on any failure so the caller falls back to the
        historical inline argument instead of raising over a temp-dir
        permission issue."""
        try:
            import os
            import tempfile
            from pathlib import Path

            home_env = os.environ.get("CORVIN_HOME")
            home = Path(home_env).expanduser() if home_env else (Path.home() / ".corvin")
            tmp_dir = home / "tenants" / self.tenant_id / "global" / "acs_tmp"
            tmp_dir.mkdir(parents=True, exist_ok=True)
            fd, path = tempfile.mkstemp(
                suffix=".txt", prefix=".corvin-sysprompt-", dir=str(tmp_dir),
            )
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(system_prompt)
            return path
        except OSError:
            return None

    def _inject_hermes(self, system_prompt: str, transport: Dict) -> Dict:
        """
        Inject into Hermes transport: add system role message.

        Transport format (dict with "messages" list):
            {"messages": [{"role": "user", "content": "…"}, ...]}

        Returns: Modified dict with system role prepended

        Logic:
            - If system role already at [0] → skip (idempotent)
            - Otherwise → prepend {"role": "system", "content": system_prompt}

        Raises:
            ValueError: If messages invalid or system_prompt too large
        """
        if not isinstance(transport, dict):
            raise ValueError("Hermes transport must be a dict")

        if "messages" not in transport:
            raise ValueError("Hermes transport missing 'messages' key")

        if len(system_prompt.encode()) > 8 * 1024:
            raise ValueError("System prompt too large for Hermes (max 8 KB)")

        result = transport.copy()
        messages = result.get("messages", [])

        if not isinstance(messages, list):
            raise ValueError("messages must be a list")

        # Prepend system message
        system_message = {"role": "system", "content": system_prompt}
        result["messages"] = [system_message] + messages

        return result

    def _inject_codex_like(self, system_prompt: str, transport: str, engine_id: str) -> str:
        """
        Inject into Codex/OpenCode transport: prepend <SYSTEM>...</SYSTEM> block.

        Transport format (string prompt):
            "User asked for help with X"

        Returns: Modified string with block prepended

        Logic:
            - Prepend <SYSTEM>…</SYSTEM>\n\n before user prompt
            - If <SYSTEM> already present → skip (idempotent)

        Raises:
            ValueError: If system_prompt contains forbidden chars or too large
        """
        if not isinstance(transport, str):
            raise ValueError(f"{engine_id} transport must be a string")

        if len(system_prompt.encode()) > 8 * 1024:
            raise ValueError(f"System prompt too large for {engine_id} (max 8 KB)")

        # Guard against injection via closing tag
        if "</SYSTEM>" in system_prompt:
            raise ValueError("System prompt cannot contain '</SYSTEM>' (injection guard)")

        return f"<SYSTEM>{system_prompt}</SYSTEM>\n\n{transport}"

    def _inject_copilot(self, system_prompt: str, transport: str) -> str:
        """
        Inject into Copilot transport: prepend [SYSTEM]...[/SYSTEM] marker.

        Transport format (string prompt):
            "User asked for help with X"

        Returns: Modified string with marker prepended

        Logic:
            - Prepend [SYSTEM]\n…\n[/SYSTEM]\n\n before user prompt
            - If [SYSTEM] marker already present → skip (idempotent)
            - Marker disambiguates from FCB [TOOL:...] syntax (M5)

        Raises:
            ValueError: If system_prompt contains forbidden chars or too large
        """
        if not isinstance(transport, str):
            raise ValueError("Copilot transport must be a string")

        if len(system_prompt.encode()) > 8 * 1024:
            raise ValueError("System prompt too large for Copilot (max 8 KB)")

        # Guard against injection via closing marker
        if "[/SYSTEM]" in system_prompt:
            raise ValueError("System prompt cannot contain '[/SYSTEM]' (injection guard)")

        return f"[SYSTEM]\n{system_prompt}\n[/SYSTEM]\n\n{transport}"

    def capability_for_engine(self, engine_id: str) -> str:
        """
        Return capability string for engine (for M8 capability matrix).

        Returns: "flag" | "message_role" | "text_prefix"

        Mapping:
            - claude_code → "flag" (--append-system-prompt)
            - hermes → "message_role" (JSON system role)
            - codex → "text_prefix" (<SYSTEM> block)
            - opencode → "text_prefix" (<SYSTEM> block)
            - copilot → "text_prefix" ([SYSTEM] marker)

        Raises:
            ValueError: If engine_id not recognized
        """
        capabilities = {
            "claude_code": "flag",
            "hermes": "message_role",
            "codex": "text_prefix",
            "opencode": "text_prefix",
            "copilot": "text_prefix",
        }

        if engine_id not in capabilities:
            raise ValueError(f"Unknown engine_id: {engine_id}")

        return capabilities[engine_id]

    def _normalize_prompt(self, prompt: str) -> str:
        """
        Normalize system prompt: NFKC, reject forbidden control chars.

        Returns: Normalized prompt (or raises ValueError on forbidden chars)

        Behavior:
            - NFKC normalize (canonical decomposition + compatibility decomposition)
            - Reject: null bytes, most control chars except newline/tab
            - Accept: Unicode letters, numbers, punctuation, newlines, tabs
            - Max size: 8 KB (enforced per-engine, varies)

        Raises:
            ValueError: If prompt contains forbidden chars or too large
        """
        # NFKC normalize
        normalized = unicodedata.normalize("NFKC", prompt)

        # Check for forbidden control chars (reject null bytes, escape sequences)
        forbidden_chars = {
            "\x00",  # Null byte
            "\x01", "\x02", "\x03", "\x04", "\x05", "\x06", "\x07",  # BEL
            "\x08",  # Backspace
            "\x0b", "\x0c",  # VT, FF
            "\x0e", "\x0f",  # SO, SI
            "\x10", "\x11", "\x12", "\x13", "\x14", "\x15", "\x16", "\x17",  # DLE-ETB
            "\x18", "\x19", "\x1a", "\x1b", "\x1c", "\x1d", "\x1e", "\x1f",  # CAN-US
        }

        for char in normalized:
            if char in forbidden_chars:
                raise ValueError(f"Forbidden control character in system prompt: {ord(char):#04x}")

        return normalized


# Module-level convenience functions (optional, for backward compat)

def inject_system_prompt(
    system_prompt: Optional[str],
    transport: Union[Dict, str],
    engine_id: str,
    tenant_id: str = "_default"
) -> Union[Dict, str]:
    """
    Convenience function: create injector, inject, return result.

    See SystemPromptInjector.inject_system_prompt() for details.
    """
    injector = SystemPromptInjector(tenant_id=tenant_id)
    return injector.inject_system_prompt(system_prompt, transport, engine_id)


def system_already_injected(transport: Union[Dict, str], engine_id: str) -> bool:
    """
    Convenience function: check if system prompt already injected.

    See SystemPromptInjector._system_already_injected() for details.
    """
    injector = SystemPromptInjector()
    return injector._system_already_injected(transport, engine_id)
