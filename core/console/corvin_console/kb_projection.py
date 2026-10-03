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

The KB is located by ``CORVIN_KB_REPO`` or, by default, the ``Corvin-Knowledge``
checkout next to this repo. No KB -> the projector is off and says so in its status.
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
_REPO_ROOT = Path(__file__).resolve().parents[3]
_lock = threading.Lock()
_state: dict[str, dict[str, Any]] = {}
_started: set[str] = set()
_stops: dict[str, threading.Event] = {}


class KbTransitionRefused(service.TaskTrackingError):
    """The KB state machine refused the move (-> HTTP 409)."""


def kb_repo() -> Optional[Path]:
    env = os.environ.get("CORVIN_KB_REPO")
    p = Path(env) if env else _REPO_ROOT.parent / "Corvin-Knowledge"
    return p if (p / "scripts" / "kb.py").is_file() and (p / "kb" / "_meta" / "sources.yaml").is_file() else None


def kb_tenant() -> str:
    return os.environ.get("CORVIN_KB_TENANT", "_default")


_ENV_KEEP = ("PATH", "HOME", "LANG", "LC_ALL", "TZ", "TMPDIR", "SSH_AUTH_SOCK", "GIT_SSH_COMMAND")


def _env(actor: str) -> dict[str, str]:
    """A minimal environment for KB subprocesses: the console's own secrets (API keys,
    tokens in os.environ) are never handed to code read from the KB repository."""
    env = {k: os.environ[k] for k in _ENV_KEEP if k in os.environ}
    env.update(KB_ACTOR=actor, KB_ACTOR_NAME=actor, KB_ACTOR_EMAIL="kb@corvin.local",
               PYTHONDONTWRITEBYTECODE="1", GIT_TERMINAL_PROMPT="0")
    return env


def _uncommitted_work(repo: Path) -> list[str]:
    """Work-item files edited but not committed. Export reads the working tree, so such an
    edit would reach the board without the state machine (a hand-set `status: done`)."""
    r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "--", "kb/initiatives", "kb/epics", "kb/tasks"],
                       capture_output=True, text=True, env=_env("sync:kb"))
    return [ln[3:] for ln in r.stdout.splitlines() if ln.strip()]


def _run(repo: Path, *args: str, actor: str = "sync:kb", timeout: int = 120) -> tuple[int, Any]:
    env = _env(actor)
    r = subprocess.run([sys.executable, str(repo / "scripts" / "kb.py"), "--repo", str(repo), *args],
                       capture_output=True, text=True, timeout=timeout, env=env)
    out = r.stdout.strip() or r.stderr.strip()
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        data = {"raw": out[-500:]}
    if r.returncode == 2 and not data:
        data = {"error": r.stderr.strip()[-500:]}
    return r.returncode, data


def fingerprint(repo: Path) -> str:
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    parts = [head]
    for d in ("initiatives", "epics", "tasks"):
        base = repo / "kb" / d
        if base.is_dir():
            for p in sorted(base.glob("*.md")):
                st = p.stat()
                parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
    # sha256, not hash(): str hashes are salted per process, and this value is persisted
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()[:32]


def _state_path(tenant_id: str) -> Path:
    return store.db_path(tenant_id).parent / "kb_projection.json"


def _save(tenant_id: str, st: dict[str, Any]) -> None:
    _state[tenant_id] = st
    try:
        p = _state_path(tenant_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(st, indent=1, default=str))
        os.replace(tmp, p)
    except OSError:
        log.debug("kb projection state not persisted", exc_info=True)


def status(tenant_id: str) -> dict[str, Any]:
    if tenant_id in _state:
        return _state[tenant_id]
    try:
        return json.loads(_state_path(tenant_id).read_text())
    except (OSError, ValueError):
        return {"state": "off" if kb_repo() is None else "idle"}


def sync(tenant_id: str, *, force: bool = False) -> dict[str, Any]:
    """One projector tick. Serialised: the loop and a transition never interleave."""
    repo = kb_repo()
    if repo is None:
        st = {"state": "off", "reason": "no Corvin-Knowledge checkout (set CORVIN_KB_REPO)"}
        _save(tenant_id, st)
        return st
    with _lock:
        prev = _state.get(tenant_id) or {}
        fp = fingerprint(repo)
        now = time.time()
        if not force and prev.get("fingerprint") == fp and prev.get("state") in ("blocked", "held"):
            # unchanged and still red/held: nothing to redo — re-running wrote one
            # projection_blocked audit record every tick (43 200 a day)
            prev["checked_at"] = now
            return prev
        if not force and prev.get("fingerprint") == fp and prev.get("payload"):
            d = projection.diff(tenant_id, prev["payload"])
            if not d:
                prev["checked_at"] = now
                return prev
            log.warning("kb projection drift: %d difference(s) with an unchanged KB — healing", len(d))
            res = projection.apply(tenant_id, prev["payload"], drift_healed=len(d))
            st = {**prev, **{k: v for k, v in res.items() if k != "remaining"}, "remaining": res["remaining"],
                  "checked_at": now, "drift_total": prev.get("drift_total", 0) + len(d)}
            _save(tenant_id, st)
            return st
        rc_h, healed = _run(repo, "heal")
        pending = _uncommitted_work(repo)
        if pending:
            # hold the last good projection (drift is still repaired against it) until the
            # edit is committed; the console banner names the files
            st = {**prev, "state": "held", "pending_uncommitted": pending[:20], "fingerprint": fp,
                  "checked_at": now}
            _save(tenant_id, st)
            return st
        rc, payload = _run(repo, "export")
        if rc != 0 or "items" not in payload:
            st = {"state": "error", "error": payload.get("error") or payload.get("raw") or f"export rc={rc}",
                  "fingerprint": None, "checked_at": now}
            _save(tenant_id, st)
            return st
        res = projection.apply(tenant_id, payload)
        if res["state"] != "blocked":
            _run(repo, "index")   # kb/graph (Knowledge Graph panel) follows every KB change
        st = {**{k: v for k, v in res.items()}, "fingerprint": fingerprint(repo) if healed.get("commit") else fp,
              "payload": payload if res["state"] != "blocked" else None, "checked_at": now,
              "healed": healed.get("fixed", 0) if rc_h == 0 else None,
              "drift_total": prev.get("drift_total", 0), "items": len(payload["items"])}
        _save(tenant_id, st)
        return st


def transition(tenant_id: str, item_id: str, to_status: str, *, reason: str = "", dod: str = "",
               actor: str = "operator", sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    repo = kb_repo()
    if repo is None:
        raise service.TaskTrackingError("no knowledge base on this host")
    if tenant_id != kb_tenant():
        # the projector serves ONE tenant; a move from another tenant would re-project there
        raise service.TaskTrackingError("the knowledge base is projected for another tenant")
    with store.connect(tenant_id) as conn:
        cur = service._fetch(conn, tenant_id, item_id)
    ref = str(cur.get("external_ref") or "")
    if not ref.startswith(service.KB_REF_PREFIX):
        raise service.TaskTrackingError("not a knowledge-base item — use a normal update")
    if cur["kind"] != "task":
        raise KbTransitionRefused(f"a {cur['kind']}'s status is derived from its children — move its tasks")
    to_kb = projection.STATUS_TO_KB.get(to_status, to_status)
    details = {"item_id": item_id, "kind": cur["kind"], "from_status": cur["status"], "to_status": to_status,
               "actor_kind": actor.split(":")[0], **({"sid_fingerprint": sid_fingerprint} if sid_fingerprint else {})}
    service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "requested"})  # audit-first
    # `--opt=value`: a reason starting with "-" can never be read as another option
    args = ["task", ref.split(":", 1)[1], to_kb, "--via=console", f"--actor={actor}"]
    if reason:
        args.append(f"--reason={reason}")
    if dod:
        args.append(f"--dod={dod}")
    try:
        rc, res = _run(repo, *args, actor=actor)
    except Exception:
        service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "error"})
        raise
    if rc != 0:
        service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "refused"})
        raise KbTransitionRefused(str(res.get("error") or res)[:500])
    service._chain(tenant_id, "task_item.kb_transition", {**details, "outcome": "applied"})
    sync(tenant_id, force=True)
    with store.connect(tenant_id) as conn:
        return service._fetch(conn, tenant_id, item_id)


def _loop(tenant_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            sync(tenant_id)
        except Exception as exc:  # noqa: BLE001 — the loop must survive; the status says what broke
            log.warning("kb projection tick failed: %s", exc)
            _save(tenant_id, {**(_state.get(tenant_id) or {}), "state": "error", "error": str(exc)[:300],
                              "checked_at": time.time()})
        stop.wait(TICK_S)


def start(tenant_id: Optional[str] = None) -> bool:
    """Start the projector thread once per process. False when there is no KB."""
    t = tenant_id or kb_tenant()
    # A test run must never heal-and-commit into the operator's real KB checkout;
    # tests that want a projector point CORVIN_KB_REPO at a fixture repo.
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("CORVIN_KB_REPO"):
        return False
    if kb_repo() is None or t in _started:
        return False
    _started.add(t)
    _stops[t] = threading.Event()
    threading.Thread(target=_loop, args=(t, _stops[t]), name=f"kb-projection-{t}", daemon=True).start()
    log.info("kb projection started for tenant %s from %s", t, kb_repo())
    return True


def stop(tenant_id: Optional[str] = None) -> None:
    """Stop the projector thread (tests; a host shutting down). It may be started again."""
    t = tenant_id or kb_tenant()
    ev = _stops.pop(t, None)
    if ev is not None:
        ev.set()
    _started.discard(t)
