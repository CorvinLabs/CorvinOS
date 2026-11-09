"""Knowledge-base projector — keeps the task board equal to Corvin-Knowledge (ADR-2205).

The KB repo owns initiatives, epics and tasks. This module runs IN the console host
(both hosts: ``corvin_console.app`` and ``corvin_gateway.app`` call ``start``) and,
every ``TICK_S`` seconds:

* source changed (git HEAD or a KB file)  ->  ``kb heal`` (derivable fixes, committed
  and audited by the KB)  ->  ``kb export``  ->  ``projection.apply``
* source unchanged                        ->  ``projection.diff`` against the last
  export; any difference is DRIFT (the store was written behind the KB's back) and
  is repaired at once, recorded as ``drift_healed``

A board move of a KB item never patches the store: ``transition`` runs ``kb task``
(the KB's one status writer and state machine) and re-projects. A refused move
answers with the state machine's own message.

Every ``PERIODIC_S`` (not every tick) ``periodic`` runs the KB's slower loops
(ADR-2208, CONCEPT-0090 §3.1/§3.4): ``kb guidance run`` (learning loop), ``kb sweep
--create-tasks`` (G6 drift -> a regression task) and the SkillForge bridge, which mints
a bootstrap-graded ``learned-experience`` skill for each active guidance file and
retires the skill of retired guidance — reporting back through ``kb guidance ack``,
so ``kb.py`` stays the only writer of KB files.

The KB is located by ``CORVIN_KB_REPO`` or, by default, the ``Corvin-Knowledge``
checkout next to this repo — the default applies only to a process on the
install's canonical CORVIN_HOME (see ``_canonical_home``); a sandboxed home
needs CORVIN_KB_REPO. Neither -> the TENANT's own knowledge base,
``<tenant_home>/knowledge``, which exists on every install once the Knowledge Graph
plugin (a standard plugin, ``default_plugins.yaml``) is ENABLED for the tenant: the tick
then runs the plugin's ``kb_init.py`` (create, or refresh the engine once per process)
and projects it like any other KB. Disabled plugin -> that KB is not read. No KB at all ->
the projector is off and its status says why (``hint: enable_plugin`` when one click is
all that is missing).

Tenant isolation: the operator's KB (``CORVIN_KB_REPO`` / the sibling checkout) binds to
ONE tenant, ``CORVIN_KB_TENANT`` (default ``_default``). Every other tenant only ever gets
its OWN ``<tenant_home>/knowledge`` -- ``kb_repo(tenant)`` never answers another tenant's
KB. A host process projects its process tenant (``start_process_tenant``): the audit chain
accepts only that tenant's records, so a second tenant runs its own host process.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from core.task_tracking import projection, service, store

log = logging.getLogger("corvin.kb_projection")

TICK_S = 2.0
PERIODIC_S = 600.0        # G6 sweep + guidance + SkillForge bridge: periodic, never per tick
GUIDANCE_PERSONA = "assistant"   # SkillForge namespace the guidance skills are minted under
_last_periodic: dict[str, float] = {}
_periodic_out: dict[str, dict[str, Any]] = {}   # survives the per-tick state replacement
# the projector code this process LOADED — /kb/status compares it with the file on disk, so a
# host still running yesterday's projector says so (review R4-C1: the periodic loop had been
# committed for hours while the live process predated it)
_LOADED_SHA = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]


def code_state() -> dict[str, Any]:
    try:
        now = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:12]
    except OSError:
        now = None
    return {"loaded": _LOADED_SHA, "on_disk": now, "stale": now is not None and now != _LOADED_SHA}
_REPO_ROOT = Path(__file__).resolve().parents[3]
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _tenant_lock(tenant_id: str) -> threading.Lock:
    """One lock per tenant: a tick, a transition and the slow loops of ONE tenant never
    interleave; another tenant's 300-s sweep never stalls this tenant's board."""
    with _locks_guard:
        return _locks.setdefault(tenant_id, threading.Lock())
_state: dict[str, dict[str, Any]] = {}
_started: set[str] = set()
_stops: dict[str, threading.Event] = {}
_threads: dict[str, threading.Thread] = {}


class KbTransitionRefused(service.TaskTrackingError):
    """The KB state machine refused the move (-> HTTP 409)."""


def _canonical_home() -> bool:
    """True when this process runs on the install's own CORVIN_HOME.

    The implicit sibling ``Corvin-Knowledge`` checkout is the OPERATOR's real
    knowledge base; the projector heals and commits there every 2 s and
    ``/kb-transition`` writes it. A console started on a throwaway
    CORVIN_HOME (Playwright backends, sandboxes) must never do that — before
    2026-10-08 only pytest was excluded (ADV-10), so every non-pytest test
    console projected into the real KB. Canonical = unset, ``<repo>/.corvin``
    or ``~/.corvin``. Any other home needs an explicit CORVIN_KB_REPO."""
    home = os.environ.get("CORVIN_HOME")
    if not home:
        return True
    try:
        h = Path(home).expanduser().resolve()
        return h in {(_REPO_ROOT / ".corvin").resolve(), (_real_user_home() / ".corvin").resolve()}
    except OSError:
        return False


def _real_user_home() -> Path:
    """The account's home from the passwd database, NOT $HOME: a sandbox that sets
    HOME=<tmp> must not make <tmp>/.corvin look like the operator's install
    (R4-I-1). On Windows ``Path.home()`` follows the equally spoofable
    USERPROFILE, so the profile dir is read from the process token instead."""
    if os.name == "nt":
        win = _win_token_profile_dir()
        if win is not None:
            return win
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path.home()


def _win_token_profile_dir() -> "Path | None":
    """The current account's profile dir via ``GetUserProfileDirectoryW`` on the
    process token (registry-backed, ignores USERPROFILE/HOME). None on failure."""
    try:
        import ctypes  # noqa: PLC0415
        from ctypes import wintypes  # noqa: PLC0415

        adv, ue, k32 = ctypes.windll.advapi32, ctypes.windll.userenv, ctypes.windll.kernel32
        adv.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        tok = wintypes.HANDLE()
        if not adv.OpenProcessToken(wintypes.HANDLE(-1), 0x0008, ctypes.byref(tok)):  # TOKEN_QUERY
            return None
        try:
            n = wintypes.DWORD(0)
            ue.GetUserProfileDirectoryW(tok, None, ctypes.byref(n))
            buf = ctypes.create_unicode_buffer(max(n.value, 260))
            n = wintypes.DWORD(len(buf))
            if not ue.GetUserProfileDirectoryW(tok, buf, ctypes.byref(n)) or not buf.value:
                return None
            return Path(buf.value)
        finally:
            k32.CloseHandle(tok)
    except Exception:  # noqa: BLE001 — fall back to the pwd / Path.home() chain
        return None


def _is_kb(p: Path) -> bool:
    return (p / "scripts" / "kb.py").is_file() and (p / "kb" / "_meta" / "sources.yaml").is_file()


def _explicit_repo() -> Optional[Path]:
    """CORVIN_KB_REPO, else the sibling checkout on the canonical home — the operator's KB."""
    env = os.environ.get("CORVIN_KB_REPO")
    if env:
        return Path(env) if _is_kb(Path(env)) else None
    if _canonical_home():   # sandboxed CORVIN_HOME: never the operator's KB by default (ADV-10)
        p = _REPO_ROOT.parent / "Corvin-Knowledge"
        return p if _is_kb(p) else None
    return None


def kb_repo(tenant_id: Optional[str] = None) -> Optional[Path]:
    """The knowledge base bound to *tenant_id* (default: ``CORVIN_KB_TENANT``), or None.

    The operator's KB serves only ``kb_tenant()``; any other tenant gets its own
    ``<tenant_home>/knowledge`` when the Knowledge Graph plugin is enabled FOR THAT TENANT."""
    t = tenant_id or kb_tenant()
    if t == kb_tenant():
        if os.environ.get("CORVIN_KB_REPO"):
            return _explicit_repo()   # an explicit KB is never silently replaced by another one
        p = _explicit_repo()
        if p is not None:
            return p
    if kg_plugin_state(t) == "enabled":
        tp = tenant_kb_path(t)
        return tp if _is_kb(tp) else None
    return None


def _uses_tenant_kb(tenant_id: str) -> bool:
    """True when *tenant_id*'s KB is its own ``<tenant_home>/knowledge`` (not the operator's)."""
    if tenant_id == kb_tenant() and (os.environ.get("CORVIN_KB_REPO") or _explicit_repo() is not None):
        return False
    return True


# --- the tenant's own knowledge base (Knowledge Graph plugin) --------------------------

KG_PLUGIN_ID = "corvin_knowledge"
KG_INDEX_ID = "plugin:buildin-knowledge_management-corvin_knowledge"
_kg_ensured: set[str] = set()     # tenants whose KB was ensured (engine refreshed) by this process


def tenant_kb_path(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home  # honours CORVIN_HOME

    return tenant_home(tenant_id) / "knowledge"


def kg_plugin_state(tenant_id: str) -> str:
    """``absent`` | ``installed`` | ``enabled`` for the Knowledge Graph plugin in the tenant
    registry. Read on every call, never cached: an enable must bind the KB on the very next
    tick, a disable must unbind it (one small YAML read per 2 s tick)."""
    try:
        from corvin_plugins.manifest import PluginError  # type: ignore[import-not-found]
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]
    except ImportError:   # no plugin subsystem in this installation: nothing to bind
        return "absent"
    try:
        rec = TenantRegistry.load(tenant_id=tenant_id).records.get(KG_PLUGIN_ID)
    except (PluginError, OSError) as exc:   # RegistryCorrupt is a PluginError: fail closed, unbound
        log.warning("plugin registry of %s unreadable (%s): knowledge base not bound", tenant_id,
                    type(exc).__name__)
        return "absent"
    if rec is None:
        return "absent"
    return "enabled" if rec.enabled else "installed"


def ensure_tenant_kb(tenant_id: str) -> dict[str, Any]:
    """Create the tenant's knowledge base, or refresh its engine, by running the ENABLED
    plugin's ``kb_init.py`` (a subprocess with the KB's minimal environment — plugin code
    never runs in this process, and never for a tenant that did not enable it)."""
    if kg_plugin_state(tenant_id) != "enabled":
        return {"status": "skipped", "reason": "Knowledge Graph plugin not enabled"}
    try:
        from .routes import marketplace_resolve  # noqa: PLC0415

        init = marketplace_resolve.resolve_builtin_dir(KG_INDEX_ID) / "src" / "kb_init.py"
    except Exception as exc:  # noqa: BLE001
        return _kg_failed_outcome(tenant_id, {"status": "error",
                                              "error": f"plugin source not found: {type(exc).__name__}: {exc}"[:300]})
    if not init.is_file():
        return _kg_failed_outcome(tenant_id, {"status": "error",
                                              "error": "the installed Knowledge Graph plugin predates kb_init.py — update it"})
    try:
        r = subprocess.run([sys.executable, str(init), "--target", str(tenant_kb_path(tenant_id))],
                           capture_output=True, text=True, timeout=120, env=_env("kb-init"), encoding="utf-8", errors="replace")
    except (subprocess.TimeoutExpired, OSError) as exc:
        return _kg_failed_outcome(tenant_id, {"status": "error", "error": f"kb_init did not finish: {type(exc).__name__}"})
    try:
        res = json.loads(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        res = {"status": "error", "error": (r.stderr or r.stdout).strip()[-300:]}
    if res.get("status") in ("created", "ok", "upgraded"):
        _kg_ensured.add(tenant_id)
        _kg_failed.pop(tenant_id, None)
    else:
        return _kg_failed_outcome(tenant_id, res)
    if res.get("status") in ("created", "upgraded"):
        try:
            from . import audit as console_audit  # noqa: PLC0415

            console_audit.action_performed(
                tenant_id=tenant_id, sid_fingerprint="host:kb_projection",
                action=f"kb.tenant_{res['status']}", target_kind="knowledge_base",
                target_id=str(res.get("engine_commit") or "")[:12], trigger="kb_projection")
        except Exception:  # noqa: BLE001 — the KB's own git history records it too
            log.warning("tenant KB audit record failed", exc_info=True)
    log.info("tenant knowledge base for %s: %s", tenant_id, res.get("status"))
    return res


#: tenant -> (retry-after monotonic, last outcome). A refused or failed kb_init used to run on
#: EVERY 2-s projector tick (30 subprocesses and 30 log lines a minute, forever — review
#: 2026-10-21); now it is retried after KB_INIT_RETRY_S and logged once per attempt.
_kg_failed: dict[str, tuple[float, dict[str, Any]]] = {}
KB_INIT_RETRY_S = 600.0


def _kg_failed_outcome(tenant_id: str, res: dict[str, Any]) -> dict[str, Any]:
    _kg_failed[tenant_id] = (time.monotonic() + KB_INIT_RETRY_S, res)
    log.warning("tenant knowledge base for %s: %s (%s) — next attempt in %d s", tenant_id,
                res.get("status"), str(res.get("error") or res.get("reason") or "")[:160], int(KB_INIT_RETRY_S))
    return res


def kb_tenant() -> str:
    return os.environ.get("CORVIN_KB_TENANT", "_default")


_WORK_FILE = __import__("re").compile(r"^kb/(initiatives|epics|tasks)/[^/]+\.md$")
_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND",
             # Windows: Python and git do not start without these (no secrets among them)
             "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP",
             "USERPROFILE", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA")


def _env(actor: str) -> dict[str, str]:
    """A minimal environment for KB subprocesses: the console's own secrets (API keys,
    tokens in os.environ) are never handed to code read from the KB repository."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.update(KB_ACTOR=actor, KB_ACTOR_NAME=actor, KB_ACTOR_EMAIL="kb@corvin.local",
               PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0",
               # the output is decoded as UTF-8 below; Windows' default pipe/locale codec (cp1252)
               # garbled or refused non-ASCII titles (kb export, kb index)
               PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
    return env


class KbIndexCorrupt(RuntimeError):
    """The KB checkout's ``.git/index`` cannot be read (a writer was hard-killed mid-write)."""


#: git's own wording for an unreadable index (``unknown index entry format 0x…``,
#: ``index file corrupt``, ``bad signature``, ``index file smaller than expected``)
_INDEX_CORRUPT = __import__("re").compile(
    r"unknown index entry format|index file corrupt|bad index file|bad signature 0x"
    r"|index file smaller than expected|index uses \S+ extension, which we do not understand")

#: tenant -> (retry-after monotonic, message). A corrupt index is not repaired by retrying:
#: the projector reports it ONCE (ERROR with the repair command) and re-checks every
#: KB_INDEX_RETRY_S instead of running heal/export against a broken checkout every tick.
_index_corrupt: dict[str, tuple[float, str]] = {}
KB_INDEX_RETRY_S = 60.0


def index_repair_hint(repo: Path) -> str:
    g = repo / ".git"
    return (f"KB checkout {repo}: .git/index is unreadable. Repair (stop the console first): "
            f"copy {g / 'index'} to a backup; if {g / 'index.lock'} exists, delete it only after "
            f"making sure no git or kb process is running; then "
            f"`mv {g / 'index'} {g / 'index.bad'}` and `git -C {repo} reset -q`.")


def _uncommitted_work(repo: Path) -> list[str]:
    """Work-item files edited but not committed. Export reads the working tree, so such an
    edit would reach the board without the state machine (a hand-set `status: done`).
    Raises ``KbIndexCorrupt`` when git cannot read the index."""
    r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-uall", "--",
                        "kb/initiatives", "kb/epics", "kb/tasks"],
                       capture_output=True, text=True, env=_env("sync:kb"), encoding="utf-8", errors="replace")
    if r.returncode != 0 and _INDEX_CORRUPT.search(r.stderr or ""):
        raise KbIndexCorrupt((r.stderr or "").strip()[-300:])
    # porcelain paths are relative to the git ROOT. A KB that is a subdirectory of a larger
    # repository (a project's docs/ in docs_mode=plain, ADR-2315) reports `docs/kb/tasks/…`;
    # strip that prefix, or such an edit is never seen (spike T-0350, fixed in T-0365)
    pre = subprocess.run(["git", "-C", str(repo), "rev-parse", "--show-prefix"], capture_output=True,
                         text=True, env=_env("sync:kb"), encoding="utf-8", errors="replace").stdout.strip()

    def _rel(path: str) -> str:
        path = path.split(" -> ")[-1].strip('"')
        return path[len(pre):] if pre and path.startswith(pre) else path

    # exactly what `kb export` reads (direct *.md children) — a scratch file is no work item
    return [p for p in (_rel(ln[3:]) for ln in r.stdout.splitlines() if ln.strip()) if _WORK_FILE.match(p)]


def _run(repo: Path, *args: str, actor: str = "sync:kb", timeout: int = 120,
         engine: Optional[Path] = None, guard_git: bool = False) -> tuple[int, Any]:
    """Run ``kb.py --repo <repo> <args>``. *engine* (a ``kb.py`` path) defaults to the repo's own
    copy; a PROJECT knowledge base is always run by the bound KB's engine instead (ADR-2316
    Decision 8): no code from a project directory is executed by the console."""
    env = _env(actor)
    if guard_git:       # a PROJECT folder's own git config must not run anything through the engine's git calls (review round 9)
        from core.projects import changes  # noqa: PLC0415
        env = changes.neutral_git_env(repo, env)
    script = engine if engine is not None else repo / "scripts" / "kb.py"
    r = subprocess.run([sys.executable, str(script), "--repo", str(repo), *args],
                       capture_output=True, text=True, timeout=timeout, env=env, encoding="utf-8", errors="replace")
    out = r.stdout.strip() or r.stderr.strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {"raw": out[-500:]}
    if r.returncode == 2 and not data:
        data = {"error": r.stderr.strip()[-500:]}
    return r.returncode, data


def fingerprint(repo: Path) -> str:
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout.strip()
    parts = [head]
    for d in ("initiatives", "epics", "tasks"):
        base = repo / "kb" / d
        if base.is_dir():
            for p in sorted(base.glob("*.md")):
                st = p.stat()
                parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
    parts += _uncommitted_work(repo)   # committing (or reverting) an edit is a change of state too
    # sha256, not hash(): str hashes are salted per process, and this value is persisted
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _state_path(tenant_id: str) -> Path:
    return store.db_path(tenant_id).parent / "kb_projection.json"


def _save(tenant_id: str, st: dict[str, Any]) -> None:
    if tenant_id in _periodic_out:
        st = {**st, "periodic": _periodic_out[tenant_id]}
    _state[tenant_id] = st
    try:
        p = _state_path(tenant_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        log.debug("kb projection state not persisted", exc_info=True)


def status(tenant_id: str) -> dict[str, Any]:
    if tenant_id in _state:
        return {**_state[tenant_id], "code": code_state()}
    try:
        return {**json.loads(_state_path(tenant_id).read_text(encoding="utf-8")), "code": code_state()}
    except (OSError, ValueError):
        return {"state": "off" if kb_repo(tenant_id) is None else "idle", "code": code_state()}


def sync(tenant_id: str, *, force: bool = False) -> dict[str, Any]:
    """One projector tick. Serialised: the loop and a transition never interleave.

    An unreadable KB index (``KbIndexCorrupt``, from any git status inside the tick) is
    turned into ONE logged ERROR plus an ``error`` state naming the repair, never an
    exception: ``transition``/``create_item`` call this after their KB write was applied."""
    try:
        return _sync(tenant_id, force=force)
    except KbIndexCorrupt as exc:
        repo = kb_repo(tenant_id)
        with _tenant_lock(tenant_id):
            if tenant_id not in _index_corrupt:
                log.error("kb projection paused: %s (git: %s)",
                          index_repair_hint(repo) if repo else "KB index unreadable", exc)
            _index_corrupt[tenant_id] = (time.monotonic() + KB_INDEX_RETRY_S, str(exc))
            prev = _state.get(tenant_id) or {}
            # fingerprint None: the first readable tick after the repair re-exports (a
            # `git reset` changes neither HEAD nor any KB file, so the old fingerprint
            # would match and keep this error state forever)
            st = {**{k: v for k, v in prev.items() if k not in ("state", "fingerprint")},
                  "state": "error", "fingerprint": None,
                  "error": f"KB git index corrupt: {exc}"[:300],
                  "repair": index_repair_hint(repo) if repo else None,
                  "checked_at": time.time()}
            _save(tenant_id, st)
            return st


def _sync(tenant_id: str, *, force: bool = False) -> dict[str, Any]:
    repo = kb_repo(tenant_id)
    if (_uses_tenant_kb(tenant_id)
            and tenant_id not in _kg_ensured and kg_plugin_state(tenant_id) == "enabled"):
        # the tenant's own KB: create it on the first tick after the plugin was enabled, and
        # refresh its engine once per process (a plugin update ships a newer kb.py)
        failed = _kg_failed.get(tenant_id)
        if failed is not None and failed[0] > time.monotonic():
            ens = failed[1]                     # back-off: the last outcome stands for now
        else:
            ens = ensure_tenant_kb(tenant_id)
        repo = kb_repo(tenant_id)
        if repo is None:
            st = {"state": "error", "error": f"tenant knowledge base: {ens.get('error') or ens.get('status')}",
                  "checked_at": time.time()}
            _save(tenant_id, st)
            return st
    if repo is None:
        st = {"state": "off", "reason": "no Corvin-Knowledge checkout, or a sandboxed CORVIN_HOME (set CORVIN_KB_REPO)"}
        if kg_plugin_state(tenant_id) == "installed":
            st = {"state": "off", "hint": "enable_plugin",
                  "reason": "the Knowledge Graph plugin is installed but not enabled — enable it in the "
                            "Marketplace to start this tenant's knowledge base"}
        _save(tenant_id, st)
        return st
    with _tenant_lock(tenant_id):
        prev = _state.get(tenant_id) or {}
        bad = _index_corrupt.get(tenant_id)
        if bad is not None and not force and bad[0] > time.monotonic():
            return prev                         # reported once; re-checked after the back-off
        fp = fingerprint(repo)                  # KbIndexCorrupt -> sync() reports it
        if bad is not None:
            log.warning("kb projection resumed: the KB checkout's git index is readable again")
            _index_corrupt.pop(tenant_id, None)
        now = time.time()
        if not force and prev.get("fingerprint") == fp:
            # Unchanged KB. The last GOOD projection is kept in every state (ok, blocked, held),
            # so drift — a store write behind the KB's back — is repaired in all of them. A
            # blocked/held state is not re-run: that wrote one projection_blocked record per tick.
            d = projection.diff(tenant_id, prev["payload"]) if prev.get("payload") else []
            if not d:
                prev["checked_at"] = now
                return prev
            log.warning("kb projection drift: %d difference(s) with an unchanged KB — healing", len(d))
            res = projection.apply(tenant_id, prev["payload"], drift_healed=len(d))
            st = {**prev, **{k: v for k, v in res.items() if k not in ("remaining", "state")},
                  "remaining": res["remaining"], "checked_at": now,
                  "state": res["state"] if prev.get("state") in (None, "ok", "diverged") else prev["state"],
                  "drift_total": prev.get("drift_total", 0) + len(d)}
            _save(tenant_id, st)
            return st
        rc_h, healed = _run(repo, "heal")
        rc, payload = _run(repo, "export")
        if rc != 0 or "items" not in payload:
            st = {"state": "error", "error": payload.get("error") or payload.get("raw") or f"export rc={rc}",
                  "fingerprint": None, "checked_at": now, "payload": prev.get("payload")}
            _save(tenant_id, st)
            return st
        fp = fingerprint(repo) if healed.get("commit") else fp
        # what waits for a human (escalated reviews, human-required decisions, incidents): the board
        # banner reads it from /kb/status, so it follows every KB change like the projection does
        rc_q, queue = _run(repo, "queue")
        base = {"fingerprint": fp, "checked_at": now, "drift_total": prev.get("drift_total", 0),
                "healed": healed.get("fixed", 0) if rc_h == 0 else None,
                "human_queue": queue if rc_q == 0 and isinstance(queue.get("queue"), list) else None}
        if not payload.get("ok"):
            # red wins over held: an inconsistent KB is reported first (one record per state)
            res = projection.apply(tenant_id, payload)
            st = {**res, **base, "payload": prev.get("payload")}
            _save(tenant_id, st)
            return st
        pending = _uncommitted_work(repo)
        if pending:
            # `kb export` reads the working tree: a hand-edited, uncommitted status would reach
            # the board past the state machine. Hold the last good projection (drift is still
            # repaired against it) and pause board moves until the edit is committed.
            st = {**{k: v for k, v in prev.items() if k not in ("error",)}, **base, "state": "held",
                  "pending_uncommitted": pending[:20], "payload": prev.get("payload")}
            _save(tenant_id, st)
            return st
        res = projection.apply(tenant_id, payload)
        _run(repo, "index")   # kb/graph (Knowledge Graph panel) follows every KB change
        st = {**res, **base, "payload": payload, "items": len(payload["items"])}
        _save(tenant_id, st)
        return st


# --- project knowledge bases (ADR-2316, T-0347) ---------------------------------------
#
# A project (ADR-2315) is a directory the user owns; the registry ``<tenant_home>/projects/
# registry.json`` only indexes it. A project with the ``kb`` feature has its KB in ``docs/``.
# Each one is projected as its own SCOPE (``core.task_tracking.projection``): its cards carry
# ``prj-<id>``, and its orphan pass never sees another KB's cards. Read-only on the project:
# export only — no heal, no index, nothing committed into the user's repository — and always
# with the bound KB's engine (no code from the project directory runs here). The registry
# WRITER arrives with ADR-2315 (T-0351); until then a missing registry is "no projects".

MAX_PROJECT_KBS = 16          # per tick; more projects are visited round-robin
_PROJECT_ID = __import__("re").compile(r"^[a-z0-9][a-z0-9-]{0,27}$")
_project_state: dict[str, dict[str, Any]] = {}
_project_cursor: dict[str, int] = {}


def project_registry_path(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home  # honours CORVIN_HOME

    return tenant_home(tenant_id) / "projects" / "registry.json"


def _project_manifest(root: Path) -> dict[str, Any]:
    try:
        import yaml  # noqa: PLC0415

        data = yaml.safe_load((root / "project.yaml").read_text(encoding="utf-8")) or {}
    except Exception:  # noqa: BLE001 -- unreadable manifest: not a project this tick
        return {}
    return data if isinstance(data, dict) else {}


def project_kbs(tenant_id: str) -> list[tuple[str, Path]]:
    """``[(project_id, docs_path)]`` of this tenant's registered projects whose manifest names
    this tenant and turns ``kb`` on, and whose ``docs/`` is a knowledge base. Anything else is
    skipped (logged at debug), never guessed: a path resolving into ANOTHER tenant's home, a
    manifest of another tenant, a duplicate id."""
    try:
        reg = json.loads(project_registry_path(tenant_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    entries = reg.get("projects") if isinstance(reg, dict) else None
    if not isinstance(entries, list):
        return []
    try:
        own = tenant_kb_path(tenant_id).parent.resolve()
    except OSError:
        return []
    tenants_root = own.parent
    out: list[tuple[str, Path]] = []
    seen: set[str] = set()
    for e in entries:
        pid = e.get("id") if isinstance(e, dict) else None
        raw = e.get("path") if isinstance(e, dict) else None
        if not isinstance(pid, str) or not _PROJECT_ID.match(pid) or pid in seen or not isinstance(raw, str):
            continue
        try:
            root = Path(raw).expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if root.is_relative_to(tenants_root) and not root.is_relative_to(own):
            log.debug("project %s of %s resolves into another tenant's home: skipped", pid, tenant_id)
            continue
        man = _project_manifest(root)
        feats = man.get("features") if isinstance(man.get("features"), dict) else {}
        if man.get("tenant") != tenant_id or feats.get("kb") is not True:
            continue
        docs = root / "docs"
        if not (docs / "kb" / "_meta" / "sources.yaml").is_file():
            continue
        seen.add(pid)
        out.append((pid, docs))
    return out


def project_status(tenant_id: str) -> dict[str, Any]:
    """The per-project projection states without their payloads (for /kb/status)."""
    st = _project_state.get(tenant_id)
    if st is None:
        try:
            st = json.loads(_project_state_path(tenant_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            st = {}
    return {k: {kk: vv for kk, vv in v.items() if kk != "payload"} if isinstance(v, dict) else v
            for k, v in st.items()}


def _project_state_path(tenant_id: str) -> Path:
    return store.db_path(tenant_id).parent / "kb_projection_projects.json"


def _save_projects(tenant_id: str, st: dict[str, Any]) -> None:
    _project_state[tenant_id] = st
    try:
        p = _project_state_path(tenant_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str), encoding="utf-8")
        os.replace(tmp, p)
    except OSError:
        log.debug("project projection state not persisted", exc_info=True)


def _sync_one_project(tenant_id: str, scope: str, repo: Path, engine: Path,
                      prev: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    fp = fingerprint(repo)                          # KbIndexCorrupt -> caller records an error
    if prev.get("fingerprint") == fp and prev.get("payload"):
        d = projection.diff(tenant_id, prev["payload"])
        if not d:
            return {**prev, "checked_at": now}
        res = projection.apply(tenant_id, prev["payload"], drift_healed=len(d))
        return {**prev, **{k: v for k, v in res.items() if k != "remaining"}, "checked_at": now,
                "drift_total": prev.get("drift_total", 0) + len(d)}
    rc, payload = _run(repo, "export", engine=engine)
    if rc != 0 or "items" not in payload:
        return {"state": "error", "error": str(payload.get("error") or payload.get("raw") or f"export rc={rc}")[:300],
                "fingerprint": None, "checked_at": now, "payload": prev.get("payload")}
    payload["scope"] = scope
    if not payload.get("ok"):
        if prev.get("state") == "blocked" and prev.get("fingerprint") == fp:
            return {**prev, "checked_at": now}
        res = projection.apply(tenant_id, payload)  # one projection_blocked record, nothing else
        return {**res, "fingerprint": fp, "checked_at": now, "payload": prev.get("payload")}
    pending = _uncommitted_work(repo)
    if pending:
        return {**{k: v for k, v in prev.items() if k != "error"}, "state": "held", "fingerprint": fp,
                "pending_uncommitted": pending[:20], "checked_at": now, "payload": prev.get("payload")}
    res = projection.apply(tenant_id, payload)
    return {**res, "fingerprint": fp, "checked_at": now, "payload": payload, "items": len(payload["items"]),
            "drift_total": prev.get("drift_total", 0)}


def sync_projects(tenant_id: str, *, busy: bool = False) -> dict[str, Any]:
    """One projector pass over the tenant's project KBs (ADR-2316). Each project is isolated:
    its error or red KB is recorded for that scope only and never stops the bound KB or another
    project. Skipped entirely on a saturated host (*busy*): project boards are optional, the
    bound KB is not."""
    if busy:
        return {"skipped": "host saturated"}
    kbs = project_kbs(tenant_id)
    prev_all = dict(_project_state.get(tenant_id) or {})
    if not prev_all:
        try:
            prev_all = json.loads(_project_state_path(tenant_id).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            prev_all = {}
    if not kbs:
        if prev_all:
            _save_projects(tenant_id, {})
        return {"projects": 0}
    bound = kb_repo(tenant_id)
    engine = bound / "scripts" / "kb.py" if bound is not None and _is_kb(bound) else None
    if engine is None:
        st = {pid: {"state": "off", "reason": "no bound knowledge base whose engine could read it"}
              for pid, _ in kbs}
        _save_projects(tenant_id, st)
        return {"projects": len(kbs), "state": "off"}
    start_at = _project_cursor.get(tenant_id, 0) % len(kbs)
    window = (kbs[start_at:] + kbs[:start_at])[:MAX_PROJECT_KBS]
    _project_cursor[tenant_id] = (start_at + len(window)) % len(kbs)
    live = {pid for pid, _ in kbs}
    out = {pid: v for pid, v in prev_all.items() if pid in live}
    for scope, repo in window:
        with _tenant_lock(tenant_id):
            try:
                out[scope] = _sync_one_project(tenant_id, scope, repo, engine, out.get(scope) or {})
            except Exception as exc:  # noqa: BLE001 -- one project's failure stays its own
                out[scope] = {"state": "error", "error": f"{type(exc).__name__}: {exc}"[:300],
                              "fingerprint": None, "checked_at": time.time(),
                              "payload": (out.get(scope) or {}).get("payload")}
    _save_projects(tenant_id, out)
    return {"projects": len(kbs), "visited": [s for s, _ in window]}


def _scope_of_item(cur: dict[str, Any]) -> Optional[str]:
    labels = cur.get("labels") if isinstance(cur.get("labels"), list) else []
    for x in labels:
        if isinstance(x, str) and x.startswith(projection.SCOPE_PREFIX):
            return x[len(projection.SCOPE_PREFIX):]
    return None


def _write_target(tenant_id: str, scope: Optional[str]) -> tuple[Path, Optional[Path]]:
    """``(repo, engine)`` a write for *scope* goes to (ADR-2316 Decision 5). ``None``: the bound KB
    with its own engine. A project scope: the REGISTERED project's ``docs/`` (never a path from a
    request) run by the bound KB's engine. Refused when the project is not registered with ``kb``
    on, or no bound engine exists."""
    bound = kb_repo(tenant_id)
    if bound is None:
        raise service.TaskTrackingError("no knowledge base is bound to this tenant")
    if scope is None:
        return bound, None
    if not _is_kb(bound):
        raise KbTransitionRefused("project boards need the bound knowledge base's engine, which is missing")
    for pid, docs in project_kbs(tenant_id):
        if pid == scope:
            return docs, bound / "scripts" / "kb.py"
    raise KbTransitionRefused(f"project {scope!r} is not registered with the kb feature on for this tenant")


def sync_project(tenant_id: str, scope: str) -> dict[str, Any]:
    """Re-project ONE project scope now (after a board write into it)."""
    repo, engine = _write_target(tenant_id, scope)
    prev_all = dict(_project_state.get(tenant_id) or {})
    with _tenant_lock(tenant_id):
        prev = {k: v for k, v in (prev_all.get(scope) or {}).items() if k != "fingerprint"}
        prev_all[scope] = _sync_one_project(tenant_id, scope, repo, engine, prev)
    _save_projects(tenant_id, prev_all)
    return prev_all[scope]


def transition(tenant_id: str, item_id: str, to_status: str, *, reason: str = "", dod: str = "",
               actor: str = "operator", sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    if kb_repo(tenant_id) is None:
        raise service.TaskTrackingError("no knowledge base is bound to this tenant")
    with store.connect(tenant_id) as conn:
        cur = service._fetch(conn, tenant_id, item_id)
    ref = str(cur.get("external_ref") or "")
    if not ref.startswith(service.KB_REF_PREFIX):
        raise service.TaskTrackingError("not a knowledge-base item — use a normal update")
    scope = _scope_of_item(cur)
    repo, engine = _write_target(tenant_id, scope)
    if cur["kind"] != "task":
        raise KbTransitionRefused(f"a {cur['kind']}'s status is derived from its children — move its tasks")
    to_kb = projection.STATUS_TO_KB.get(to_status, to_status)
    details = {"item_id": item_id, "kind": cur["kind"], "from_status": cur["status"], "to_status": to_status,
               "actor_kind": actor.split(":")[0], **({"sid_fingerprint": sid_fingerprint} if sid_fingerprint else {}),
               **({"scope": scope} if scope else {})}
    try:
        pending = _uncommitted_work(repo)
    except KbIndexCorrupt:
        raise KbTransitionRefused(f"board moves are paused: {index_repair_hint(repo)}") from None
    if pending:
        # while held the board cannot show a move (it projects only committed state), so a
        # move is refused with the reason instead of answering 200 for a card that stays put
        raise KbTransitionRefused(f"board moves are paused: uncommitted knowledge-base edits {pending[:5]}")
    service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "requested"})  # audit-first
    # `--opt=value`: a reason starting with "-" can never be read as another option
    args = ["task", ref.split(":", 1)[1], to_kb, "--via=console", f"--actor={actor}"]
    if reason:
        args.append(f"--reason={reason}")
    if dod:
        args.append(f"--dod={dod}")
    try:
        rc, res = _run(repo, *args, actor=actor, engine=engine)
    except Exception:
        service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "error"})
        raise
    if rc != 0:
        service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "refused"})
        raise KbTransitionRefused(str(res.get("error") or res)[:500])
    service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "applied"})
    if scope:
        sync_project(tenant_id, scope)
    else:
        sync(tenant_id, force=True)
    with store.connect(tenant_id) as conn:
        return service._fetch(conn, tenant_id, item_id)


def _skill_registry(tenant_id: str):
    """Tenant-native SkillForge registry acting for the `assistant` persona (the namespace
    gate then refuses any name outside `assistant.*`). Same import path as the CEL's
    explicit-skill stage: skill-forge and forge are on neither host's sys.path by default."""
    base = _REPO_ROOT / "corvin_operator"
    for d in (base / "skill-forge", base / "forge"):
        if str(d) not in sys.path:
            sys.path.insert(0, str(d))
    from skill_forge.multi_registry import MultiSkillRegistry  # noqa: PLC0415
    return MultiSkillRegistry(tenant_id=tenant_id, caller_persona=GUIDANCE_PERSONA)


def guidance_skill_name(finding_class: str) -> str:
    import re
    return f"{GUIDANCE_PERSONA}.kb_guidance_" + re.sub(r"[^a-z0-9_]", "_", finding_class.lower())


def guidance_bridge(tenant_id: str, repo: Path) -> dict[str, Any]:
    """Mint/retire the SkillForge skills of KB guidance (T-0049). A skill is created once,
    graded once with the capped bootstrap seed (organic=False clamps to the cap; disclosed
    in the notes), then acknowledged in the KB. A failure leaves the guidance `pending`,
    so the next period retries — nothing is acknowledged that did not happen."""
    rc, pend = _run(repo, "guidance", "pending")
    if rc != 0:
        return {"error": str(pend.get("error") or pend)[:300]}
    if not pend.get("mint") and not pend.get("retire"):
        return {"minted": [], "retired": []}
    reg = _skill_registry(tenant_id)
    minted, retired, failed = [], [], []
    for m in pend.get("mint") or []:
        name = guidance_skill_name(m["finding_class"])
        try:
            spec = reg.get(name)
            if spec is None:
                spec = reg.create(scope="user", name=name, type="learned-experience", body_md=m["body_md"],
                                  description=f"Corvin-Knowledge guidance for the recurring finding class "
                                              f"{m['finding_class']} (advisory, retired when it shows no effect)",
                                  created_by="kb-guidance")
            if not list(getattr(spec, "grades", None) or []):
                # graded whenever it has no grade yet — not only right after create: a grade that
                # failed once left an ungraded (never injected) skill acked as minted
                reg.grade(name, run_id=f"kb-guidance-bootstrap:{m['finding_class']}", score=0.3,
                          notes="bootstrap seed minted from KB guidance — not earned usage", organic=False)
        except Exception as exc:  # noqa: BLE001 — one bad entry must not stop the others
            failed.append({"finding_class": m["finding_class"], "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        rc, res = _run(repo, "guidance", "ack", m["finding_class"], f"--skill={name}")
        (minted if rc == 0 else failed).append(name if rc == 0 else {"finding_class": m["finding_class"],
                                                                     "error": str(res)[:200]})
    for r in pend.get("retire") or []:
        try:
            if reg.get(r["skill"]) is not None:
                reg.delete(r["skill"], reason="KB guidance retired (no effect, or blamed for a false refusal)")
        except Exception as exc:  # noqa: BLE001
            failed.append({"skill": r["skill"], "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        rc, res = _run(repo, "guidance", "ack", r["finding_class"], "--retired")
        (retired if rc == 0 else failed).append(r["skill"] if rc == 0 else {"skill": r["skill"],
                                                                            "error": str(res)[:200]})
    return {"minted": minted, "retired": retired, **({"failed": failed} if failed else {})}


def periodic(tenant_id: str, *, force: bool = False) -> dict[str, Any]:
    """The KB's slow loops, every PERIODIC_S (T-0044/T-0049). Serialised with sync and
    transitions — all of them commit to the KB."""
    repo = kb_repo(tenant_id)
    now = time.time()
    if repo is None or (not force and now - _last_periodic.get(tenant_id, 0.0) < PERIODIC_S):
        return {}
    _last_periodic[tenant_id] = now
    out: dict[str, Any] = {}
    # The slow loops create tasks and trip breakers: they run only on a KB checkout that
    # contains origin/main. A checkout that lags behind runs OLD kb.py with OLD data (review
    # R2-C1: a stale checkout's first sweep regressed six tasks and tripped stop-all).
    behind = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", "refs/remotes/origin/main", "HEAD"],
                            capture_output=True, env=_env("sync:kb"))
    has_origin = subprocess.run(["git", "-C", str(repo), "rev-parse", "--verify", "-q", "refs/remotes/origin/main"],
                                capture_output=True, env=_env("sync:kb")).returncode == 0
    if has_origin and behind.returncode != 0:
        _periodic_out[tenant_id] = {"skipped": "the KB checkout does not contain origin/main — pull it", "at": now}
        _save(tenant_id, dict(_state.get(tenant_id) or status(tenant_id)))
        return _periodic_out[tenant_id]
    with _tenant_lock(tenant_id):
        rc, g = _run(repo, "guidance", "run", actor="kb-guidance")
        out["guidance"] = g if rc == 0 else {"error": str(g.get("error") or g)[:300]}
        rc, sw = _run(repo, "sweep", "--create-tasks", actor="kb-sweep", timeout=300)
        out["sweep"] = sw if rc == 0 else {"error": str(sw.get("error") or sw)[:300]}
        try:
            out["skills"] = guidance_bridge(tenant_id, repo)
        except Exception as exc:  # noqa: BLE001 — SkillForge unavailable: report, retry next period
            out["skills"] = {"error": f"{type(exc).__name__}: {exc}"[:300]}
    _periodic_out[tenant_id] = {**out, "at": now}
    _save(tenant_id, dict(_state.get(tenant_id) or status(tenant_id)))
    return out


#: on a saturated host the projector ticks at most this often (each tick spawns git)
SATURATED_TICK_S = 30.0


def _loop(tenant_id: str, stop: threading.Event) -> None:
    last_tick = 0.0
    while not stop.is_set():
        try:
            from corvin_console.loop_health import host_saturated as _saturated  # noqa: PLC0415
            busy = _saturated()
        except Exception:  # noqa: BLE001
            busy = False
        if busy and time.monotonic() - last_tick < SATURATED_TICK_S:
            stop.wait(TICK_S)
            continue
        last_tick = time.monotonic()
        try:
            sync(tenant_id)
        except Exception as exc:  # noqa: BLE001 — the loop must survive; the status says what broke
            log.warning("kb projection tick failed: %s", exc)
            _save(tenant_id, {**(_state.get(tenant_id) or {}), "state": "error", "error": str(exc)[:300],
                              "checked_at": time.time()})
        try:
            sync_projects(tenant_id, busy=busy)
        except Exception as exc:  # noqa: BLE001 -- a project pass never stops the bound KB's loop
            log.warning("kb project projection failed: %s", exc)
        try:
            periodic(tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("kb periodic loop failed: %s", exc)
        stop.wait(TICK_S)


def start(tenant_id: Optional[str] = None) -> bool:
    """Start the projector thread once per process and tenant (False: already running, or a
    pytest process without CORVIN_KB_REPO)."""
    t = tenant_id or kb_tenant()
    # A test run must never heal-and-commit into the operator's real KB checkout;
    # tests that want a projector point CORVIN_KB_REPO at a fixture repo.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("CORVIN_KB_REPO"):
        return False
    # Started even with no KB yet: on a fresh install the Knowledge Graph plugin is provisioned
    # AFTER this call (default_plugins runs in its own thread) and enabled whenever the operator
    # consents — the tick then creates the tenant KB, no restart needed. Without a KB a tick is
    # one cached registry read; it never resolves the operator's sibling KB on a sandboxed home.
    if t in _started:
        return False
    _started.add(t)
    # the slow loops start PERIODIC_S after boot: the first ticks belong to the projection
    # (a periodic run holds _lock for its subprocesses and would delay the first sync)
    _last_periodic.setdefault(t, time.time())
    _stops[t] = threading.Event()
    _threads[t] = threading.Thread(target=_loop, args=(t, _stops[t]), name=f"kb-projection-{t}", daemon=True)
    _threads[t].start()
    log.info("kb projection started for tenant %s (KB: %s)", t, kb_repo(t) or "none yet")
    return True


def process_tenant() -> str:
    """The tenant THIS host process serves (``CORVIN_TENANT_ID``, default ``_default``).

    The forge audit writer refuses any record whose tenant is not the process tenant
    (``AuditTenantMismatch``, ADR-0007 F-A6): every projection, transition and create writes
    ``task_item.*`` records, so a projector can only ever run for the process tenant. A second
    tenant is served by its own host process (its own ``CORVIN_TENANT_ID``) with its own projector."""
    return os.environ.get("CORVIN_TENANT_ID") or "_default"


def start_process_tenant() -> bool:
    """What every host calls at boot: the projector of the process tenant. It binds that tenant's
    KB only (``kb_repo(tenant)``): the operator's KB when the process tenant is ``CORVIN_KB_TENANT``,
    otherwise the tenant's own ``<tenant_home>/knowledge``."""
    return start(process_tenant())


def ensure_started(tenant_id: str) -> None:
    """Idempotent: a board/status request of the process tenant starts its projector if a host
    never did (a host that predates this call). Never for another tenant (see process_tenant)."""
    if tenant_id == process_tenant() and tenant_id not in _started:
        try:
            start(tenant_id)
        except Exception:  # noqa: BLE001 -- the status route answers without a projector too
            log.debug("kb projector start for %s failed", tenant_id, exc_info=True)


# --- creating work items from the console (the KB stays the one writer) ----------------

KB_KINDS = ("initiative", "epic", "task")
_KB_PARENT_KIND = {"epic": "initiative", "task": "epic"}


def _kb_ref_of(tenant_id: str, item_id: str) -> tuple[str, str, Optional[str]]:
    """(kind, uid, scope) of a projected KB item of this tenant; refuses a non-KB item."""
    with store.connect(tenant_id) as conn:
        cur = service._fetch(conn, tenant_id, item_id)
    ref = str(cur.get("external_ref") or "")
    if not ref.startswith(service.KB_REF_PREFIX):
        raise KbTransitionRefused("the parent is not a knowledge-base item")
    return cur["kind"], ref.split(":", 1)[1], _scope_of_item(cur)


def create_item(tenant_id: str, *, kind: str, title: str, parent_id: Optional[str] = None,
                description: str = "", dod: str = "", actor: str = "operator",
                sid_fingerprint: Optional[str] = None, scope: Optional[str] = None) -> dict[str, Any]:
    """Create an initiative / epic / task IN the tenant's knowledge base (``kb new``, the KB's one
    identity writer), re-project, and return the projected store item. The board gets no second
    writer: the store row appears only through the projection. A child goes into its parent's
    KB (ADR-2316: the parent's ``prj-*`` scope); an initiative goes into *scope* (a registered
    project id) or, without one, into the bound KB."""
    if kb_repo(tenant_id) is None:
        raise service.TaskTrackingError("no knowledge base is bound to this tenant")
    if kind not in KB_KINDS:
        raise KbTransitionRefused(f"the knowledge base holds {list(KB_KINDS)}, not {kind!r}")
    # `--opt=value`: a title starting with "-" is never read as another option
    args = ["new", kind, f"--title={title}"]
    want_parent = _KB_PARENT_KIND.get(kind)
    if want_parent:
        if not parent_id:
            raise KbTransitionRefused(f"a {kind} needs a parent {want_parent} (the knowledge base's hierarchy)")
        pkind, puid, pscope = _kb_ref_of(tenant_id, parent_id)
        if pkind != want_parent:
            raise KbTransitionRefused(f"a {kind}'s parent must be a {want_parent}, not a {pkind}")
        if scope is not None and scope != pscope:
            raise KbTransitionRefused("a child goes into its parent's knowledge base")
        scope = pscope
        repo, engine = _write_target(tenant_id, scope)
        rc_s, shown = _run(repo, "show", puid, engine=engine)
        pid = shown.get("id") if rc_s == 0 and isinstance(shown.get("id"), str) else puid
        args.append(f"--{want_parent}={pid}")
    elif parent_id:
        raise KbTransitionRefused("an initiative has no parent")
    else:
        repo, engine = _write_target(tenant_id, scope)
    if dod and kind == "task":
        args.append(f"--dod={dod}")
    if description:
        args.append(f"--body={description}")
    args.append(f"--actor={actor}")
    details = {"kind": kind, "actor_kind": actor.split(":")[0],
               **({"sid_fingerprint": sid_fingerprint} if sid_fingerprint else {}),
               **({"scope": scope} if scope else {})}
    service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "requested"})  # audit-first
    with _tenant_lock(tenant_id):
        try:
            pending = _uncommitted_work(repo)
        except KbIndexCorrupt:
            service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "refused"})
            raise KbTransitionRefused(f"board writes are paused: {index_repair_hint(repo)}") from None
        if pending:
            service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "refused"})
            raise KbTransitionRefused(f"board writes are paused: uncommitted knowledge-base edits {pending[:5]}")
        try:
            rc, res = _run(repo, *args, actor=actor, engine=engine)
        except Exception:
            service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "error"})
            raise
    if rc != 0 or not res.get("uid"):
        service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "refused"})
        raise KbTransitionRefused(str(res.get("error") or res)[:500])
    service._chain(tenant_id, "task_item.kb_create", {**details, "outcome": "applied",
                                                      "kb_id": str(res.get("id") or "")})
    if scope:
        pst = sync_project(tenant_id, scope)
    else:
        pst = sync(tenant_id, force=True)
    item = projection.kb_items(tenant_id, scope=scope).get(f"{service.KB_REF_PREFIX}{res['uid']}")
    if item is None:
        # created in the KB, but the projection did not take it (a red check holds the board)
        raise service.TaskTrackingError(f"{res.get('id')} was created in the knowledge base but is not on the "
                                        f"board yet (projector: {pst.get('state')})")
    with store.connect(tenant_id) as conn:
        return service._fetch(conn, tenant_id, item["id"])


def stop(tenant_id: Optional[str] = None, *, join_s: float = 30.0) -> None:
    """Stop the projector thread and WAIT for its tick to end (tests; a host shutting down). It
    may be started again. Returning before the thread ended let a tick that was already running
    finish against whatever environment the caller restored next — in a test, the process-level
    CORVIN_HOME, i.e. the operator's live chain (review 2026-10-21)."""
    t = tenant_id or kb_tenant()
    ev = _stops.pop(t, None)
    if ev is not None:
        ev.set()
    th = _threads.pop(t, None)
    if th is not None and th is not threading.current_thread():
        th.join(timeout=join_s)
    _started.discard(t)
