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

import json
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


def legacy_chat_key(s: str) -> str:
    """The alnum-only directory form of a chat id: every other character is ``_``, cut at 64.

    LOSSY: ``a.b@x.com``, ``a_b@x.com`` and ``a-b@x.com`` are all ``a_b_x_com``. It is the
    name a chat directory has unless another chat already holds it
    (:func:`resolve_chat_dir`), and the form every pre-2026-11-19 store was written under.
    """
    return "".join(ch if ch.isalnum() else "_" for ch in str(s))[:64] or "anon"


def safe_chat_key(s: str) -> str:
    """Sanitise a chat id for use as a path segment (:func:`legacy_chat_key`).

    This is the DEFAULT directory name of a chat, not necessarily the one it has: where
    two chat ids share it, the second one lives under :func:`hashed_chat_key`. Code that
    needs a chat's directory calls :func:`resolve_chat_dir` / :func:`claude_session_dirs`;
    code that matches a directory NAME against a subject (erasure) must try both
    :func:`chat_dir_names`.
    """
    return legacy_chat_key(s)


def hashed_chat_key(s: str) -> str:
    """The collision-free directory form: readable prefix + ``_`` + sha256(raw id)[:16].

    64 characters at most, like the legacy form. Used for a chat whose legacy name is held
    by a different chat id."""
    import hashlib  # noqa: PLC0415
    raw = str(s)
    digest = hashlib.sha256(raw.encode("utf-8", "surrogatepass")).hexdigest()[:16]
    return legacy_chat_key(raw)[:47] + "_" + digest


def chat_dir_names(s: str) -> tuple[str, str]:
    """Both directory names a chat id can have: ``(legacy, hashed)``."""
    return legacy_chat_key(s), hashed_chat_key(s)


#: Who a chat directory belongs to: ``{"chat_key": <raw id>, "claimed_at": <ts>}``, kept in
#: the chat's ledger store (``session_ledger.ledger_dir(workdir)``) - outside every worker
#: cwd and write-protected by the path gate, like the ledger next to it.
CHAT_OWNER_FILE = "owner.json"


def _ledger_store(workdir: Path) -> Path:
    try:
        from . import session_ledger as _sl  # type: ignore  # noqa: PLC0415
    except ImportError:
        import session_ledger as _sl  # type: ignore  # noqa: PLC0415
    return Path(_sl.ledger_dir(workdir))


def _owner_state(workdir: Path, chat_key: str) -> tuple[str, bool]:
    """``(state, has_marker)`` - state is ``"mine"``, ``"foreign"`` or ``"unclaimed"``.

    * the owner marker names a chat id -> that id's, nobody else's;
    * a marker that cannot be read as one -> ``foreign`` (never guessed);
    * no marker: the ledger decides. A recorded TURN of a different chat id that maps to
      the same directory name makes the directory ``foreign`` - also when this chat's own
      turns are in it too (the two shared it before 2026-11-19; neither keeps it).
      Only this chat's turns -> ``mine``. No such turn -> ``unclaimed``. Boundary records
      are not evidence: the inactivity sweep writes them under the directory name.
    """
    raw = str(chat_key)
    name = legacy_chat_key(raw)
    store = _ledger_store(workdir)
    marker = store / CHAT_OWNER_FILE
    try:
        fd = os.open(marker, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except FileNotFoundError:
        fd = -1
    except OSError:
        return "foreign", True
    if fd >= 0:
        try:
            with os.fdopen(fd, "r", encoding="utf-8", errors="surrogatepass") as fh:
                data = json.loads(fh.read(65536))
            owner = data.get("chat_key") if isinstance(data, dict) else None
        except (OSError, ValueError):
            owner = None
        if not isinstance(owner, str) or not owner:
            return "foreign", True
        return ("mine" if owner == raw else "foreign"), True
    mine = False
    try:
        with open(store / "ledger.jsonl", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"turn"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(rec, dict) or rec.get("kind") != "turn":
                    continue
                key = rec.get("chat_key")
                if not isinstance(key, str) or not key:
                    continue
                if key == raw:
                    mine = True
                elif legacy_chat_key(key) == name:
                    return "foreign", False
    except OSError:
        pass
    return ("mine" if mine else "unclaimed"), False


def _claim_chat_dir(workdir: Path, chat_key: str) -> bool:
    """Write the owner marker unless one exists (hard link of a fresh temp: never an
    overwrite, so two chats racing for one name cannot both win). Never raises."""
    import tempfile  # noqa: PLC0415
    import time  # noqa: PLC0415
    try:
        store = _ledger_store(workdir)
        store.mkdir(parents=True, exist_ok=True)
        marker = store / CHAT_OWNER_FILE
        fd, tmp = tempfile.mkstemp(prefix=".owner.", suffix=".tmp", dir=str(store))
        try:
            if hasattr(os, "fchmod"):
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8", errors="surrogatepass") as fh:
                json.dump({"chat_key": str(chat_key), "claimed_at": int(time.time())}, fh,
                          ensure_ascii=True)
            try:
                os.link(tmp, marker)
                return True
            except FileExistsError:
                return False
            except (OSError, AttributeError, NotImplementedError):
                if os.path.lexists(marker):
                    return False
                os.replace(tmp, marker)        # filesystems without hard links
                return True
        finally:
            try:
                os.unlink(tmp)
            except OSError:
                pass
    except OSError:
        return False


def resolve_chat_dir(parent: Path | str, chat_key: str, *, claim: bool = False) -> Path:
    """The directory of chat ``chat_key`` below ``parent`` (``<sessions>/<channel>/``).

    The sanitised name alone is lossy, so two chat ids could resolve to ONE working
    directory - one Claude session, one ``.corvin-history.md``, one ledger. The e-mail
    bridge keys a chat by its From address, where ``a.b@x.com`` and ``a_b@x.com`` are two
    people. Rule:

    * the hashed directory exists -> that is the chat's (it was diverted before);
    * the legacy name is this chat's or nobody's -> the legacy name. Every chat that
      existed before this rule, and every chat whose name nobody else wants, keeps the
      name it always had - nothing moves, no mirror of the name goes stale;
    * the legacy name belongs to a DIFFERENT chat id (owner marker, or that id's recorded
      turns) -> the hashed name. Where two ids already shared a directory, both get their
      own new one and the shared directory is left as it is, untouched.

    ``claim=True`` (the adapter, when it is about to use the directory) records this chat
    as the owner of an unclaimed legacy name; ``claim=False`` never writes. Neither creates
    the working directory. Never raises."""
    parent = Path(parent)
    raw = str(chat_key)
    legacy, hashed = parent / legacy_chat_key(raw), parent / hashed_chat_key(raw)
    try:
        if hashed.is_dir():
            return hashed
        state, has_marker = _owner_state(legacy, raw)
        if state == "foreign":
            return hashed
        if claim and not has_marker and not _claim_chat_dir(legacy, raw):
            # Lost the race for the name (or the store is not writable): whoever holds
            # the marker now decides.
            if _owner_state(legacy, raw) == ("foreign", True):
                return hashed
    except Exception:  # noqa: BLE001 — identity resolver must never raise
        pass
    return legacy


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
    safe = legacy_chat_key(chat_id)
    out: list[Path] = []

    _paths = _load_paths()
    if _paths is not None:
        # Canonical, tenant-aware (ADR-0007 Phase 1.2). The chat's name below the channel
        # directory is resolve_chat_dir's (the hashed name where the legacy one is held
        # by another chat id) - never written from here (claim=False).
        try:
            _canon = Path(_paths.voice_session_dir(channel, safe, tenant_id=tenant_id))
            out.append(resolve_chat_dir(_canon.parent, chat_id))
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
            out.append(resolve_chat_dir(legacy_root / legacy_chat_key(channel), chat_id))
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


#: Reset reasons that are the operator's explicit "start over" (``/new``,
#: ``/clear``, ``/reset``). Every other reason — context overflow, a corrupted
#: or idle stream, the inactivity sweep, an unknown caller — is an UNWANTED
#: reset, after which the session ledger re-supplies the chat's earlier turns.
MANUAL_RESET_REASONS: frozenset[str] = frozenset({"manual"})


#: Directory-name prefix of a ROUTE session (ADR-2268): the claude CLI state of turns that were
#: rerouted to a declared provider lives in ``<chat workdir>/.route-<provider>/`` and never in the
#: chat workdir itself. The CLI resumes whatever session sits under its cwd, so a rerouted
#: exchange in the native workdir would be replayed to the default endpoint by the next native
#: turn (and the reverse). One session per endpoint; neither resumes the other.
ROUTE_SESSION_PREFIX = ".route-"


def route_session_dir(workdir: Path, provider: str) -> Path:
    """The session directory of turns rerouted to ``provider`` (created by the caller)."""
    base = Path(workdir)
    if base.name.startswith(ROUTE_SESSION_PREFIX):
        base = base.parent
    # The readable part alone is lossy ("a-b" and "a_b" collapse): the hash of the raw id keeps two
    # providers cleared for different data out of one CLI session.
    import hashlib  # noqa: PLC0415
    digest = hashlib.sha256(str(provider).encode("utf-8")).hexdigest()[:8]
    return base / (ROUTE_SESSION_PREFIX + legacy_chat_key(provider) + "-" + digest)


def _route_dirs(workdir: Path) -> list[Path]:
    try:
        return sorted(p for p in Path(workdir).glob(ROUTE_SESSION_PREFIX + "*")
                      if p.is_dir() and not p.is_symlink())
    except OSError:
        return []


def _wipe_state(workdir: Path) -> list[str]:
    removed: list[str] = []
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


def reset_claude_session_state(workdir: Path, *, reason: str = "unspecified",
                               chat_key: str = "") -> list[str]:
    """Delete only Claude's conversation state in ``workdir``; keep project files.

    Returns the names of the entries removed, for logging. Missing entries are
    not an error — the operation is idempotent by construction.

    The chat's append-only session ledger (``session_ledger/``, outside the workdir) is not Claude
    state and is never removed here. When a session actually existed, the
    reset is recorded in that ledger as a boundary carrying ``reason``; an
    unknown reason counts as unwanted (the ledger then re-supplies the earlier
    turns), so a new caller that forgets to pass one fails towards
    remembering, not towards forgetting. An explicit ``/new`` is recorded even
    when no CLI state was left (after the inactivity sweep, say): the operator
    still asked to start over. ``chat_key`` is the RAW chat id — the directory
    name is the sanitised one, which GDPR erasure cannot match.
    """
    removed: list[str] = []
    workdir = Path(workdir)
    if not workdir.is_dir():
        return removed
    try:
        _prev_session = json.loads(
            (workdir / ".main_session.json").read_text(encoding="utf-8")
        ).get("session_id") or ""
    except (OSError, ValueError, AttributeError):
        _prev_session = ""

    removed = _wipe_state(workdir)
    if workdir.name.startswith(ROUTE_SESSION_PREFIX):
        # A route session: the chat's boundary is recorded against the CHAT workdir.
        workdir = workdir.parent
    else:
        # A chat-level reset (/new, overflow) clears every route session of the chat too.
        for rd in _route_dirs(workdir):
            removed += [rd.name + "/" + n for n in _wipe_state(rd)]

    if removed or reason in MANUAL_RESET_REASONS:
        try:
            try:
                from . import session_ledger as _ledger  # type: ignore
            except ImportError:
                import session_ledger as _ledger  # type: ignore
            _ledger.append_boundary(
                workdir, kind="reset", reason=str(reason or "unspecified"),
                channel=workdir.parent.name, chat_key=chat_key or workdir.name,
                session_id=str(_prev_session),
            )
        except Exception:  # noqa: BLE001 — a reset must never fail on the label
            pass
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
    if any((workdir / n).exists() for n in CLAUDE_STATE_FILES):
        return True
    if not workdir.name.startswith(ROUTE_SESSION_PREFIX):
        return any(has_claude_session_state(rd) for rd in _route_dirs(workdir))
    return False
