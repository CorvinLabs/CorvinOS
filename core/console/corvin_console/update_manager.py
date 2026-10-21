"""Console client for the repository's own updater (update.sh / update.ps1).

The updater at the root of the install's source tree is the ONE updater
(verified swap, rollback, shared install/update lock -- ADR-2054, ADR-2215,
ADR-2214). This module only locates it, runs it detached, audits the run and
remembers when it last ran. It never copies or re-implements update logic.

The updater restarts the console, so it is started in its own session and the
request returns immediately; the outcome is read back from ``update_state.json``.

Traceability (ADR-2256 amendment): the updater reports each stage as one JSON line in
``update_progress.jsonl`` next to the state file (``CORVIN_UPDATE_PROGRESS``). A run only
counts as a success when ``evaluate_run`` can PROVE every link - delivered, UI rebuilt,
script exit 0, verified over the wire, installed commit current - and only then is
``last_success_at`` written. The script's own claims are cross-checked against the tree.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

# In the ``corvin.`` namespace: the hosts' journal/file handlers are attached
# to the ``corvin`` logger, so ``corvin_console.update_manager`` (the module
# name) never reached the journal — an auto-update check that fetched, stalled
# and was killed at its timeout left no line at all (fresh system, 2026-10-21).
logger = logging.getLogger("corvin.console.update_manager")

BRANCH = "main"
_FETCH_TIMEOUT_S = 120.0

_locks: dict[str, Lock] = {}
_locks_guard = Lock()

_EXIT_MEANING = {
    1: "update failed and was rolled back - the previous version is running",
    2: "update failed and the rollback failed too - re-run the installer from the checkout",
    3: "another CorvinOS install/update is running - try again when it has finished",
}

PHASES = ("triggered", "delivery", "ui_build", "update_script", "verification")
_STEP_STATUSES = {"running", "ok", "failed", "skipped"}
_CHECK_LABELS = {"delivered": "delivery", "ui_rebuilt": "ui_build", "script_ok": "update_script",
                 "verified": "verification", "up_to_date": "verification"}
_ENTRY_RE = re.compile(r"assets/index-[^\"'\s]*\.js")


def progress_path_for(state_path: Path) -> Path:
    return state_path.with_name("update_progress.jsonl")


def read_progress(path: Path) -> list[dict[str, Any]]:
    """Events the updater appended; a torn or foreign line is skipped, never fatal."""
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        if isinstance(ev, dict) and ev.get("phase") in PHASES and ev.get("status") in _STEP_STATUSES:
            events.append(ev)
    return events


def fold_progress(events: list[dict[str, Any]], base: dict[str, Any] | None = None) -> dict[str, dict[str, Any]]:
    """Latest event per phase -> ``{phase: {status, at, detail}}`` (``base`` seeds steps the manager owns)."""
    steps = {k: dict(v) for k, v in (base or {}).items()}
    for ev in events:
        steps[ev["phase"]] = {"status": ev["status"], "at": ev.get("ts"),
                              "detail": str(ev.get("detail") or "")[:120] or None}
    return steps


def _sha_matches(a: str | None, b: str | None) -> bool:
    return bool(a and b and (a.startswith(b) or b.startswith(a)))


def _tree_facts(src: Path | None) -> dict[str, Any]:
    """What the tree says, independent of what the updater claimed."""
    facts: dict[str, Any] = {"git": False, "head": None, "shallow": False, "dist_entry": None}
    if src is None:
        return facts
    try:
        head = _git(src, "rev-parse", "HEAD")
        if head.returncode == 0 and head.stdout.strip():
            facts.update(git=True, head=head.stdout.strip(),
                         shallow=_git(src, "rev-parse", "--is-shallow-repository").stdout.strip() == "true")
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        html = (src / "core" / "console" / "corvin_console" / "web-next" / "dist" / "index.html").read_text(
            encoding="utf-8", errors="replace")
        m = _ENTRY_RE.search(html)
        facts["dist_entry"] = m.group(0) if m else None
    except OSError:
        pass
    return facts


def _target_reached(src: Path | None, facts: dict[str, Any], target: str | None, head_before: str | None) -> bool | None:
    """Is the installed commit the target (or newer)? None = cannot be proven."""
    head = facts.get("head")
    if not facts.get("git") or not target:
        return None
    if _sha_matches(head, target):
        return True
    try:
        if src is not None and _git(src, "merge-base", "--is-ancestor", target, head).returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    # A shallow tree may lack the target's ancestry: moving forward is the best offline proof.
    return bool(facts.get("shallow") and head and not _sha_matches(head, head_before))


def evaluate_run(steps: dict[str, dict[str, Any]], rc: int | None, facts: dict[str, Any],
                 target: str | None, head_before: str | None, src: Path | None = None) -> dict[str, Any]:
    """Decide how a finished run ends. Success needs ALL five links proven; a link that is
    merely unproven ends the run as ``unverified``, never as success."""
    st = lambda k: (steps.get(k) or {}).get("status")  # noqa: E731
    claim = (steps.get("delivery") or {}).get("detail") or ""
    delivered = st("delivery") == "ok"
    if delivered and facts.get("git") and re.fullmatch(r"[0-9a-f]{7,40}", claim):
        delivered = _sha_matches(facts.get("head"), claim)  # the script's claim must match the tree
    entry = (steps.get("ui_build") or {}).get("detail") or ""
    ui = st("ui_build") == "ok" and bool(facts.get("dist_entry")) and (not entry or entry == facts["dist_entry"])
    verified = st("verification") == "ok"
    script_ok = rc == 0 or (rc is None and verified)  # rc is unknown when the restart cut the watcher off
    reached = _target_reached(src, facts, target, head_before)
    up_to_date = reached if reached is not None else (not facts.get("git") and verified)
    checks = {"delivered": delivered, "ui_rebuilt": ui, "script_ok": script_ok,
              "verified": verified, "up_to_date": bool(up_to_date)}
    failed_phase = next((p for p in PHASES if st(p) == "failed"), None)
    if all(checks.values()):
        return {"status": "success", "phase": "completed", "checks": checks, "failed_phase": None, "detail": None}
    if rc not in (0, None) or failed_phase:
        detail = _EXIT_MEANING.get(rc or 0) or ((steps.get(failed_phase) or {}).get("detail") if failed_phase else None)
        return {"status": "failed", "phase": "failed", "checks": checks,
                "failed_phase": failed_phase or "update_script",
                "detail": detail or (f"updater exited {rc}" if rc else f"{failed_phase} failed")}
    missing = [_CHECK_LABELS[k] for k, v in checks.items() if not v]
    return {"status": "unverified", "phase": "failed", "checks": checks, "failed_phase": missing[0],
            "detail": "the updater finished but this was not proven: " + ", ".join(sorted(set(missing)))}


def _tenant_lock(tenant_id: str) -> Lock:
    with _locks_guard:
        return _locks.setdefault(tenant_id, Lock())


class UpdateNotAllowedError(Exception):
    """No updater found, or an update is already running."""


@dataclass
class UpdateCheckResult:
    has_update: bool
    current_version: str
    available_version: str | None
    error: str | None = None
    running: bool = False
    last_update: dict[str, Any] | None = field(default=None)
    behind: int = 0
    local_sha: str | None = None
    remote_sha: str | None = None


def _script_name() -> str:
    return "update.ps1" if sys.platform == "win32" else "update.sh"


def _is_source_tree(p: Path) -> bool:
    return (p / "pyproject.toml").is_file() and (p / _script_name()).is_file()


def find_source_tree() -> Path:
    """Source tree of the running install: CORVIN_SRC_DIR, the checkout this
    module is imported from (editable install), else the installer-managed tree."""
    env = os.environ.get("CORVIN_SRC_DIR")
    if env and _is_source_tree(Path(env)):
        return Path(env)
    for parent in Path(__file__).resolve().parents:
        if _is_source_tree(parent):
            return parent
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        managed = Path(base) / "corvinos" / "src" if base else None
    else:
        data = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        managed = Path(data) / "corvinos" / "src"
    if managed and _is_source_tree(managed):
        return managed
    raise UpdateNotAllowedError(
        f"{_script_name()} not found in the CorvinOS source tree. Install from a local "
        "checkout (git clone https://github.com/CorvinLabs/CorvinOS && ./install.sh), "
        "or point CORVIN_SRC_DIR at it."
    )


def updater_command(src: Path) -> list[str]:
    if sys.platform == "win32":
        shell = "powershell"
        return [shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(src / "update.ps1")]
    return ["sh", str(src / "update.sh")]


def _use_systemd_run() -> bool:
    """The updater restarts every corvin-*.service. Started from inside one of them it
    shares that unit's cgroup and is killed with it, so on Linux it runs as its own
    transient user unit."""
    import shutil
    if not sys.platform.startswith("linux") or not shutil.which("systemd-run"):
        return False
    try:
        return subprocess.run(["systemctl", "--user", "show-environment"], capture_output=True,
                              timeout=5, stdin=subprocess.DEVNULL).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def isolated_command(src: Path, state_path: Path, log: Path) -> list[str]:
    """systemd-run wrapper: runs update.sh, then records its exit code in the state file
    itself, so the outcome survives the console restarting mid-update."""
    pkg_root = str(Path(__file__).resolve().parents[1])
    script = ('sh "$1"; rc=$?; "$2" -m corvin_console.update_manager --record "$3" "$rc" "$4"; exit $rc')
    return ["systemd-run", "--user", "--quiet", "--collect", "--wait",
            f"--unit=corvin-update-{int(time.time())}", f"--working-directory={src}",
            f"--setenv=PATH={os.environ.get('PATH', '')}", f"--setenv=PYTHONPATH={pkg_root}",
            f"--setenv=CORVIN_UPDATE_PROGRESS={progress_path_for(state_path)}",
            f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
            "sh", "-c", script, "_", str(src / "update.sh"), sys.executable, str(state_path), str(src)]


def finalize_state(st: dict[str, Any], events: list[dict[str, Any]], rc: int | None, src: Path | None) -> dict[str, Any]:
    """Fold the updater's progress into ``st`` and decide the outcome (shared by the isolated
    wrapper, the in-process watcher and the cut-off fallback)."""
    steps = fold_progress(events, st.get("steps"))
    facts = _tree_facts(src)
    res = evaluate_run(steps, rc, facts, st.get("target_sha"), st.get("head_before"), src)
    now = time.time()
    steps["update_script"] = {"status": "ok" if res["checks"]["script_ok"] else "failed", "at": now,
                              "detail": None if rc in (0, None) else f"exit {rc}"}
    st.update(status=res["status"], phase=res["phase"], steps=steps, checks=res["checks"],
              failed_phase=res["failed_phase"], finished_at=now, exit_code=rc, head_after=facts["head"],
              version_after=_local_version(src) if src else st.get("version_after"), detail=res["detail"])
    if res["status"] == "success":
        st.update(last_success_at=now, last_success_sha=facts["head"], last_success_version=st["version_after"],
                  last_success_trigger=st.get("trigger"))
    return st


def record_result(state_path: Path, rc: int, src: Path | None = None) -> None:
    """Stdlib-only: finalize a run in the state file (called by the isolated wrapper)."""
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    src = src or Path(__file__).resolve().parents[3]
    finalize_state(st, read_progress(progress_path_for(state_path)), rc, src)
    tmp = state_path.with_suffix(".tmp")
    tmp.write_text(json.dumps(st), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, state_path)


def _git(src: Path, *args: str, timeout: float = 30.0) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(src), *args], capture_output=True, text=True,
                          timeout=timeout, stdin=subprocess.DEVNULL)


def _pyproject_version(text: str) -> str:
    in_project = False
    for line in text.splitlines():
        s = line.strip()
        if s.startswith("["):
            in_project = s == "[project]"
        elif in_project and s.startswith("version") and "=" in s:
            return s.split("=", 1)[1].strip().strip('"').strip("'")
    return "unknown"


def _local_version(src: Path) -> str:
    try:
        return _pyproject_version((src / "pyproject.toml").read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return "unknown"


def _scrub(text: str) -> str:
    import re
    for pat, rep in ((r"sk-[A-Za-z0-9_\-]{20,}", "[redacted]"), (r"gh[pousr]_[A-Za-z0-9]{20,}", "[redacted]"),
                     (r"(?i)(token|secret|password|api[_-]?key)\s*[=:]\s*\S+", r"\1=[redacted]")):
        text = re.sub(pat, rep, text)
    return text



def _isolated_run_active() -> bool:
    """True while a transient ``corvin-update-*`` systemd unit (the isolated updater) is active.
    False where systemd-run is not used (no unit, or not a Linux systemd host): the in-process
    lock is then the only truth, as before."""
    if sys.platform != "linux":
        return False
    try:
        out = subprocess.run(
            ["systemctl", "--user", "list-units", "--plain", "--no-legend", "--state=active,activating,deactivating",
             "corvin-update-*"],
            capture_output=True, text=True, timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return any(line.strip().startswith("corvin-update-") for line in out.splitlines())

class UpdateManager:
    def __init__(self, tenant_id: str, state_path: Path | None = None):
        self.tenant_id = tenant_id
        self.lock = _tenant_lock(tenant_id)
        self._state_path = state_path

    # ── state: when did the last update run ───────────────────────────────
    def state_path(self) -> Path:
        if self._state_path is not None:
            return self._state_path
        from . import _bootstrap
        return _bootstrap.forge_paths.tenant_global_dir(self.tenant_id) / "update_state.json"

    def _read_state(self) -> dict[str, Any] | None:
        try:
            data = json.loads(self.state_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (OSError, ValueError):
            return None

    def _write_state(self, state: dict[str, Any]) -> None:
        path = self.state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".update_state.")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def last_update(self, current_version: str, src: Path | None = None) -> dict[str, Any] | None:
        """Last run: ``{at, status, phase, steps, checks, ...}`` plus ``last_success_at``.

        While it runs, the updater's progress file is folded in live. A run still
        ``running`` while nothing holds the lock was cut off by the console restart the
        updater itself performs; it is settled from the progress it left and the tree
        (``finalize_state``) - never from a bare version change."""
        st = self._read_state()
        if not st:
            return None
        events = read_progress(progress_path_for(self.state_path()))
        if st.get("status") == "running" and not self.lock.locked() and not _isolated_run_active():
            # No lock in THIS process does not mean the run is over: the updater restarts the
            # console, and the new process polled this before the transient unit had finished
            # (verify / wait_live take up to 180 s). Finalising then marked a live run
            # "interrupted", re-enabled the Update button and let a second run start (review
            # 2026-10-21). The unit's own existence is the truth on a systemd host.
            if src is None:
                try:
                    src = find_source_tree()
                except UpdateNotAllowedError:
                    src = None
            finalize_state(st, events, None, src)
            if st["status"] == "unverified":  # a recorded failure stays a failure
                st.update(status="interrupted", phase="failed",
                          detail=st.get("detail") or "the update was cut off before it finished")
            try:
                self._write_state(st)
            except OSError:
                pass
        steps, phase = st.get("steps") or {}, st.get("phase")
        if st.get("status") == "running":
            steps = fold_progress(events, steps)
            phase = events[-1]["phase"] if events else "triggered"
        return {
            "at": st.get("finished_at") or st.get("started_at"),
            "status": st.get("status"),
            "phase": phase,
            "steps": steps,
            "checks": st.get("checks"),
            "failed_phase": st.get("failed_phase"),
            "trigger": st.get("trigger"),
            "started_at": st.get("started_at"),
            "target_sha": st.get("target_sha"),
            "head_after": st.get("head_after"),
            "version_before": st.get("version_before"),
            "version_after": st.get("version_after"),
            "detail": st.get("detail"),
            "last_success_at": st.get("last_success_at"),
            "last_success_version": st.get("last_success_version"),
            "last_success_trigger": st.get("last_success_trigger"),
        }

    # ── check ─────────────────────────────────────────────────────────────
    async def check_update(self) -> UpdateCheckResult:
        running = self.lock.locked()
        try:
            src = find_source_tree()
        except UpdateNotAllowedError as e:
            return UpdateCheckResult(False, "unknown", None, str(e), running, self.last_update("unknown"))
        current = _local_version(src)
        last = self.last_update(current, src)
        try:
            return await asyncio.to_thread(self._check_blocking, src, current, running, last)
        except Exception as e:  # noqa: BLE001 - surfaced to the UI as a check error
            return UpdateCheckResult(False, current, None, f"check failed: {e}", running, last)

    @staticmethod
    def _check_blocking(src: Path, current: str, running: bool, last: dict[str, Any] | None) -> UpdateCheckResult:
        fetch = _git(src, "fetch", "--quiet", "origin", BRANCH, timeout=_FETCH_TIMEOUT_S)
        if fetch.returncode != 0:
            return UpdateCheckResult(False, current, None,
                                     f"git fetch failed: {_scrub((fetch.stderr or fetch.stdout).strip()[:200])}",
                                     running, last)
        counts = _git(src, "rev-list", "--left-right", "--count", f"origin/{BRANCH}...HEAD")
        if counts.returncode != 0:
            return UpdateCheckResult(False, current, None, f"cannot compare with origin/{BRANCH}", running, last)
        behind = int(counts.stdout.split()[0])
        remote = _pyproject_version(_git(src, "show", f"origin/{BRANCH}:pyproject.toml").stdout)
        local_sha = _git(src, "rev-parse", "--short", "HEAD").stdout.strip() or None
        remote_sha = _git(src, "rev-parse", "--short", f"origin/{BRANCH}").stdout.strip() or None
        # Commit-based: ANY commit on origin/main that HEAD lacks is an update, version bump or not.
        return UpdateCheckResult(behind > 0, current, remote if behind > 0 else None, None, running, last,
                                 behind, local_sha, remote_sha)

    # ── run ───────────────────────────────────────────────────────────────
    def start_update(self, sid_fingerprint: str, trigger: str = "manual") -> dict[str, Any]:
        """Start the updater detached. Raises UpdateNotAllowedError if one is running."""
        src = find_source_tree()
        if not self.lock.acquire(blocking=False):
            raise UpdateNotAllowedError("Update already in progress. Please wait.")
        try:
            before = _local_version(src)
            log = Path(tempfile.gettempdir()) / "corvinos-console-update.log"
            logfh = open(log, "wb")
            kwargs: dict[str, Any] = {"cwd": str(src), "stdin": subprocess.DEVNULL,
                                      "stdout": logfh, "stderr": subprocess.STDOUT}
            if sys.platform == "win32":
                # CREATE_NO_WINDOW, not DETACHED_PROCESS: powershell.exe started without a
                # console exits 0 at once without running the script (empty log, no progress).
                kwargs["creationflags"] = (getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
                                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                # The updater stops this console; if a Job object (task scheduler, launcher) kills its
                # members with the console, the updater must not be one of them. Tried first, below.
                breakaway = 0x01000000  # CREATE_BREAKAWAY_FROM_JOB
            else:
                kwargs["start_new_session"] = True
            keep = {k: v for k, v in (self._read_state() or {}).items()
                    if k in ("last_auto_target", "last_success_at", "last_success_sha", "last_success_version",
                             "last_success_trigger")}
            facts = _tree_facts(src)
            target = _git(src, "rev-parse", f"origin/{BRANCH}").stdout.strip() if facts["git"] else ""
            progress = progress_path_for(self.state_path())
            progress.parent.mkdir(parents=True, exist_ok=True)
            progress.write_text("", encoding="utf-8")
            os.chmod(progress, 0o600)
            now = time.time()
            self._write_state({**keep, "status": "running", "phase": "triggered", "trigger": trigger,
                               "started_at": now, "version_before": before, "log": str(log),
                               "head_before": facts["head"], "target_sha": target or None,
                               "steps": {"triggered": {"status": "ok", "at": now, "detail": trigger},
                                         "update_script": {"status": "running", "at": now, "detail": None}}})
            kwargs["env"] = {**os.environ, "CORVIN_UPDATE_PROGRESS": str(progress)}
            self._audit(sid_fingerprint, ok=True, action="settings.update_install")
            isolated = _use_systemd_run()
            cmd = isolated_command(src, self.state_path(), log) if isolated else updater_command(src)
            if isolated:
                kwargs.pop("start_new_session", None)
            try:
                if sys.platform == "win32":
                    proc = subprocess.Popen(cmd, **{**kwargs, "creationflags": kwargs["creationflags"] | breakaway})  # noqa: S603
                else:
                    proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603
            except OSError:  # the job forbids breakaway: start inside it, update.ps1 copes
                if sys.platform != "win32":
                    raise
                proc = subprocess.Popen(cmd, **kwargs)  # noqa: S603
        except BaseException:
            self.lock.release()
            raise
        finally:
            try:
                logfh.close()
            except Exception:  # noqa: BLE001
                pass
        try:
            asyncio.get_running_loop().create_task(self._watch(proc, src, before, sid_fingerprint))
        except RuntimeError:
            self.lock.release()
        return {"success": True, "exit_code": None,
                "final_message": "Update started. The console restarts when it is done - reload the page afterwards."}

    async def _watch(self, proc: subprocess.Popen, src: Path, before: str, sid: str) -> None:
        try:
            rc = await asyncio.to_thread(proc.wait)
            st = self._read_state() or {}
            if st.get("status") == "running":  # the isolated wrapper records its own result
                finalize_state(st, read_progress(progress_path_for(self.state_path())), rc, src)
                try:
                    self._write_state(st)
                except OSError:
                    logger.warning("could not persist update state")
            ok = st.get("status") == "success"
            self._audit(sid, ok=ok, action="settings.update_completed",
                        reason=None if ok else f"{st.get('status')}-{st.get('failed_phase') or rc}")
        finally:
            self.lock.release()

    def _audit(self, sid: str, *, ok: bool, action: str, reason: str | None = None) -> None:
        try:
            from . import audit as console_audit
            kw = dict(tenant_id=self.tenant_id, sid_fingerprint=sid, action=action,
                      target_kind="system_update", target_id="corvinOS_core")
            if ok:
                console_audit.action_performed(**kw)
            else:
                console_audit.action_failed(reason=reason or "failed", **kw)
        except Exception as e:  # noqa: BLE001 - never let auditing break the update, but say so
            logger.error("update audit event %s not written: %s", action, e)


AUTO_START_DELAY_S = 60.0


async def auto_update_on_start(tenant_id: str, *, enabled: bool, delay_s: float = AUTO_START_DELAY_S) -> str:
    """After a start, update when origin/main has commits this checkout lacks.

    Returns what it did (also logged): disabled | no-updater | up-to-date | already-tried |
    check-failed | busy | started. The same remote commit is attempted ONCE: the updater
    restarts this process, and a failed update (rolled back) leaves HEAD behind, so without
    the marker every restart would re-run it."""
    if not enabled:
        return "disabled"
    try:
        find_source_tree()
    except UpdateNotAllowedError:
        return "no-updater"
    await asyncio.sleep(delay_s)
    mgr = UpdateManager(tenant_id)
    res = await mgr.check_update()
    if res.error:
        logger.warning("auto-update: check failed: %s", res.error)
        return "check-failed"
    if not res.has_update:
        return "up-to-date"
    st = mgr._read_state() or {}
    if st.get("last_auto_target") == res.remote_sha:
        logger.warning("auto-update: origin/main %s was already attempted - not retrying", res.remote_sha)
        return "already-tried"
    try:
        logger.info("auto-update: %d commit(s) behind origin/main (%s -> %s) - updating",
                    res.behind, res.local_sha, res.remote_sha)
        mgr.start_update("auto-update", trigger="auto")
    except UpdateNotAllowedError:
        # NOT marked as attempted: nothing ran. Until 2026-10-21 the marker was
        # written before start_update, so a start refused because an install
        # or a manual update still held the lock consumed the one attempt and
        # that commit was never retried on any later restart.
        return "busy"
    # Marked right after the updater really started (start_update keeps an
    # existing marker, it never writes one): a failed, rolled-back run leaves
    # HEAD behind, and without the marker every restart would re-run it.
    st = mgr._read_state() or {}
    st["last_auto_target"] = res.remote_sha
    mgr._write_state(st)
    return "started"


_SCHEDULED_FOR: set[str] = set()


def schedule_auto_update(tenant_id: str | None = None) -> None:
    """Fire-and-forget; called from the lifespan of BOTH hosts (console app, gateway app).

    Once per tenant per process: when the gateway mounts the console, both
    lifespans run in ONE process and two checks ran two concurrent `git fetch`
    on the same checkout (two abandoned tmp_pack files on a fresh system,
    2026-10-21, round 2)."""
    try:
        from .routes.settings import _read_auto_update
        tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"
        if tid in _SCHEDULED_FOR:
            logger.info("auto-update on start: already scheduled for tenant %s in this process", tid)
            return
        _SCHEDULED_FOR.add(tid)

        async def _run() -> None:
            try:
                logger.info("auto-update on start: %s", await auto_update_on_start(tid, enabled=_read_auto_update()))
            except Exception as exc:  # noqa: BLE001 - never affects startup
                logger.warning("auto-update on start failed: %s", exc)

        asyncio.get_running_loop().create_task(_run())
    except Exception as exc:  # noqa: BLE001
        logger.warning("auto-update on start not scheduled: %s", exc)


if __name__ == "__main__":  # isolated wrapper: python -m corvin_console.update_manager --record STATE RC
    if len(sys.argv) in (4, 5) and sys.argv[1] == "--record":
        record_result(Path(sys.argv[2]), int(sys.argv[3]), Path(sys.argv[4]) if len(sys.argv) == 5 else None)
