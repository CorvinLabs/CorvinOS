"""session_state — the single source of truth for *where* a bridge chat's
Claude conversation state lives, and *what* counts as that state.

Why this module exists
----------------------
Two independent code paths need the same answer:

  * ``adapter._session_dir()`` — writes the state (``.main_session.json``,
    ``.session_started``, ``.claude/``) and reads it back on the next turn to
    decide between ``claude --resume <id>``, ``claude --continue`` and a
    fresh session.
  * ``session_reset._wipe_voice_state()`` — must delete exactly that state
    when the operator types ``/new``, ``/clear`` or ``/reset``.

Before 2026-08-28 each hand-built its own path. ADR-0007 Phase 1.2 moved the
adapter onto the tenant-aware resolver ``paths.voice_session_dir()``
(``<corvin_home>/tenants/<tid>/sessions/voice/<channel>/<chat>/``) and
``session_reset`` was not moved with it — it kept rmtree-ing
``<corvin_home>/voice/sessions/<channel>/<chat>/``, a path that resolves to
nothing under any configuration. ``/new`` therefore reported
``voice_state_removed: no``, ``.main_session.json`` survived, and the next
turn resumed the *old* Claude session verbatim. Discord channel
1501315335750684803 sat on the same session id for weeks.

The fix is structural, not a corrected string: both sides now call in here,
so a future path migration cannot desynchronise them again.

What is NOT session state
-------------------------
Everything else in the session workdir is a *project file* and survives a
reset — the ``/new`` reply promises this in so many words ("Project files in
this chat's session dir are kept; only Claude's memory was cleared"). That
notably includes ``outputs/``, ``tasks/``, ``operator/`` and the L37-retained
``cel-briefs/`` audit sidecars, which are governed by the retention policy and
must not be deleted by a user-facing reset.
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

# ── what counts as Claude conversation state ───────────────────────────────
#
# Keep in lockstep with adapter.call_claude()'s two probes:
#   has_session       = any(workdir.glob(".claude*")) or .session_started
#   resume_session_id = json(.main_session.json)["session_id"]
# A file listed here is one the adapter would otherwise use to continue the
# previous conversation.
CLAUDE_STATE_FILES: tuple[str, ...] = (
    ".claude.json",
    ".session_started",
    ".main_session.json",
)
# Anything else Claude may version alongside them (e.g. ".claude.session.json",
# the ".claude/" directory itself).
CLAUDE_STATE_GLOB = ".claude*"


def safe_chat_key(s: str) -> str:
    """Sanitise a chat id for use as a path segment.

    Identical to ``adapter._safe_id`` — the adapter's directory names are
    produced by that function, so any resolver aiming at the same directory
    must sanitise the same way.
    """
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:64] or "anon"


def _load_paths():
    """Load the bridge ``paths`` module by FILE PATH, not by module name.

    A plain ``import paths`` is not safe here. ``session_reset`` prepends
    ``operator/forge`` to ``sys.path`` before importing this module, and that
    directory ships its own unrelated ``paths.py`` (FORGE_ROOT / get_forge_home
    / …). Whichever lands in ``sys.path`` first wins, so name-based import
    silently resolved to the forge module, ``voice_session_dir`` came back as
    an AttributeError, and the reset found no directories to clear — the exact
    failure this module was written to end. Binding to the file next to us
    removes the ambiguity entirely.
    """
    here = Path(__file__).resolve().parent
    target = here / "paths.py"
    # A correctly-loaded copy already in sys.modules is reused as-is.
    cached = sys.modules.get("corvin_bridge_paths")
    if cached is not None:
        return cached
    try:
        import importlib.util as _ilu
        spec = _ilu.spec_from_file_location("corvin_bridge_paths", target)
        if spec is None or spec.loader is None:
            return None
        mod = _ilu.module_from_spec(spec)
        sys.modules["corvin_bridge_paths"] = mod
        spec.loader.exec_module(mod)
        return mod
    except Exception:  # noqa: BLE001 — a missing resolver must not crash a reset
        sys.modules.pop("corvin_bridge_paths", None)
        return None


def claude_session_dirs(channel: str, chat_id: str,
                        tenant_id: str | None = None) -> list[Path]:
    """Every directory ``adapter._session_dir()`` may resolve to, canonical first.

    The adapter picks the tenant-aware path when it exists, else falls back to
    the legacy layout it used before ADR-0007 Phase 1.2, else to that same
    legacy layout when the ``paths`` module is unimportable. A reset has to
    cover all of them: an install that has not been touched since the
    migration still keeps its live state in the legacy directory.

    Never creates directories and never raises — identity only. Returns a
    de-duplicated list; a caller that finds no existing directory in it has
    nothing to reset.
    """
    safe = safe_chat_key(chat_id)
    out: list[Path] = []

    _paths = _load_paths()
    if _paths is not None:
        # Canonical, tenant-aware (ADR-0007 Phase 1.2).
        try:
            out.append(Path(_paths.voice_session_dir(channel, safe,
                                                     tenant_id=tenant_id)))
        except Exception:  # noqa: BLE001 — identity resolver must never raise
            pass
        # Legacy: adapter.SESSIONS_ROOT = voice_dir()/sessions, honouring the
        # XDG_CACHE_HOME override the adapter still supports.
        try:
            xdg = os.environ.get("XDG_CACHE_HOME")
            if xdg:
                legacy_root = Path(xdg) / "corvin-voice" / "sessions"
            else:
                legacy_root = Path(_paths.voice_dir(tenant_id)) / "sessions"
            out.append(legacy_root / safe_chat_key(channel) / safe)
        except Exception:  # noqa: BLE001
            pass

    # De-duplicate while preserving order.
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        if p not in seen:
            seen.add(p)
            uniq.append(p)
    return uniq


def tenant_sessions_root(tenant_id: str | None = None) -> Path | None:
    """``<corvin_home>/tenants/<tid>/sessions/`` — the forge session-workspace root.

    This is the parent of the ``<channel>:<chat>`` directories that
    ``forge.scope.scope_root("session", ...)`` writes into. Returns None when
    the resolver is unavailable, so callers can fall back explicitly rather
    than silently aiming at a path that resolves to nothing.
    """
    _paths = _load_paths()
    if _paths is None:
        return None
    try:
        return Path(_paths.tenant_sessions_dir(tenant_id))
    except Exception:  # noqa: BLE001
        return None


def reset_claude_session_state(workdir: Path) -> list[str]:
    """Delete only Claude's conversation state in ``workdir``; keep project files.

    Returns the names of the entries removed, for logging. Missing entries are
    not an error — the operation is idempotent by construction.
    """
    removed: list[str] = []
    workdir = Path(workdir)
    if not workdir.is_dir():
        return removed

    for name in CLAUDE_STATE_FILES:
        p = workdir / name
        if p.exists() or p.is_symlink():
            p.unlink()
            removed.append(name)

    for p in sorted(workdir.glob(CLAUDE_STATE_GLOB)):
        is_real_dir = p.is_dir() and not p.is_symlink()
        if is_real_dir:
            shutil.rmtree(p)
        elif p.exists() or p.is_symlink():
            p.unlink()
        else:
            continue
        removed.append(p.name + "/" if is_real_dir else p.name)

    return removed


def has_claude_session_state(workdir: Path) -> bool:
    """True when ``workdir`` still holds state the adapter would resume from.

    Mirrors the adapter's own ``has_session`` probe plus the ``--resume``
    read, so a reset can be verified with the same predicate the next turn
    will apply.
    """
    workdir = Path(workdir)
    if not workdir.is_dir():
        return False
    if any(workdir.glob(CLAUDE_STATE_GLOB)):
        return True
    return any((workdir / n).exists() for n in CLAUDE_STATE_FILES)
