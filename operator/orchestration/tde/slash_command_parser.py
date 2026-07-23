"""ADR-0214: Slash Command Parser.

Parses /use-engine and /debug-engine commands for explicit engine selection.

Works in CLI (claude -p) and Chat bridges (Discord, Slack, Web).

Example:
  /use-engine tiered_delegation
  Implement OAuth + OIDC + SAML

  → task_text = "Implement OAuth + OIDC + SAML"
  → engine_override = "tiered_delegation"
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Optional

_logger = logging.getLogger(__name__)


@dataclass
class ParseResult:
    """Result of slash-command parsing."""
    task_text: str
    engine_override: Optional[str]  # "tiered_delegation" | "acs" | "claude_code" | None
    debug_mode: bool
    original_message: str


class SlashCommandParser:
    """Parse /use-engine commands (CLI + Bridges)."""

    VALID_ENGINES = {"tiered_delegation", "acs", "claude_code"}

    def parse(self, message: str) -> ParseResult:
        """
        Parse message for slash commands.

        Returns:
            ParseResult (task_text, engine_override, debug_mode)

        Raises:
            ValueError: If engine is invalid
        """

        # Pattern 1: /use-engine <name>
        match = re.match(r"^/use-engine\s+(\w+)\s*\n?(.*)", message, re.DOTALL)
        if match:
            engine = match.group(1)
            task_start = message.find("\n")
            task_text = message[task_start + 1:].strip() if task_start >= 0 else ""

            if engine not in self.VALID_ENGINES:
                raise ValueError(
                    f"Unknown engine: {engine}. Valid: {', '.join(self.VALID_ENGINES)}"
                )

            _logger.info(f"Parsed /use-engine {engine}")
            return ParseResult(
                task_text=task_text,
                engine_override=engine,
                debug_mode=False,
                original_message=message,
            )

        # Pattern 2: /engine-auto
        match = re.match(r"^/engine-auto\s*\n?(.*)", message, re.DOTALL)
        if match:
            task_text = match.group(1).strip()
            _logger.info("Parsed /engine-auto (will auto-detect)")
            return ParseResult(
                task_text=task_text,
                engine_override=None,  # Explicit auto-detection
                debug_mode=False,
                original_message=message,
            )

        # Pattern 3: /debug-engine
        match = re.match(r"^/debug-engine\s*\n?(.*)", message, re.DOTALL)
        if match:
            task_text = match.group(1).strip()
            _logger.info("Parsed /debug-engine (will show signals)")
            return ParseResult(
                task_text=task_text,
                engine_override=None,
                debug_mode=True,
                original_message=message,
            )

        # No slash command: normal message (auto-detect)
        return ParseResult(
            task_text=message,
            engine_override=None,
            debug_mode=False,
            original_message=message,
        )

    @staticmethod
    def format_help() -> str:
        r"""Return help text for slash commands."""
        return r"""
**Engine Selection Commands:**

`/use-engine tiered_delegation <task>` — Force TDE (parallelized, context-preserving)
`/use-engine acs <task>` — Force ACS (recursive delegation)
`/use-engine claude_code <task>` — Force sequential (default engine)
`/engine-auto <task>` — Let CorvinOS auto-detect (normal behavior)
`/debug-engine <task>` — Show engine selection signals

**Example:**
```
/use-engine tiered_delegation
Refactor auth module: OAuth + OIDC + SAML
```

**Normal (no command):**
Just type your task; CorvinOS will auto-detect which engine to use.
"""
