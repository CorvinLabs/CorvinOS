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
PERIODIC_S = 600.0        # G6 sweep + guidance + SkillForge bridge: periodic, never per tick
GUIDANCE_PERSONA = "assistant"   # SkillForge namespace the guidance skills are minted under
_last_periodic: dict[str, float] = {}
_periodic_out: dict[str, dict[str, Any]] = {}   # survives the per-tick state replacement
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


_WORK_FILE = __import__("re").compile(r"^kb/(initiatives|epics|tasks)/[^/]+\.md$")
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
    r = subprocess.run(["git", "-C", str(repo), "status", "--porcelain", "-uall", "--",
                        "kb/initiatives", "kb/epics", "kb/tasks"],
                       capture_output=True, text=True, env=_env("sync:kb"))
    # exactly what `kb export` reads (direct *.md children) — a scratch file is no work item
    return [p for p in (ln[3:].strip('"') for ln in r.stdout.splitlines() if ln.strip())
            if _WORK_FILE.match(p.split(" -> ")[-1])]


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
        base = {"fingerprint": fp, "checked_at": now, "drift_total": prev.get("drift_total", 0),
                "healed": healed.get("fixed", 0) if rc_h == 0 else None}
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
    pending = _uncommitted_work(repo)
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
    repo = kb_repo()
    now = time.time()
    if repo is None or (not force and now - _last_periodic.get(tenant_id, 0.0) < PERIODIC_S):
        return {}
    _last_periodic[tenant_id] = now
    out: dict[str, Any] = {}
    with _lock:
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


def _loop(tenant_id: str, stop: threading.Event) -> None:
    while not stop.is_set():
        try:
            sync(tenant_id)
        except Exception as exc:  # noqa: BLE001 — the loop must survive; the status says what broke
            log.warning("kb projection tick failed: %s", exc)
            _save(tenant_id, {**(_state.get(tenant_id) or {}), "state": "error", "error": str(exc)[:300],
                              "checked_at": time.time()})
        try:
            periodic(tenant_id)
        except Exception as exc:  # noqa: BLE001
            log.warning("kb periodic loop failed: %s", exc)
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
    # the slow loops start PERIODIC_S after boot: the first ticks belong to the projection
    # (a periodic run holds _lock for its subprocesses and would delay the first sync)
    _last_periodic.setdefault(t, time.time())
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
