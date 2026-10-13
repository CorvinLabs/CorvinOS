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
        return UpdateCheckResult(behind > 0, current, remote if behind > 0 else None, None, running, last)

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
            self._write_state({"status": "running", "started_at": time.time(),
                               "version_before": before, "log": str(log)})
            self._audit(sid_fingerprint, ok=True, action="settings.update_install")
            proc = subprocess.Popen(updater_command(src), **kwargs)  # noqa: S603
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
