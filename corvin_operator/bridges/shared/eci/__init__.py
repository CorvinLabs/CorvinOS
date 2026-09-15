"""Engine Command Interface (ECI) — ADR-0069 Component 5.

Normalises per-engine slash-command transports so Corvin can dispatch
generic commands (/btw, /cancel, /compact) to the correct engine-native
mechanism, and surface engine-local commands under a reserved /e: namespace.

Design constraints:
  - MUST NOT import anthropic (CI AST lint, same rule as L34/L35/L36/L37/L38)
  - MUST NOT perform network I/O (pure dispatch logic)
  - manifest.native_commands handler_method MUST name a method on the engine
    instance — resolved at dispatch time to avoid circular imports
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class CommandResult:
    """Result returned by any ECI dispatch call.

    success=True means the command was accepted (either executed live or
    queued for buffered delivery).  buffered=True means it was queued and
    will take effect at the next engine spawn, not immediately.
    message is the user-visible feedback string (empty = use default ACK).
    """

    success: bool
    message: str
    buffered: bool = False


@dataclass
class NativeCommandSpec:
    """Spec for one engine-local /e:<name> command.

    handler_method is the name of a method on the engine instance that
    accepts a single str (the arguments string) and returns CommandResult.
    """

    description: str
    handler_method: str
    usage: str = ""


@dataclass
class EngineCommandManifest:
    """Declarative capability map for one engine's slash-command surface.

    Generic command transports
    --------------------------
    mid_stream_inject : "stdin_json" | "buffered" | "sidecar" | None
        - "stdin_json"  — write JSONL to subprocess stdin (ClaudeCode, OpenCode)
        - "buffered"    — queue text; prepend at next spawn (HTTP engines)
        - "sidecar"     — POST to loopback ECI sidecar (future, M7)
        - None          — not supported; user gets explicit feedback

    cancel : "sigterm" | "http_delete" | None
    compact : "flag" | None

    Engine-local commands
    ---------------------
    native_commands : dict[str, NativeCommandSpec]
        Keys are the /e:<name> identifiers (alphanumeric + underscore).
        Exposed only when this engine is the active engine for the chat.
    """

    mid_stream_inject: str | None
    cancel: str | None
    compact: str | None
    native_commands: dict[str, NativeCommandSpec] = field(default_factory=dict)
