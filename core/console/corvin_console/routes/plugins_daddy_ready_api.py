"""DaddyReady console routes (panel backend of the private plugin ``daddy_ready``, ADR-2331 source).

Live paths: ``/v1/console/plugins/daddy-ready/{overview,kb,kb/{id},episodes,episodes/{day}/brief,
episodes/{day}/video,settings}``. Every route needs a session, every mutation the CSRF token, and the whole router is
gated on the plugin being installed AND enabled for the caller's tenant (404 otherwise, fail-closed).

The plugin is never imported: each call runs ``python -m daddy_ready api <op>`` as its own process and returns the JSON
it prints (a hostile or broken plugin cannot take the console down, and the plugin keeps owning its data and config).
The panel shows data about a child: it is read from the plugin's local data directory, returned only to the
authenticated caller, never forwarded, and audit records carry the operation name only - never ids, text or paths.
"""
from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

PLUGIN_ID = "daddy_ready"
_CLI_TIMEOUT_S = 60
_MAX_SETTINGS_BYTES = 256 * 1024
_USER = re.compile(r"^[a-z0-9][a-z0-9_-]{0,30}$")
_DAY = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ID = re.compile(r"^[A-Z]{3}-[0-9a-f]{12}$")
# The environment a job may see: the basics, plus the speech and secret variables the config may reference by name.
_PASS_ENV = ("PATH", "HOME", "LANG", "LC_ALL", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "TMPDIR", "PIPER_BIN",
             "CORVIN_TTS_OPENAI_KEY", "PLAYWRIGHT_BROWSERS_PATH")
_MAX_JOBS = 2                                      # renders + concept videos at once (the pool has the same size)


def _plugin_dir() -> Optional[Path]:
    """The plugin directory (the one holding ``plugin.yaml`` with our id) under a trusted or private plugin root."""
    from corvin_plugins import bootstrap as _bs  # noqa: PLC0415

    from . import marketplace_resolve as _resolve  # noqa: PLC0415

    roots = list(_resolve._plugins_roots()) + list(_resolve._private_roots())
    for root in roots:
        for manifest in sorted(root.glob("*/*/*/plugin.yaml")):
            data = _bs.load_from_manifest_safe(manifest)
            if isinstance(data, dict) and data.get("plugin_id") == PLUGIN_ID:
                return manifest.parent
    return None


def _record(tenant_id: str):
    from .plugins import _load  # noqa: PLC0415

    return _load(tenant_id).records.get(PLUGIN_ID)


async def _require_enabled(rec=Depends(require_session_csrf_on_mutation)):
    """The API lives and dies with the plugin: installed AND enabled for THIS tenant. Fail-closed, 404."""
    def _ok() -> bool:
        try:
            r = _record(rec.tenant_id)
            return bool(r is not None and getattr(r, "enabled", False))
        except Exception:  # noqa: BLE001
            return False

    if not await asyncio.to_thread(_ok):
        raise HTTPException(status_code=404, detail="The DaddyReady plugin is not installed and enabled for this tenant")
    return rec


router = APIRouter(
    prefix="/plugins/daddy-ready",
    tags=["console-daddy-ready"],
    dependencies=[Depends(require_session_csrf_on_mutation), Depends(_require_enabled)],
)


def _audit(tenant_id: str, event: str, details: dict[str, Any]) -> bool:
    try:
        from .. import _bootstrap  # noqa: PLC0415
        from forge.security_events import write_event  # type: ignore[import-not-found]  # noqa: PLC0415

        write_event(_bootstrap.forge_paths.tenant_audit_chain(tenant_id), event,
                    details={"tenant_id": tenant_id, **details})
        return True
    except Exception as exc:  # noqa: BLE001
        logger.warning("daddy-ready: audit write failed (%s)", type(exc).__name__)
        return False


def _config_user(tenant_id: str) -> str:
    try:
        r = _record(tenant_id)
        user = str((getattr(r, "settings", None) or {}).get("config_user") or "default")
    except Exception:  # noqa: BLE001
        user = "default"
    return user if _USER.match(user) else "default"


def _run_cli(tenant_id: str, *args: str, stdin: str | None = None) -> Any:
    """Run one plugin command in its own process; return the decoded JSON. Blocking - call via to_thread."""
    pdir = _plugin_dir()
    if pdir is None:
        raise HTTPException(status_code=503, detail="The DaddyReady plugin source was not found")
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env["PYTHONPATH"] = str(pdir / "src")
    cmd = [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); sys.argv = ['daddy_ready'] + sys.argv[2:]; "
           "from daddy_ready.cli import main; sys.exit(main())", str(pdir / "src"),
           "--user", _config_user(tenant_id), "api", *args]
    try:
        r = subprocess.run(cmd, input=stdin, capture_output=True, text=True, env=env, timeout=_CLI_TIMEOUT_S,
                           cwd=str(pdir), encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        raise HTTPException(status_code=504, detail="The plugin did not answer in time") from None
    except OSError:
        raise HTTPException(status_code=502, detail="The plugin could not be started") from None
    if r.returncode == 0:
        try:
            return json.loads(r.stdout)
        except ValueError:
            raise HTTPException(status_code=502, detail="The plugin answered something that is not JSON") from None
    detail = (r.stderr or "").strip().splitlines()[-1:] or ["failed"]
    msg = re.sub(r"^error:\s*", "", detail[0])[:300]
    if r.returncode == 2:
        raise HTTPException(status_code=422, detail=msg)
    if r.returncode == 6:
        raise HTTPException(status_code=404, detail=msg)
    logger.warning("daddy-ready: plugin command %s exited %s", args[0] if args else "?", r.returncode)
    raise HTTPException(status_code=502, detail="The plugin command failed")


async def _call(rec, op: str, *args: str, stdin: str | None = None, audit: bool = True) -> Any:
    out = await asyncio.to_thread(_run_cli, rec.tenant_id, op, *args, stdin=stdin)
    if audit:
        await asyncio.to_thread(_audit, rec.tenant_id, "daddy_ready.panel_read", {"op": op})
    return out


@router.get("/overview")
async def overview(rec=Depends(_require_enabled)) -> Any:
    return await _call(rec, "overview", audit=False)


@router.get("/kb")
async def kb_list(type: Optional[str] = Query(None, max_length=20), q: Optional[str] = Query(None, max_length=200),
                  limit: Optional[int] = Query(None, ge=1, le=200), offset: int = Query(0, ge=0, le=100000),
                  rec=Depends(_require_enabled)) -> Any:
    args = ["kb", "--offset", str(offset)]
    if type:
        args += ["--type", type]
    if q:
        args += ["--q", q]
    if limit:
        args += ["--limit", str(limit)]
    return await _call(rec, *args)


@router.get("/kb/{entry_id}")
async def kb_entry(entry_id: str, rec=Depends(_require_enabled)) -> Any:
    if not _ID.match(entry_id):
        raise HTTPException(status_code=404, detail="no such entry")
    return await _call(rec, "entry", entry_id)


@router.get("/episodes")
async def episodes(rec=Depends(_require_enabled)) -> Any:
    doc = await _call(rec, "episodes", audit=False)
    prefix = f"{rec.tenant_id}:"
    doc["jobs"] = [{"day": k[len(prefix):], "state": v["state"], "error": v.get("error", "")}
                   for k, v in _RENDER_JOBS.items() if k.startswith(prefix)]
    seen = {j["day"] for j in doc["jobs"]}
    for row in doc.get("episodes", []):                 # a render still running after a console restart: show it as running
        if row.get("rendering") and row["day"] not in seen:
            doc["jobs"].append({"day": row["day"], "state": "running", "error": ""})
    return doc


@router.get("/episodes/{day}/brief")
async def episode_brief(day: str, rec=Depends(_require_enabled)) -> Any:
    if not _DAY.match(day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    return await _call(rec, "brief", day)


@router.get("/episodes/{day}/video")
async def episode_video(day: str, rec=Depends(_require_enabled)) -> FileResponse:
    """The rendered episode as video/mp4. FileResponse answers Range requests, so the player can seek."""
    if not _DAY.match(day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    doc = await _call(rec, "video", day)
    p = Path(str(doc.get("path", "")))
    if p.name != "episode.mp4" or p.parent.parent.name != "episodes" or not _DAY.match(p.parent.name) or not p.is_file():
        raise HTTPException(status_code=404, detail="no video for this day")
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.get("/graph")
async def graph(rec=Depends(_require_enabled)) -> Any:
    return await _call(rec, "graph", audit=False)


_RENDER_JOBS: dict[str, dict[str, Any]] = {}
_CONCEPT = re.compile(r"^[a-z][a-z0-9_]{1,40}$")
_VIDEO = re.compile(r"^[a-z0-9_]+-\d{4}-\d{2}-\d{2}\.mp4$")
_CONCEPT_JOBS: dict[str, dict[str, Any]] = {}
# One long-lived pool for the background renders (no thread per request; survives the request's event loop).
_JOB_POOL = ThreadPoolExecutor(max_workers=2, thread_name_prefix="daddy-ready-job")


def _cli_cmd(pdir: Path, user: str, *args: str) -> list[str]:
    return [sys.executable, "-c", "import sys; sys.path.insert(0, sys.argv[1]); sys.argv = ['daddy_ready'] + sys.argv[2:]; "
            "from daddy_ready.cli import main; sys.exit(main())", str(pdir / "src"), "--user", user, *args]


def _run_job(cmd: list[str], pdir: Path, timeout: int) -> tuple[int, str]:
    """Blocking: runs in the loop's long-lived executor, never on the event loop. Returns the exit code and the CLI's
    own (already redacted) error line, so the panel can say WHY a render was refused."""
    env = {k: os.environ[k] for k in _PASS_ENV if k in os.environ}
    env["PYTHONPATH"] = str(pdir / "src")
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=timeout, cwd=str(pdir), encoding="utf-8",
                           errors="replace")
    except subprocess.TimeoutExpired:
        return -1, "the render took too long and was stopped"
    except OSError:
        return -1, "the render could not be started"
    lines = [ln for ln in (r.stderr or "").splitlines() if ln.startswith("error:")]
    return r.returncode, (lines[-1][len("error:"):].strip()[:300] if lines and r.returncode != 0 else "")


def _busy() -> int:
    return sum(1 for j in (*_RENDER_JOBS.values(), *_CONCEPT_JOBS.values()) if j.get("state") == "running")


def _render_job(tenant_id: str, key: str, cmd: list[str], pdir: Path, timeout: int, audit_op: str, state_key: dict) -> None:
    """Runs in the job pool, never on the event loop: runs the CLI, records the outcome, writes the audit record. A job
    can never stay `running`: any failure in here ends it as `failed`."""
    try:
        code, err = _run_job(cmd, pdir, timeout)
    except BaseException:                                  # noqa: BLE001 - the slot must be freed whatever happened
        logger.exception("daddy-ready: job %s crashed", key)
        code, err = -1, "the job crashed"
    state = "done" if code == 0 else "failed"
    state_key[key] = {"state": state, "error": err}
    if not _audit(tenant_id, "daddy_ready.render_finished", {"op": audit_op, "outcome": state}):
        logger.warning("daddy-ready: finish record for %s was not written", key)


@router.post("/episodes/{day}/render", status_code=202)
async def episode_render(day: str, rec=Depends(_require_enabled)) -> Any:
    """Start rendering the day's episode in the background. The plugin's own gates run inside (brief, approvals,
    video enabled). Poll `/episodes` for the step status and `jobs` for a refusal; a render never blocks the request."""
    if not _DAY.match(day):
        raise HTTPException(status_code=422, detail="day must be YYYY-MM-DD")
    pdir = await asyncio.to_thread(_plugin_dir)
    if pdir is None:
        raise HTTPException(status_code=503, detail="The DaddyReady plugin source was not found")
    key = f"{rec.tenant_id}:{day}"
    user = await asyncio.to_thread(_config_user, rec.tenant_id)
    # check and claim in one step with no await in between: two requests cannot both start the same render
    if _RENDER_JOBS.get(key, {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="A render for this day is already running")
    if _busy() >= _MAX_JOBS:
        raise HTTPException(status_code=409, detail="Two videos are already being made; try again when one is finished")
    _RENDER_JOBS[key] = {"state": "running"}                 # claimed BEFORE the audit await: no second request can slip in
    if not await asyncio.to_thread(_audit, rec.tenant_id, "daddy_ready.render_started", {"op": "render"}):
        _RENDER_JOBS.pop(key, None)
        raise HTTPException(status_code=503, detail="The audit record could not be written; nothing was started")

    _JOB_POOL.submit(_render_job, rec.tenant_id, key, _cli_cmd(pdir, user, "render", day), pdir, 7200, "render", _RENDER_JOBS)
    return {"day": day, "status": "started"}


@router.get("/concepts")
async def concepts(rec=Depends(_require_enabled)) -> Any:
    return await _call(rec, "concepts", audit=False)


@router.get("/videos")
async def videos_list(rec=Depends(_require_enabled)) -> Any:
    doc = await _call(rec, "videos", audit=False)
    prefix = f"{rec.tenant_id}:"
    doc["jobs"] = [{"concept": k[len(prefix):], "state": v["state"]} for k, v in _CONCEPT_JOBS.items() if k.startswith(prefix)]
    return doc


@router.get("/videos/{name}")
async def video_file(name: str, rec=Depends(_require_enabled)) -> FileResponse:
    if not _VIDEO.match(name):
        raise HTTPException(status_code=422, detail="not a video name")
    doc = await _call(rec, "video-file", name)
    p = Path(str(doc.get("path", "")))
    if p.parent.name != "videos" or not _VIDEO.match(p.name) or not p.is_file():
        raise HTTPException(status_code=404, detail="no such video")
    return FileResponse(p, media_type="video/mp4", headers={"Cache-Control": "private, no-store"})


@router.post("/videos/concept/{concept_id}", status_code=202)
async def video_concept(concept_id: str, rec=Depends(_require_enabled)) -> Any:
    """Make a template video for one psychology concept, locally and in the background. Poll GET /videos for the job
    state."""
    if not _CONCEPT.match(concept_id):
        raise HTTPException(status_code=422, detail="not a concept id")
    pdir = await asyncio.to_thread(_plugin_dir)
    if pdir is None:
        raise HTTPException(status_code=503, detail="The DaddyReady plugin source was not found")
    key = f"{rec.tenant_id}:{concept_id}"
    user = await asyncio.to_thread(_config_user, rec.tenant_id)
    if _CONCEPT_JOBS.get(key, {}).get("state") == "running":
        raise HTTPException(status_code=409, detail="A video for this concept is already being made")
    if _busy() >= _MAX_JOBS:
        raise HTTPException(status_code=409, detail="Two videos are already being made; try again when one is finished")
    _CONCEPT_JOBS[key] = {"state": "running"}                # claimed BEFORE the audit await
    if not await asyncio.to_thread(_audit, rec.tenant_id, "daddy_ready.render_started", {"op": "video-concept"}):
        _CONCEPT_JOBS.pop(key, None)
        raise HTTPException(status_code=503, detail="The audit record could not be written; nothing was started")

    _JOB_POOL.submit(_render_job, rec.tenant_id, key, _cli_cmd(pdir, user, "video", "concept", concept_id), pdir, 3600,
                     "video-concept", _CONCEPT_JOBS)
    return {"concept": concept_id, "state": "running"}


@router.get("/settings")
async def settings_get(rec=Depends(_require_enabled)) -> Any:
    return await _call(rec, "settings", audit=False)


_SECRET_FIELDS = (("video", "template_openai_key"), ("delivery", "webhook_url"))
_REFERENCE = re.compile(r"^(\$\{[A-Za-z_][A-Za-z0-9_]*\}|file:.+)$")


def _check_secret_references(body: dict) -> None:
    """Secrets are only ever REFERENCES (an env variable or a file), never the literal value - a literal would be written
    into the config file and echoed back by the settings view."""
    for group, key in _SECRET_FIELDS:
        value = (body.get(group) or {}).get(key) if isinstance(body.get(group), dict) else None
        if isinstance(value, str) and value and not _REFERENCE.match(value):
            raise HTTPException(status_code=422, detail=f"{group}.{key} must be a reference such as ${{VAR}} or file:PATH, not the value")


@router.put("/settings")
async def settings_put(body: dict = Body(...), rec=Depends(_require_enabled)) -> Any:
    raw = json.dumps(body)
    if len(raw.encode("utf-8")) > _MAX_SETTINGS_BYTES:
        raise HTTPException(status_code=413, detail="settings are too large")
    _check_secret_references(body)
    out = await asyncio.to_thread(_run_cli, rec.tenant_id, "settings-save", stdin=raw)   # validates and saves
    # recorded only once the save succeeded: a refused change never appears as "settings saved"
    if not await asyncio.to_thread(_audit, rec.tenant_id, "daddy_ready.settings_saved", {"op": "settings-save"}):
        logger.warning("daddy-ready: settings were saved but the audit record could not be written")
    return out
