"""FastAPI routes for the Video Producer plugin.

The plugin source (models / storage / skill / async_runner) lives in the
marketplace (contributor/media/video_producer/src) and is loaded here as ONE
private package. Every route is tenant-scoped: each tenant has its own store
under ``tenant_home(rec.tenant_id)/video_producer`` and its own settings, so
no tenant can list, read or download another tenant's jobs.
"""

from __future__ import annotations

import asyncio
import importlib
import importlib.util
import logging
import os
import re
import shutil
import sys
import threading
import uuid
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

# ── Plugin loading ───────────────────────────────────────────────────────────

# Loaded under ONE private package name. Until 2026-10-03 the modules were also
# registered as the bare top-level names "models", "storage" and "skill", so any
# later `import models` anywhere in the console process got the video
# producer's module instead.
_PLUGIN_PKG = "corvin_video_producer_plugin"

_repo_root = Path(__file__).resolve().parents[4]


def _plugin_locations() -> List[tuple]:
    from core.paths.tenant import corvin_home  # honours CORVIN_HOME

    home = corvin_home()
    locs = [
        (home / "tenants" / "_default" / "plugins" / "instances" / "video_producer" / "src", "installed-instance"),
        (home / "plugins" / "media" / "video_producer" / "src", "installed-media"),
        (home / "plugins" / "video_producer" / "src", "installed"),
        (_repo_root.parent / "Corvin-Marketplace" / "plugins" / "contributor" / "media" / "video_producer" / "src", "sibling-marketplace"),
    ]
    root = os.environ.get("CORVIN_MARKETPLACE_ROOT")
    if root:
        locs.append((Path(root) / "plugins" / "contributor" / "media" / "video_producer" / "src", "dev-env"))
    # A fresh install has no sibling checkout: the marketplace install syncs the repo
    # from GitHub into <home>/marketplace-cache (bootstrap.ensure_marketplace_source),
    # and that is the ONLY place the plugin source exists there. Until this entry was
    # added the loader never looked in it, so install -> enable -> panel all worked and
    # every /video/* call answered 503.
    locs.append((home / "marketplace-cache" / "Corvin-Marketplace" / "plugins" / "contributor"
                 / "media" / "video_producer" / "src", "github-cache"))
    return locs


def _load_plugin():
    for src, kind in _plugin_locations():
        if not (src / "__init__.py").is_file() or not (src / "storage.py").is_file():
            continue
        try:
            spec = importlib.util.spec_from_file_location(
                _PLUGIN_PKG, src / "__init__.py", submodule_search_locations=[str(src)],
            )
            pkg = importlib.util.module_from_spec(spec)
            sys.modules[_PLUGIN_PKG] = pkg
            spec.loader.exec_module(pkg)
            models = importlib.import_module(f"{_PLUGIN_PKG}.models")
            storage = importlib.import_module(f"{_PLUGIN_PKG}.storage")
        except Exception as e:  # noqa: BLE001
            logger.warning("video producer plugin at %s (%s) failed to import: %s", src, kind, type(e).__name__)
            for name in [n for n in sys.modules if n == _PLUGIN_PKG or n.startswith(_PLUGIN_PKG + ".")]:
                sys.modules.pop(name, None)
            continue
        runner_mod = None
        try:
            runner_mod = importlib.import_module(f"{_PLUGIN_PKG}.async_runner")
        except Exception as e:  # noqa: BLE001 — read routes still work without the producer
            logger.warning("video producer runner unavailable (%s): jobs cannot be started", type(e).__name__)
        logger.info("video producer plugin loaded from %s", kind)
        return models.VideoJob, storage.get_storage, (runner_mod.get_runner if runner_mod else None), kind
    return None, None, None, None


VideoJob, _get_storage, get_runner, PLUGIN_SOURCE = _load_plugin()
_load_lock = threading.Lock()


def _ensure_plugin() -> None:
    """Load the plugin source on demand.

    ``_load_plugin()`` above runs once, at import — i.e. BEFORE an operator has
    installed anything on a fresh install, so the source was never there to find and
    stayed missing until a restart. Retry whenever a request arrives and nothing is
    loaded yet. Cheap when loaded (one truthiness check), and a no-op once it succeeds.
    """
    global VideoJob, _get_storage, get_runner, PLUGIN_SOURCE
    if _get_storage is not None:
        return
    with _load_lock:
        if _get_storage is None:
            VideoJob, _get_storage, get_runner, PLUGIN_SOURCE = _load_plugin()


PLUGIN_ID = "video_producer"


async def _require_plugin_enabled(rec=Depends(require_session_csrf_on_mutation)):
    """The API lives and dies with the plugin: installed AND enabled for THIS tenant.

    The router is mounted unconditionally, so without this gate an uninstalled (or
    merely installed, consent not yet given) Video Producer still accepted jobs — and
    every job sends its narration text to Google TTS. Fail-closed: an unreadable
    registry means "not enabled". 404, like every other absent surface.
    """
    from .capabilities import _plugin_is_enabled  # noqa: PLC0415

    if not await asyncio.to_thread(_plugin_is_enabled, rec.tenant_id, PLUGIN_ID):
        raise HTTPException(status_code=404, detail="The Video Producer plugin is not installed and enabled for this tenant")
    await asyncio.to_thread(_ensure_plugin)
    return rec


router = APIRouter(
    dependencies=[Depends(require_session_csrf_on_mutation), Depends(_require_plugin_enabled)],
    prefix="/video", tags=["video-producer"],
)

_SessionRec = Depends(require_session_csrf_on_mutation)
_JOB_ID_RE = re.compile(r"^job_[0-9a-f]{8}$")
_SCENE_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_TASK_CHARS = 4000
_MEASURE_WINDOW = 100
_UNAVAILABLE = "Video Producer plugin not available"


def _tenant_base(rec) -> Path:
    from core.paths.tenant import tenant_home  # validates the tenant id

    return tenant_home(rec.tenant_id) / "video_producer"


def _store(rec):
    _ensure_plugin()  # also reached from video_learning_api, which has no router gate
    if not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    return _get_storage(str(_tenant_base(rec)))


def _check_job_id(job_id: str) -> None:
    if not _JOB_ID_RE.match(job_id):
        raise HTTPException(status_code=404, detail="Job not found")


def _inside(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except (ValueError, OSError):
        return False


def _own_file(rec, raw_path: Optional[str]) -> Optional[Path]:
    """A stored artifact path, but only if it lies inside THIS tenant's store."""
    if not raw_path:
        return None
    p = Path(raw_path)
    if not _inside(p, _tenant_base(rec)) or not p.is_file():
        return None
    return p


# ── Models ───────────────────────────────────────────────────────────────────

class CreateJobRequest(BaseModel):
    task: str = Field(..., max_length=_MAX_TASK_CHARS)


class JobResponse(BaseModel):
    id: str
    task: str
    status: str
    created_at: str
    percent: int = 0
    current_step: Optional[str] = None
    current_scene: Optional[int] = None
    total_scenes: Optional[int] = None


class JobDetailResponse(JobResponse):
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    error_message: Optional[str] = None
    storyboard: Optional[dict] = None
    video_output_path: Optional[str] = None
    encoding_status: Optional[str] = None
    encoding_progress: int = 0
    quality_score: Optional[float] = None
    timing_issues: Optional[List[dict]] = None


class SceneFeedbackRequest(BaseModel):
    feedback_type: Literal["approve", "reject"]
    reason: Optional[str] = Field(None, max_length=500)
    confidence: float = Field(0.5, ge=0.0, le=1.0)
    quality_rating: Optional[int] = Field(None, ge=1, le=5)


class SettingsRequest(BaseModel):
    output_folder: Optional[str] = None
    tts_engine: Optional[Literal["openai", "auto", "gtts"]] = None
    max_duration_minutes: Optional[int] = Field(None, ge=1, le=60)


# Narration engines the producer really has (skill.SUPPORTED_TTS_ENGINES):
#   openai  OpenAI TTS (tts-1-hd, voice onyx) — the default; a missing key or an API failure
#           fails the job instead of silently switching the narrator
#   auto    the fallback chain: OpenAI -> edge-tts -> piper -> silent mock (ADR-2211)
#   gtts    Google Translate TTS (legacy, no key)
_TTS_ENGINES = ["openai", "auto", "gtts"]
_DEFAULT_SETTINGS = {"tts_engine": "openai", "max_duration_minutes": 60}
# Per tenant, in memory (reset on restart).
_settings: Dict[str, Dict[str, Any]] = {}


def _tenant_settings(rec) -> Dict[str, Any]:
    s = _settings.setdefault(rec.tenant_id, dict(_DEFAULT_SETTINGS))
    return {
        **s,
        # Fixed: videos always land in the tenant's own store. A settable
        # folder let any session aim every tenant's output at any directory
        # the console process can write (e.g. the audit-chain directory).
        "output_folder": str(_tenant_base(rec) / "videos"),
        "output_folder_editable": False,
        "tts_engines": list(_TTS_ENGINES),
        # What this host can actually do, so the panel can say so instead of a job failing later.
        "openai_configured": _openai_configured(),
        "web_slides_available": importlib.util.find_spec("playwright") is not None,
    }


# ── Jobs ─────────────────────────────────────────────────────────────────────

def _storyboard_backend(tenant_id: str, chat_key: str) -> tuple:
    """Where the storyboard LLM call goes. The host decides, after its own
    egress gate: Anthropic only when a key is configured AND L35 admits
    api.anthropic.com for this tenant; otherwise the local Ollama instance."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "ollama", None
    try:
        from spawn_gates import check_l35  # type: ignore  # path set up by _spawn_gates
        if check_l35("claude_code", tenant_id, channel="web", chat_key=chat_key) is not None:
            return "ollama", None
        import model_selector  # type: ignore  # bridges/shared, same import as chat_runtime
        return "anthropic", model_selector.top_model()
    except Exception as e:  # noqa: BLE001 — any doubt → the local backend
        logger.warning("video producer: storyboard backend check failed (%s) — using local", type(e).__name__)
        return "ollama", None


# The gate must name the host the narration really goes to (ADR-2211): the engine id picks
# the host in egress_gate.DEFAULT_ENGINE_HOSTS and the floor in data_classification.
_TTS_GATE_ENGINE = {"openai": "video_producer_openai", "auto": "video_producer_openai", "gtts": "video_producer"}
# "auto" may fall through to these tiers, so each of their hosts must be admitted too.
_TTS_EXTRA_GATE_ENGINES = {"auto": ("video_producer_edge",)}


def _tts_egress_refusal(tts_engine: str, tenant_id: str, chat_key: str) -> Optional[str]:
    """Refusal text if an additional host the chosen engine may reach is not admitted, else None."""
    extra = _TTS_EXTRA_GATE_ENGINES.get(tts_engine, ())
    if not extra:
        return None
    from spawn_gates import check_l35  # type: ignore  # path set up by _spawn_gates

    for engine_id in extra:
        refusal = check_l35(engine_id, tenant_id, channel="web", chat_key=chat_key)
        if refusal is not None:
            return refusal
    return None


def _openai_configured() -> bool:
    """A TTS key is in this process's environment (the same two names the plugin reads)."""
    return bool(os.environ.get("CORVIN_TTS_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY"))


def _missing_runtime_dependencies(tts_engine: str = "openai") -> List[str]:
    """What a job needs on THIS host that is not there: the narration engine's package
    (and key), Pillow (classic slides) and ffmpeg (assembly). The plugin runs inside the
    console process, so a marketplace install cannot add Python packages to it — better a
    named refusal now than a job that dies 40 s in. Playwright is NOT required: without it
    web slides fall back to the classic slide, which the panel shows (``web_slides_available``)."""
    missing: List[str] = []
    if tts_engine == "gtts" and importlib.util.find_spec("gtts") is None:
        missing.append("gTTS (pip install 'gTTS>=2.5.0' into the console environment)")
    if tts_engine == "openai":
        if importlib.util.find_spec("openai") is None:
            missing.append("openai (pip install 'openai>=1.0.0' into the console environment)")
        if not _openai_configured():
            missing.append("an OpenAI TTS key (CORVIN_TTS_OPENAI_KEY or OPENAI_API_KEY in the console's environment) — "
                           "or pick another narration engine in the Video Producer settings")
    if importlib.util.find_spec("PIL") is None:
        missing.append("Pillow (pip install 'Pillow>=10.0.0' into the console environment)")
    if shutil.which("ffmpeg") is None:
        missing.append("ffmpeg (system binary, must be on PATH)")
    return missing


@router.post("/jobs")
async def create_video_job(req: CreateJobRequest, rec=_SessionRec):
    """Create a job and start production in the background (non-blocking)."""
    task = (req.task or "").strip()
    if not task:
        raise HTTPException(status_code=400, detail="Task cannot be empty")
    if not VideoJob or not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    if not get_runner:
        # Refuse instead of saving a job that nothing will ever run.
        raise HTTPException(status_code=503, detail="Video production is not available on this build (runner missing)")
    settings = _tenant_settings(rec)
    missing = await asyncio.to_thread(_missing_runtime_dependencies, settings["tts_engine"])
    if missing:
        raise HTTPException(status_code=503, detail="Video production needs: " + "; ".join(missing))

    job_id = f"job_{uuid.uuid4().hex[:8]}"
    chat_key = f"video:{job_id}"

    # Pre-spawn gates (L44 acceptable-use, capabilities, L34 classification,
    # L35 egress) — the same function every console spawn site calls. The
    # task text becomes narration sent to the TTS provider on every job, so the
    # gate runs under the engine profile of the chosen narration engine (US cloud).
    from .._spawn_gates import check_console_spawn_or_refusal  # noqa: PLC0415

    refusal = await asyncio.to_thread(
        check_console_spawn_or_refusal, task,
        tenant_id=rec.tenant_id, persona="assistant", channel="web",
        chat_key=chat_key, engine_id=_TTS_GATE_ENGINE[settings["tts_engine"]],
    )
    if refusal is None:
        refusal = await asyncio.to_thread(_tts_egress_refusal, settings["tts_engine"], rec.tenant_id, chat_key)
    if refusal is not None:
        raise HTTPException(status_code=403, detail=refusal)

    backend, model = await asyncio.to_thread(_storyboard_backend, rec.tenant_id, chat_key)
    storage = _store(rec)
    job = VideoJob(id=job_id, task=task, status="pending")
    storage.save_job(job)

    config = {
        "storage_base": str(_tenant_base(rec)),
        "tts_engine": settings["tts_engine"],
        "max_duration_minutes": settings["max_duration_minutes"],
        "storyboard_backend": backend,
        "storyboard_model": model,
    }
    try:
        await get_runner().start_job(job_id, task, config)
    except Exception as e:  # noqa: BLE001
        logger.error("[%s] could not start job (%s)", job_id, type(e).__name__)
        from datetime import datetime  # noqa: PLC0415

        job.status = "error"
        job.error_message = "The job could not be started."
        job.completed_at = datetime.now()
        storage.save_job(job)
        raise HTTPException(status_code=500, detail="Failed to start the job") from None

    return {"job_id": job_id, "status": "pending", "created_at": job.created_at.isoformat()}


@router.get("/jobs/{job_id}")
def get_job_status(job_id: str, rec=_SessionRec):
    _check_job_id(job_id)
    job = _store(rec).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobDetailResponse(
        id=job.id,
        task=job.task,
        status=job.status,
        created_at=job.created_at.isoformat(),
        started_at=job.started_at.isoformat() if job.started_at else None,
        completed_at=job.completed_at.isoformat() if job.completed_at else None,
        error_message=job.error_message,
        video_output_path=job.video_output_path,
        percent=getattr(job, "percent", 0),
        current_step=getattr(job, "current_step", None),
        current_scene=getattr(job, "current_scene", None),
        total_scenes=getattr(job, "total_scenes", None),
    )


@router.get("/jobs")
def list_jobs(limit: int = 20, offset: int = 0, rec=_SessionRec):
    if limit <= 0 or limit > 100:
        raise HTTPException(status_code=400, detail="Limit must be between 1 and 100")
    if offset < 0:
        raise HTTPException(status_code=400, detail="Offset cannot be negative")
    storage = _store(rec)
    jobs = storage.list_jobs(limit=limit, offset=offset)
    return {
        "jobs": [
            JobResponse(
                id=j.id,
                task=j.task,
                status=j.status,
                created_at=j.created_at.isoformat(),
                percent=getattr(j, "percent", 0),
                current_step=getattr(j, "current_step", None),
                current_scene=getattr(j, "current_scene", None),
                total_scenes=getattr(j, "total_scenes", None),
            )
            for j in jobs
        ],
        "count": len(jobs),
        "offset": offset,
        "limit": limit,
        "total": storage.get_job_count(),
    }


@router.get("/jobs/{job_id}/progress")
def get_job_progress(job_id: str, rec=_SessionRec):
    _check_job_id(job_id)
    job = _store(rec).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return {
        "job_id": job_id,
        "status": job.status,
        "encoding_progress": getattr(job, "percent", 0),
        "current_scene": getattr(job, "current_scene", None),
        "total_scenes": getattr(job, "total_scenes", None),
        "current_step": getattr(job, "current_step", None),
    }


# ── Settings ─────────────────────────────────────────────────────────────────

@router.get("/settings")
def get_settings(rec=_SessionRec):
    return _tenant_settings(rec)


@router.put("/settings")
def update_settings(req: SettingsRequest, rec=_SessionRec):
    current = _tenant_settings(rec)
    if req.output_folder is not None and req.output_folder != current["output_folder"]:
        raise HTTPException(status_code=400, detail="The output folder is fixed to this tenant's video directory")
    s = _settings.setdefault(rec.tenant_id, dict(_DEFAULT_SETTINGS))
    if req.tts_engine is not None:
        s["tts_engine"] = req.tts_engine
    if req.max_duration_minutes is not None:
        s["max_duration_minutes"] = req.max_duration_minutes
    return {"status": "ok", "settings": _tenant_settings(rec)}


# ── Artifacts ────────────────────────────────────────────────────────────────

def _video_output(rec, job_id: str):
    _check_job_id(job_id)
    out = _store(rec).get_video_output(job_id)
    if not out:
        raise HTTPException(status_code=404, detail="Video not found")
    return out


@router.get("/videos/{job_id}/download")
def download_video(job_id: str, rec=_SessionRec):
    from fastapi.responses import FileResponse  # noqa: PLC0415

    path = _own_file(rec, _video_output(rec, job_id).video_path)
    if path is None:
        raise HTTPException(status_code=404, detail="Video file not found")
    return FileResponse(str(path), media_type="video/mp4", filename=f"video_{job_id}.mp4")


@router.post("/jobs/{job_id}/youtube")
def upload_to_youtube(job_id: str, rec=_SessionRec):
    """Not built: no upload runs, so nothing is reported as queued."""
    _video_output(rec, job_id)
    raise HTTPException(status_code=501, detail="YouTube upload is not available on this build")


def _job_dict(job) -> dict:
    """The stored job as a plain dict, timestamps as ISO."""
    data = dict(job.to_dict()) if hasattr(job, "to_dict") else dict(vars(job))
    for k in ("created_at", "started_at", "completed_at"):
        v = data.get(k)
        if hasattr(v, "isoformat"):
            data[k] = v.isoformat()
    return data


def _output_dict(video_output) -> Optional[dict]:
    if video_output is None:
        return None
    return dict(video_output.to_dict()) if hasattr(video_output, "to_dict") else dict(vars(video_output))


# Sync handlers: measure() runs ffprobe subprocesses, which must not block
# the event loop — FastAPI runs a plain `def` in its worker threadpool.
@router.get("/jobs/{job_id}/quality-metrics")
def get_quality_metrics(job_id: str, rec=_SessionRec):
    _check_job_id(job_id)
    storage = _store(rec)
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    from ..video_quality import measure  # noqa: PLC0415

    return measure(_job_dict(job), _output_dict(storage.get_video_output(job_id)))


@router.get("/overview")
def get_overview(rec=_SessionRec):
    """This tenant's library at a glance. Counts span ALL jobs; runtime, size
    and the mean checklist share are measured over the newest
    ``measure_window`` complete jobs (the denominator is named)."""
    from ..video_quality import ffprobe_available, measure  # noqa: PLC0415

    storage = _store(rec)
    by_status = storage.count_by_status()
    runtime = 0.0
    size = 0
    shares: list = []
    last = None
    for j in storage.list_jobs(limit=_MEASURE_WINDOW, offset=0):
        if j.status == "complete":
            q = measure(_job_dict(j), _output_dict(storage.get_video_output(j.id)))
            runtime += q["summary"]["rendered_s"] or 0
            size += q["summary"]["size_bytes"] or 0
            if q["container"] and q["score"]["share"] is not None:
                shares.append(q["score"]["share"])
        ts = j.completed_at or j.created_at
        if ts and (last is None or ts > last):
            last = ts
    return {
        "jobs_total": sum(by_status.values()),
        "by_status": by_status,
        "videos": by_status.get("complete", 0),
        "measure_window": _MEASURE_WINDOW,
        "runtime_s": round(runtime, 1),
        "size_bytes": size,
        "measured_videos": len(shares),
        "mean_score_share": round(sum(shares) / len(shares), 3) if shares else None,
        "last_activity": last.isoformat() if hasattr(last, "isoformat") else last,
        "ffprobe_available": ffprobe_available(),
        "plugin_source": PLUGIN_SOURCE,
    }


def _scene_png(rec, job_id: str, name: str) -> Optional[Path]:
    out = _video_output(rec, job_id)
    if not out.video_path:
        return None
    return _own_file(rec, str(Path(out.video_path).parent / "scenes" / name))


@router.get("/videos/{job_id}/poster")
def get_poster(job_id: str, rec=_SessionRec):
    """The first rendered slide as the video's poster."""
    from fastapi.responses import FileResponse  # noqa: PLC0415

    for name in ("scene_001.png", "scene_000.png"):
        p = _scene_png(rec, job_id, name)
        if p is not None:
            return FileResponse(str(p), media_type="image/png")
    raise HTTPException(status_code=404, detail="No poster for this video")


@router.get("/videos/{job_id}/scenes/{index}/slide")
def get_scene_slide(job_id: str, index: int, rec=_SessionRec):
    from fastapi.responses import FileResponse  # noqa: PLC0415

    if index < 1 or index > 999:
        raise HTTPException(status_code=400, detail="scene index out of range")
    p = _scene_png(rec, job_id, f"scene_{index:03d}.png")
    if p is None:
        raise HTTPException(status_code=404, detail="No slide for this scene")
    return FileResponse(str(p), media_type="image/png")


# ── Feedback ─────────────────────────────────────────────────────────────────

@router.post("/jobs/{job_id}/scenes/{scene_id}/feedback")
async def submit_scene_feedback(
    job_id: str,
    scene_id: str,
    feedback: SceneFeedbackRequest,
    rec=_SessionRec,
):
    """Record operator feedback for one scene in the learning loop (ADR-0314).

    Validated at the boundary (422 on a bad type/range), recorded audit-first
    on the tenant's own store, and the response says whether it WAS recorded —
    503 when it was not. The free-text reason is never echoed or persisted.
    """
    _check_job_id(job_id)
    if not _SCENE_ID_RE.match(scene_id):
        raise HTTPException(status_code=400, detail="invalid scene id")
    if not _store(rec).get_job(job_id):
        raise HTTPException(status_code=404, detail="Job not found")

    from .feedback_emitter_helper import emit_feedback_event

    audit_ref = await emit_feedback_event(
        skill_id="os.video_producer",
        task_id=job_id,
        tenant_id=rec.tenant_id,
        outcome_feedback={"approve": "yes", "reject": "no"}[feedback.feedback_type],
        quality_rating=feedback.quality_rating,
        reason=feedback.reason,
        confidence=feedback.confidence,
        source="user",
        lom="corvin_console.routes.video_producer_api:submit_scene_feedback",
        scene_id=scene_id,
    )
    if not audit_ref:
        raise HTTPException(status_code=503, detail="Feedback could not be recorded")
    return {
        "job_id": job_id,
        "scene_id": scene_id,
        "feedback_type": feedback.feedback_type,
        "status": "recorded",
        "audit_ref": audit_ref,
    }
