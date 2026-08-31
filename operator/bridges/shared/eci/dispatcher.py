"""CommandDispatcher — ECI dispatch layer (ADR-0069 M6).

Routes generic Corvin slash commands to the correct engine-native
transport and dispatches /e:<name> engine-local commands.

Usage (adapter.py integration point):
    from eci.dispatcher import CommandDispatcher

    result = CommandDispatcher.dispatch_btw(engine, text, btw_buffer)
    if result.success:
        ack = result.message or DEFAULT_ACK
    else:
        ack = result.message or FALLBACK_ACK

MUST NOT import anthropic (CI AST lint enforces, same rule as L34+).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from . import CommandResult, EngineCommandManifest

if TYPE_CHECKING:
    pass

_TRANSPORT_LABELS: dict[str | None, str] = {
    "stdin_json": "live (mid-stream)",
    "buffered":   "gepuffert — wirkt ab nächstem Turn",
    "sidecar":    "Sidecar-Kanal",
    None:         "nicht unterstützt",
}


def _get_manifest(engine: Any) -> EngineCommandManifest | None:
    return getattr(engine, "command_manifest", None)


class CommandDispatcher:
    """Stateless dispatch helper — all methods are class methods.

    The btw_buffer argument is a mutable list owned by the adapter
    (one per chat_key).  The dispatcher appends to it; the adapter
    drains it at the next spawn.
    """

    @classmethod
    def dispatch_btw(
        cls,
        engine: Any,
        text: str,
        btw_buffer: list[str],
    ) -> CommandResult:
        """Route a /btw injection to the appropriate engine transport."""
        manifest = _get_manifest(engine)
        if manifest is None:
            return CommandResult(success=False, message="")

        transport = manifest.mid_stream_inject

        if transport == "stdin_json":
            inject = getattr(engine, "inject", None)
            if inject is None:
                return CommandResult(
                    success=False,
                    message=f"✗ Engine '{engine.name}' deklariert stdin_json, hat aber keine inject()-Methode",
                )
            try:
                ok = inject(text)
                return CommandResult(success=bool(ok), message="")
            except Exception as exc:  # noqa: BLE001
                return CommandResult(success=False, message=f"✗ inject() fehlgeschlagen: {exc}")

        if transport == "buffered":
            btw_buffer.append(text)
            return CommandResult(
                success=True,
                buffered=True,
                message=f"📝 Notiz gepuffert — wirkt ab nächstem Turn (Engine: {engine.name})",
            )

        if transport == "sidecar":
            return CommandResult(
                success=False,
                message=f"✗ Sidecar-Transport ist noch nicht implementiert (Engine: {engine.name})",
            )

        return CommandResult(
            success=False,
            message=f"✗ /btw wird von Engine '{engine.name}' nicht unterstützt",
        )

    @classmethod
    def dispatch_native(
        cls,
        engine: Any,
        cmd: str,
        args: str,
    ) -> CommandResult:
        """Dispatch a /e:<cmd> engine-local command."""
        manifest = _get_manifest(engine)
        if manifest is None:
            return CommandResult(
                success=False,
                message=f"✗ /e:{cmd}: Engine '{getattr(engine, 'name', '?')}' hat kein Command-Manifest",
            )

        spec = manifest.native_commands.get(cmd)
        if spec is None:
            available = ", ".join(f"/e:{k}" for k in manifest.native_commands) or "keine"
            return CommandResult(
                success=False,
                message=(
                    f"✗ /e:{cmd} ist für Engine '{engine.name}' nicht verfügbar. "
                    f"Verfügbar: {available}"
                ),
            )

        handler = getattr(engine, spec.handler_method, None)
        if handler is None:
            return CommandResult(
                success=False,
                message=f"✗ Handler '{spec.handler_method}' nicht auf Engine '{engine.name}' gefunden",
            )

        try:
            result = handler(args)
            if not isinstance(result, CommandResult):
                return CommandResult(success=True, message=str(result))
            return result
        except Exception as exc:  # noqa: BLE001
            return CommandResult(success=False, message=f"✗ /e:{cmd} fehlgeschlagen: {exc}")

    @classmethod
    def format_commands(cls, engine: Any) -> str:
        """Return a /commands help table for the active engine."""
        manifest = _get_manifest(engine)
        engine_name = getattr(engine, "name", "?")

        lines = [f"Aktive Engine: {engine_name}", ""]

        if manifest is None:
            lines.append("  (kein Command-Manifest — nur Standard-Corvin-Commands)")
            return "\n".join(lines)

        btw_transport = manifest.mid_stream_inject
        lines.append(f"  /btw <text>    {_TRANSPORT_LABELS.get(btw_transport, btw_transport or 'n/a')}")

        cancel_transport = manifest.cancel
        if cancel_transport:
            lines.append(f"  /cancel        {cancel_transport}")

        compact_transport = manifest.compact
        if compact_transport:
            lines.append(f"  /compact       {compact_transport}")

        if manifest.native_commands:
            lines.append("")
            for cmd, spec in manifest.native_commands.items():
                usage = f" {spec.usage}" if spec.usage else ""
                lines.append(f"  /e:{cmd:<12}{spec.description}{usage}")

        return "\n".join(lines)
