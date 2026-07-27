"""engine_registry.py — Phase 4 (ADR-0001 / ADR-0003) Multi-Engine Resolver.

Maps a stable ``engine_id`` (``claude_code``, ``codex_cli``, future
``gemini_cli``, ``ollama``, ``vllm``, ``anthropic_sdk`` ...) to a concrete
``WorkerEngine`` instance from ``bridges/shared/agents/``. Builds factories
that AWP-workers can call when they need an LLM step — the
``worker_engine_factory`` injected into ``state`` by the adapter is exactly
the callable produced here.

This is the structural piece that closes the Phase-3c architecture gap
documented in ADR-0003 § Architecture invariant: AWP-internal LLM calls
must flow through the Engine layer, not bypass it. The factory contract
is the seam.

Design notes:

* The registry is **declarative-first**: the static ``_ENGINE_BUILDERS``
  table is the single source of truth for which engine_ids are known.
  Adding ``gemini_cli`` later is one entry plus a thin builder.
* Factories are **per-call**, not singletons: each AWP-worker that asks
  for an engine gets a fresh instance with the chat's persona / channel /
  chat_key context baked in. This keeps audit-event ``engine_id`` ↔
  ``awp_task_id`` correlatable.
* Failures **never raise** into the dispatcher — an unknown engine_id or
  a missing engine module returns ``None`` and is logged. The adapter
  treats a missing factory as "AWP runs without engine routing" — the
  Phase-3 fallback behaviour.
* The registry imports the agents lazily so this module is cheap to
  import even on hosts where the engine binaries aren't installed.

API:

    list_engine_ids() -> list[str]
        All engine_ids the registry can currently build (filtered by
        capability_info: only engines whose underlying CLI / SDK is
        importable).

    get_engine(engine_id) -> WorkerEngine | None
        One-shot factory — returns a fresh engine instance or None.

    make_factory(default_engine_id, *, persona, channel, chat_key) -> Callable
        Build a worker_engine_factory closure: the AWP-worker calls
        ``factory(engine_id=None)`` to get the default, or with a
        specific id to override per worker step. The closure carries
        the per-chat context for audit attribution.

    resolve_engine_id(profile, env_var) -> str
        Resolve the effective engine_id for a chat:
            chat_profile.default_engine
              > env (e.g. CORVIN_DEFAULT_ENGINE)
              > "claude_code" (hard fallback — the proven path)
"""
from __future__ import annotations

import importlib
import logging
import os
from typing import Any, Callable

logger = logging.getLogger(__name__)

# ----- engine builders ----------------------------------------------------

def _build_claude_code() -> Any | None:
    """Lazy-import ClaudeCodeEngine from bridges/shared/agents/."""
    try:
        from .agents.claude_code import ClaudeCodeEngine  # type: ignore
        return ClaudeCodeEngine()
    except ImportError:
        try:
            mod = importlib.import_module("agents.claude_code")
            cls = getattr(mod, "ClaudeCodeEngine", None)
            return cls() if cls is not None else None
        except ImportError as e:
            logger.debug("engine_registry: claude_code unavailable: %s", e)
            return None


def _build_codex_cli() -> Any | None:
    """Lazy-import CodexCliEngine from bridges/shared/agents/."""
    try:
        from .agents.codex_cli import CodexCliEngine  # type: ignore
        return CodexCliEngine()
    except ImportError:
        try:
            mod = importlib.import_module("agents.codex_cli")
            cls = getattr(mod, "CodexCliEngine", None)
            return cls() if cls is not None else None
        except ImportError as e:
            logger.debug("engine_registry: codex_cli unavailable: %s", e)
            return None


def _build_opencode() -> Any | None:
    """Lazy-import OpenCodeEngine from bridges/shared/agents/."""
    try:
        from .agents.opencode_cli import OpenCodeEngine  # type: ignore
        return OpenCodeEngine()
    except ImportError:
        try:
            mod = importlib.import_module("agents.opencode_cli")
            cls = getattr(mod, "OpenCodeEngine", None)
            return cls() if cls is not None else None
        except ImportError as e:
            logger.debug("engine_registry: opencode unavailable: %s", e)
            return None


def _build_hermes() -> Any | None:
    """Lazy-import HermesEngine from bridges/shared/agents/.

    Absent from the table until 2026-07-27, although ``agents/hermes_engine.py``
    has shipped for far longer and every OTHER surface names ``hermes`` as a
    selectable engine — ``routes/engine.py``'s ``valid_worker_engines``, the
    Settings → Engines page, ``chat_runtime._DIRECT_OS_ENGINES``, and the Hermes
    fallback that a fresh install lands on when Claude Code is unauthenticated.
    The console's /settings/engine/capabilities route asks ``get_engine(eid)``
    for each of the five it advertises, so hermes reported ``capabilities: {}``
    and ``command_manifest: null`` while the engine object itself has ten
    capabilities. The chat path never noticed because it constructs
    ``HermesEngine`` directly rather than through this registry.
    """
    try:
        from .agents.hermes_engine import HermesEngine  # type: ignore
        return HermesEngine()
    except ImportError:
        try:
            mod = importlib.import_module("agents.hermes_engine")
            cls = getattr(mod, "HermesEngine", None)
            return cls() if cls is not None else None
        except ImportError as e:
            logger.debug("engine_registry: hermes unavailable: %s", e)
            return None


def _build_copilot() -> Any | None:
    """Lazy-import CopilotCliEngine from bridges/shared/agents/.

    Same gap and same symptom as ``_build_hermes`` — advertised in
    ``valid_worker_engines`` and in the capability matrix's structural-gap table,
    buildable by nothing.
    """
    try:
        from .agents.copilot_cli import CopilotCliEngine  # type: ignore
        return CopilotCliEngine()
    except ImportError:
        try:
            mod = importlib.import_module("agents.copilot_cli")
            cls = getattr(mod, "CopilotCliEngine", None)
            return cls() if cls is not None else None
        except ImportError as e:
            logger.debug("engine_registry: copilot unavailable: %s", e)
            return None


# Static table — the single source of truth. To add a new engine:
#   1. Implement ``MyEngine`` matching ``WorkerEngine`` Protocol in
#      ``bridges/shared/agents/<name>.py``.
#   2. Add a ``_build_my_engine()`` lazy-import here.
#   3. Add ``"my_engine_id": _build_my_engine`` to the table below.
# Then ``list_engine_ids()`` will surface it automatically.
_ENGINE_BUILDERS: dict[str, Callable[[], Any | None]] = {
    "claude_code": _build_claude_code,
    "codex_cli":   _build_codex_cli,
    "opencode":    _build_opencode,
    "hermes":      _build_hermes,
    "copilot":     _build_copilot,
    # Future: "gemini_cli", "vllm", "anthropic_sdk", "azure_openai"
}

# Default engine — the conservative, battle-tested path.
DEFAULT_ENGINE_ID = "claude_code"


# ----- public API ---------------------------------------------------------

def list_engine_ids(*, available_only: bool = True) -> list[str]:
    """Return engine_ids the registry knows about.

    available_only=True (default): filter to engines whose underlying
    module is actually importable on this host. Useful for /whoami,
    diagnostics, and the Phase-5 compliance-policy validator.

    available_only=False: return every id from the static table,
    even if unbuildable. Useful for documentation generation.
    """
    if not available_only:
        return list(_ENGINE_BUILDERS.keys())
    out: list[str] = []
    for engine_id, builder in _ENGINE_BUILDERS.items():
        try:
            inst = builder()
            if inst is not None:
                out.append(engine_id)
        except Exception:  # noqa: BLE001
            continue
    return out


def _engine_allowed_by_license(engine_id: str) -> bool:
    """ADR-0150 (structural): fail-CLOSED engines_allowed gate at the registry
    chokepoint, so every consumer of get_engine() — the make_factory
    worker-engine factory and the AWP DAG walker — inherits the license check
    BY CONSTRUCTION rather than each call site re-implementing it.

    Returns True to proceed, False to deny (get_engine then returns None — a deny
    is "no engine", which is fail-closed for the spawn). Latent today: every tier
    sets engines_allowed=None, so assert_limit is a no-op and this returns True.
    The dual-env test bypass mirrors adapter.py / delegation.py / acs_runtime.py
    (BOTH vars required — one alone must NOT disable it).

    NOTE on scope: this is NOT the universal spawn chokepoint. adapter, gateway,
    ACS, and the delegate path construct their engines DIRECTLY (not via
    get_engine) and are gated at their own spawn sites — see
    docs/claude-ref/license-metering.md. The only truly universal point is
    WorkerEngine.spawn(); get_engine() covers the factory + walker paths.
    """
    if (os.environ.get("CORVIN_AGENTS_SKIP_LIVE") == "1"
            and os.environ.get("CORVIN_INTEGRATION_TEST") == "1"):
        return True
    try:
        import sys as _sys
        from pathlib import Path as _P
        _op = str(_P(__file__).resolve().parents[2])  # operator/
        if _op not in _sys.path:
            _sys.path.insert(0, _op)
        from license.validator import assert_limit as _al  # type: ignore
        from license.limits import LicenseLimitError as _LE  # type: ignore
    except ImportError:
        logger.warning(
            "engine_registry: license module unavailable — denying engine build "
            "(fail-closed)"
        )
        return False
    try:
        _al("engines_allowed", engine_id)
        return True
    except _LE:
        logger.warning(
            "engine_registry: engine_id=%r not allowed by license", engine_id
        )
        return False
    except Exception as e:  # noqa: BLE001 — fail-CLOSED on any license error
        logger.warning(
            "engine_registry: license gate error (%s) — denying (fail-closed)", e
        )
        return False


def get_engine(engine_id: str) -> Any | None:
    """One-shot engine instantiation. Returns None on unknown id, license deny,
    or builder failure. Never raises."""
    builder = _ENGINE_BUILDERS.get(engine_id)
    if builder is None:
        logger.debug("engine_registry: unknown engine_id %r", engine_id)
        return None
    # Structural engines_allowed gate (fail-closed) — inherited by every
    # get_engine consumer (make_factory worker factory + AWP DAG walker).
    if not _engine_allowed_by_license(engine_id):
        return None
    try:
        return builder()
    except Exception as e:  # noqa: BLE001
        logger.debug("engine_registry: builder for %r raised: %s", engine_id, e)
        return None


def resolve_engine_id(profile: dict | None,
                      env_var: str = "CORVIN_DEFAULT_ENGINE",
                      legacy_env_var: str = "CORVIN_DEFAULT_ENGINE") -> str:
    """Pick the effective engine_id for a chat.

    Order:
      1. ``profile.default_engine`` (per-chat override via cowork persona
         or chat_profile)
      2. env var (canonical, then legacy alias for the rebrand window)
      3. ``DEFAULT_ENGINE_ID`` ("claude_code")

    Validates: the picked id must exist in ``_ENGINE_BUILDERS``;
    unknown ids fall back to the next tier with a warning. This stops
    a typo'd persona JSON from silently routing every turn into the
    void.
    """
    candidates = []
    if isinstance(profile, dict):
        v = profile.get("default_engine")
        if isinstance(v, str) and v.strip():
            candidates.append(("profile", v.strip()))

    canonical = os.environ.get(env_var, "").strip()
    if canonical:
        candidates.append((f"env:{env_var}", canonical))
    else:
        legacy = os.environ.get(legacy_env_var, "").strip()
        if legacy:
            candidates.append((f"env:{legacy_env_var}", legacy))

    candidates.append(("default", DEFAULT_ENGINE_ID))

    for source, engine_id in candidates:
        if engine_id in _ENGINE_BUILDERS:
            return engine_id
        logger.warning(
            "engine_registry: %s asked for unknown engine_id %r; "
            "checking next tier", source, engine_id,
        )

    # Should never reach here — DEFAULT_ENGINE_ID is in the table by
    # construction. But return it explicitly to guarantee a non-None.
    return DEFAULT_ENGINE_ID


def make_factory(default_engine_id: str | None = None,
                 *,
                 persona: str | None = None,
                 channel: str | None = None,
                 chat_key: str | None = None) -> Callable[..., Any | None]:
    """Build a ``worker_engine_factory`` closure for AWP-workers.

    The closure has signature ``factory(engine_id: str | None = None) ->
    WorkerEngine | None``. Workers call it without an arg to get the
    chat's default engine, or with a specific id (e.g. ``"codex_cli"``)
    to override per worker step (Phase-4 multi-engine workflows).

    The closure captures persona / channel / chat_key as context — these
    are *read-only* for the worker but available for the engine to
    propagate into its own audit trail (per the ADR-0003 integrity rule:
    every engine_id audit event must carry an enclosing awp_task_id).

    Returns a callable even if the default engine is unavailable —
    the closure still maps unknown ids to None gracefully.
    """
    default_id = default_engine_id or DEFAULT_ENGINE_ID

    def factory(engine_id: str | None = None) -> Any | None:
        eid = engine_id or default_id
        engine = get_engine(eid)
        if engine is None:
            logger.warning(
                "worker_engine_factory: engine_id=%r unavailable "
                "(persona=%s channel=%s)",
                eid, persona, channel,
            )
            return None

        # Stamp context for the engine to pick up if it cares (the
        # ClaudeCodeEngine and CodexCliEngine signatures already accept
        # persona / channel / chat_key as kwargs to spawn(); the AWP-
        # worker is expected to forward those when calling spawn).
        # We attach them as attributes on the engine instance for
        # introspection-friendly access too.
        try:
            setattr(engine, "_corvin_context", {
                "persona": persona,
                "channel": channel,
                "chat_key": chat_key,
                "engine_id": eid,
            })
        except Exception:  # noqa: BLE001
            pass
        return engine

    # Stamp diagnostics on the factory itself so /whoami can introspect.
    factory.corvin_default_engine = default_id  # type: ignore[attr-defined]
    factory.corvin_context = {  # type: ignore[attr-defined]
        "persona": persona, "channel": channel, "chat_key": chat_key,
    }
    return factory
