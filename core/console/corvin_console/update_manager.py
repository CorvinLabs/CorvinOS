"""Console client for the repository's own updater (update.sh / update.ps1).

The updater at the root of the install's source tree is the ONE updater
(verified swap, rollback, shared install/update lock -- ADR-2054, ADR-2215,
ADR-2214). This module only locates it, runs it detached, audits the run and
remembers when it last ran. It never copies or re-implements update logic.

The updater restarts the console, so it is started in its own session and the
request returns immediately; the outcome is read back from ``update_state.json``.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)

BRANCH = "main"
_FETCH_TIMEOUT_S = 120.0

_locks: dict[str, Lock] = {}
_locks_guard = Lock()

_EXIT_MEANING = {
    1: "update failed and was rolled back - the previous version is running",
    2: "update failed and the rollback failed too - re-run the installer from the checkout",
    3: "another CorvinOS install/update is running - try again when it has finished",
}


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
            f"--property=StandardOutput=append:{log}", f"--property=StandardError=append:{log}",
            "sh", "-c", script, "_", str(src / "update.sh"), sys.executable, str(state_path), str(src)]


def record_result(state_path: Path, rc: int, src: Path | None = None) -> None:
    """Stdlib-only: finalize a run in the state file (called by the isolated wrapper)."""
    try:
        st = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        st = {}
    src = src or Path(__file__).resolve().parents[3]
    st.update(status="success" if rc == 0 else "failed", finished_at=time.time(), exit_code=rc,
              version_after=_local_version(src),
              detail=None if rc == 0 else _EXIT_MEANING.get(rc, f"updater exited {rc}"))
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

    def last_update(self, current_version: str) -> dict[str, Any] | None:
        """Last run as ``{at, status, version_before, version_after}``.

        A run that is still ``running`` while nothing holds the lock was cut off
        by the console restart the updater itself performs: it counts as done
        when the installed version changed, otherwise as interrupted."""
        st = self._read_state()
        if not st:
            return None
        if st.get("status") == "running" and not self.lock.locked():
            changed = current_version not in ("unknown", st.get("version_before"))
            st["status"] = "success" if changed else "interrupted"
            st["version_after"] = current_version
            try:
                self._write_state(st)
            except OSError:
                pass
        return {
            "at": st.get("finished_at") or st.get("started_at"),
            "status": st.get("status"),
            "version_before": st.get("version_before"),
            "version_after": st.get("version_after"),
            "detail": st.get("detail"),
        }

    # ── check ─────────────────────────────────────────────────────────────
    async def check_update(self) -> UpdateCheckResult:
        running = self.lock.locked()
        try:
            src = find_source_tree()
        except UpdateNotAllowedError as e:
            return UpdateCheckResult(False, "unknown", None, str(e), running, self.last_update("unknown"))
        current = _local_version(src)
        last = self.last_update(current)
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
    def start_update(self, sid_fingerprint: str) -> dict[str, Any]:
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
                kwargs["creationflags"] = (getattr(subprocess, "DETACHED_PROCESS", 0)
                                           | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
            else:
                kwargs["start_new_session"] = True
            keep = {k: v for k, v in (self._read_state() or {}).items() if k == "last_auto_target"}
            self._write_state({**keep, "status": "running", "started_at": time.time(),
                               "version_before": before, "log": str(log)})
            self._audit(sid_fingerprint, ok=True, action="settings.update_install")
            isolated = _use_systemd_run()
            cmd = isolated_command(src, self.state_path(), log) if isolated else updater_command(src)
            if isolated:
                kwargs.pop("start_new_session", None)
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
            after = _local_version(src)
            st = self._read_state() or {}
            if st.get("status") == "running":  # the isolated wrapper records its own result
                st.update(status="success" if rc == 0 else "failed", finished_at=time.time(),
                          version_after=after, exit_code=rc,
                          detail=None if rc == 0 else _EXIT_MEANING.get(rc, f"updater exited {rc}"))
                try:
                    self._write_state(st)
                except OSError:
                    logger.warning("could not persist update state")
            self._audit(sid, ok=rc == 0, action="settings.update_completed",
                        reason=None if rc == 0 else f"exit-{rc}")
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
        st = mgr._read_state() or {}
        st["last_auto_target"] = res.remote_sha
        mgr._write_state(st)
        logger.info("auto-update: %d commit(s) behind origin/main (%s -> %s) - updating",
                    res.behind, res.local_sha, res.remote_sha)
        mgr.start_update("auto-update")
    except UpdateNotAllowedError:
        return "busy"
    return "started"


def schedule_auto_update(tenant_id: str | None = None) -> None:
    """Fire-and-forget; called from the lifespan of BOTH hosts (console app, gateway app)."""
    try:
        from .routes.settings import _read_auto_update
        tid = tenant_id or os.environ.get("CORVIN_TENANT_ID") or "_default"

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
