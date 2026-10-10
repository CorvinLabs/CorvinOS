"""FastAPI routes for the Video Producer plugin.

The plugin source (models / storage / skill / async_runner) lives in the
marketplace (contributor/media/video_producer/src) and is loaded here as ONE
private package. Every route is tenant-scoped: each tenant has its own store
under ``tenant_home(rec.tenant_id)/video_producer`` and its own settings, so
no tenant can list, read or download another tenant's jobs.
"""

from __future__ import annotations

import asyncio
import collections
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

from fastapi import APIRouter, Depends, File, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from ..deps import require_session_csrf_on_mutation

logger = logging.getLogger(__name__)

# ── Plugin loading ───────────────────────────────────────────────────────────

# Loaded under ONE private package name. Until 2026-10-03 the modules were also
# registered as the bare top-level names "models", "storage" and "skill", so any
# later `import models` anywhere in the console process got the video
# producer's module instead.
_PLUGIN_PKG = "corvin_video_producer_plugin"

# How jobs are executed: "process" = one OS process (own process group) per job via the plugin's
# ProcessJobRunner (PLAN-0946 R1); "thread" = the in-process thread pool. One line to flip back.
# An installed plugin that predates ProcessJobRunner falls back to threads.
_JOB_RUNNER = "thread"

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
        getter = None
        if runner_mod is not None:
            getter = runner_mod.get_runner
            if _JOB_RUNNER == "process" and hasattr(runner_mod, "get_process_runner"):
                getter = runner_mod.get_process_runner
        return models.VideoJob, storage.get_storage, getter, kind
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
_MAX_SOURCE_CHARS = 20_000
_MAX_SOURCES = 4
_MAX_SOURCES_TOTAL = 40_000
_MAX_UPLOAD_BYTES = 2 * 1024 * 1024
_SCENE_NARRATION_CHARS = 600
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

class SourceItem(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)
    text: str = Field(..., min_length=1, max_length=_MAX_SOURCE_CHARS)


class CreateJobRequest(BaseModel):
    task: str = Field(..., max_length=_MAX_TASK_CHARS)
    # Revision: a change request against a produced video of this tenant. The original stays.
    base_job_id: Optional[str] = Field(None, max_length=32)
    # Source material extracted from attachments (POST /video/attachments/extract).
    sources: List[SourceItem] = Field(default_factory=list, max_length=_MAX_SOURCES)
    # PLAN-0945: absent = the tenant's default style (else built-in); "corvin" = built-in; else one of THIS tenant's styles.
    style_id: Optional[str] = Field(None, max_length=32)


class JobResponse(BaseModel):
    id: str
    task: str
    revision_of: Optional[str] = None
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

def _claude_cli_available() -> bool:
    pinned = (os.environ.get("CORVIN_CLAUDE_BIN") or "").strip()
    if pinned:
        return os.path.isfile(pinned) and os.access(pinned, os.X_OK)
    return shutil.which("claude") is not None


def _storyboard_backend(tenant_id: str, chat_key: str, task: str) -> tuple:
    """Where the storyboard LLM call goes. The host decides, after its own gates:
    the Anthropic API when a key is configured, else this host's Claude Code
    login (the ``claude`` CLI the console already runs), else local Ollama.
    Both remote paths send the task text to api.anthropic.com, so both need the
    L35 egress gate AND the L34 classification of the task under the
    ``claude_code`` engine; any refusal or doubt keeps the call local.
    Sonnet, not Opus: on the real storyboard prompt both produced fully valid,
    equally varied template choices, Sonnet in ~14 s for ~$0.03 vs ~17-42 s
    for ~$0.06 (measured 2026-10-08, ADR-2238 amendment)."""
    api_key = bool(os.environ.get("ANTHROPIC_API_KEY"))
    if not api_key and not _claude_cli_available():
        return "ollama", None
    try:
        from spawn_gates import check_l34, check_l35  # type: ignore  # path set up by _spawn_gates
        if check_l35("claude_code", tenant_id, channel="web", chat_key=chat_key) is not None:
            return "ollama", None
        if check_l34("claude_code", tenant_id, prompt=task, persona="assistant",
                     channel="web", chat_key=chat_key) is not None:
            return "ollama", None
        import model_selector  # type: ignore  # bridges/shared, same import as chat_runtime
        return ("anthropic" if api_key else "claude_cli"), model_selector.tier_model("sonnet")
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


def _grounding_audit(tenant_id: str, event: str, details: Dict[str, Any]) -> bool:
    """Write a grounding decision to the tenant chain; False if it did not commit."""
    try:
        from .. import _bootstrap  # noqa: PLC0415
        from forge.security_events import write_event  # type: ignore[import-not-found]  # noqa: PLC0415

        write_event(_bootstrap.forge_paths.tenant_audit_chain(tenant_id), event,
                    details={"tenant_id": tenant_id, **details})
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("video producer: grounding audit write failed (%s)", type(e).__name__)
        return False


_audit = _grounding_audit  # same tenant-chain writer for every video_producer.* event


def _grounding_for_job(tenant_id: str, chat_key: str, job_id: str, task: str, backend: str,
                       tts_engine: str) -> tuple:
    """(pack, status) for a job — PLAN-0942 D1/D2/D7.

    The knowledge base and the code are the OPERATOR's: only the tenant the KB belongs
    to (``CORVIN_KB_TENANT``, default ``_default``) can get a pack. The plugin builds it;
    this host decides: per decision the L34 heuristic (secret/PII shapes) under every
    engine the text may reach, then the whole pack through L44 + capabilities + L34 at
    the declared class INTERNAL + L35. Released and refused packs are audited
    (ids, digest, size — never text) BEFORE the job may use them."""
    try:
        from ..kb_projection import kb_repo, kb_tenant  # noqa: PLC0415
    except Exception:  # noqa: BLE001
        return None, None
    if tenant_id != kb_tenant():
        return None, None
    try:
        g = importlib.import_module(f"{_PLUGIN_PKG}.grounding")
    except Exception:  # noqa: BLE001 — an older plugin without grounding
        return None, None
    if not g.is_candidate_task(task):
        return None, None
    if backend == "ollama":
        return None, {"status": "unavailable", "reason": "local_storyboard_model"}
    kb = kb_repo()
    if kb is None:
        return None, {"status": "unavailable", "reason": "no_knowledge_base"}
    roots = [g.CodeRoot(_repo_root)]
    market = _repo_root.parent / "Corvin-Marketplace"
    if (market / ".git").exists():
        roots.append(g.CodeRoot(market, "Corvin-Marketplace/", ("plugins/buildin/",)))
    try:
        pack = g.build_pack(task, kb, roots, deadline_s=8.0)
    except Exception as e:  # noqa: BLE001
        logger.warning("video producer: grounding pack failed (%s)", type(e).__name__)
        return None, {"status": "unavailable", "reason": "pack_build_failed"}
    if pack is None:
        return None, None

    from spawn_gates import check_l34  # type: ignore  # path set up by _spawn_gates
    from .._spawn_gates import check_console_spawn_or_refusal  # noqa: PLC0415

    engines = ["claude_code", _TTS_GATE_ENGINE[tts_engine], *_TTS_EXTRA_GATE_ENGINES.get(tts_engine, ())]
    keep, dropped = [], []
    for sec in pack["sections"]:
        try:
            refused = any(check_l34(e, tenant_id, prompt=sec["text"], persona="assistant", channel="web",
                                    chat_key=chat_key) is not None for e in engines)
        except Exception:  # noqa: BLE001 — doubt drops the decision
            refused = True
        (dropped if refused else keep).append(sec["id"])
    base = {"job_id": job_id, "engines": ",".join(engines)}
    if not keep:
        _grounding_audit(tenant_id, "video_producer.grounding_refused",
                         {**base, "entity_ids": ",".join(dropped), "gate": "l34_per_decision"})
        return None, {"status": "refused", "reason": "l34"}
    pack = g.with_sections(pack, keep)
    text = g.render_pack(pack)
    digest = g.pack_digest(pack)[:16]
    for engine in engines:
        refusal = check_console_spawn_or_refusal(
            text, tenant_id=tenant_id, persona="assistant", channel="web", chat_key=chat_key,
            engine_id=engine, classification="INTERNAL",
        )
        if refusal is not None:
            _grounding_audit(tenant_id, "video_producer.grounding_refused",
                             {**base, "entity_ids": ",".join(keep), "pack_sha256": digest, "chars": len(text),
                              "gate": f"pack:{engine}"})
            return None, {"status": "refused", "reason": "pack_gate"}
    if not _grounding_audit(tenant_id, "video_producer.grounding_released",
                            {**base, "entity_ids": ",".join(keep), "dropped_ids": ",".join(dropped),
                             "pack_sha256": digest, "chars": len(text)}):
        return None, {"status": "unavailable", "reason": "audit_write_failed"}
    return pack, None


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


# ── Revisions, sources, attachments ──────────────────────────────────────────

_REVISIONS_FILE = "revisions.json"


def _revisions(rec) -> Dict[str, str]:
    """job id -> the job it revises (tenant-scoped sidecar; the plugin's job record is untouched)."""
    import json  # noqa: PLC0415

    try:
        data = json.loads((_tenant_base(rec) / _REVISIONS_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in data.items() if _JOB_ID_RE.match(str(k)) and _JOB_ID_RE.match(str(v))} if isinstance(data, dict) else {}


def _record_revision(rec, job_id: str, base_job_id: str) -> None:
    import json  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    base = _tenant_base(rec)
    base.mkdir(parents=True, exist_ok=True)
    # read-modify-write under a cross-process lock where the platform has one (two host processes can share a tenant)
    with open(base / ".revisions.lock", "a") as lock_fh:
        try:
            import fcntl  # noqa: PLC0415

            fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX)
        except ImportError:  # Windows: the single-process event loop already serialises this function
            pass
        data = _revisions(rec)
        data[job_id] = base_job_id
        fd, tmp = tempfile.mkstemp(dir=base, prefix=".revisions.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh)
            os.chmod(tmp, 0o600)
            os.replace(tmp, base / _REVISIONS_FILE)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


_LANG_NAMES = {"de": "German", "en": "English"}

# One tenant cannot fill the host's job workers (three, shared by every tenant) or queue without
# bound, each job spending TTS money (review 2026-10-10).
_MAX_ACTIVE_JOBS_PER_TENANT = 2
_ACTIVE_JOBS_LOCK = threading.Lock()
_TERMINAL = frozenset({"complete", "error", "cancelled"})


def _active_job_count(storage) -> int:
    return sum(n for status, n in storage.count_by_status().items() if status not in _TERMINAL)


def _revision_brief(base_job, instruction: str, lang: Optional[str] = None) -> str:
    """The task text of a revision: the previous storyboard plus the change request. ``lang`` is the
    base video's measured narration language (its output metadata); it is named, not left implied."""
    sb = getattr(base_job, "storyboard", None)
    scenes = list(getattr(sb, "scenes", None) or [])
    if not scenes:
        raise HTTPException(status_code=409, detail="The video to revise has no storyboard")
    lines = []
    for i, sc in enumerate(scenes, 1):
        narr = " ".join((getattr(sc, "narration_text", "") or "").split())[:_SCENE_NARRATION_CHARS]
        lines.append(f"Scene {i} [{getattr(sc, 'template', None) or getattr(sc, 'kind', '')}]: {narr}")
    return (
        "REVISION OF AN EXISTING VIDEO. Produce the video again with the change request applied. Keep every scene "
        "the request does not touch (same template, same content, same narration) and keep the narration language "
        "of the current storyboard; change only what is asked.\n"
        + (f"Narration language: {_LANG_NAMES[lang]} - every scene, also the changed ones.\n" if lang in _LANG_NAMES else "")
        +
        f"Original task: {' '.join((base_job.task or '').split())[:1500]}\n"
        "Current storyboard:\n" + "\n".join(lines) + "\n"
        f"Change request: {instruction}"
    )


def _sources_block(sources) -> str:
    if not sources:
        return ""
    if sum(len(x.text) for x in sources) > _MAX_SOURCES_TOTAL:
        raise HTTPException(status_code=413, detail=f"Source material is limited to {_MAX_SOURCES_TOTAL} characters in total")
    parts = [f"--- SOURCE MATERIAL {i} ({x.name}) ---\n{x.text}" for i, x in enumerate(sources, 1)]
    return ("\n\nSOURCE MATERIAL (attached by the operator; use it as the factual basis of the video, "
            "do not invent beyond it):\n" + "\n".join(parts))


_TEXT_EXT = {".txt", ".md", ".markdown", ".json", ".csv", ".log", ".yaml", ".yml"}
_PDF_SEM = threading.BoundedSemaphore(1)
_PDF_TIMEOUT_S = 30
_PDF_MAX_PAGES = 200
_PDF_MEM_BYTES = 512 * 1024 * 1024



def _extract_text(name: str, data: bytes) -> str:
    ext = Path(name).suffix.lower()
    if ext in _TEXT_EXT:
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise HTTPException(status_code=415, detail=f"{name}: not valid UTF-8 text") from None
    if ext == ".pdf":
        import shutil as _sh  # noqa: PLC0415
        import subprocess  # noqa: PLC0415

        exe = _sh.which("pdftotext")
        if not exe:
            raise HTTPException(status_code=415, detail="PDF extraction needs pdftotext (poppler-utils) on this host")
        # A 0.5 MB PDF with one deflated content stream drove pdftotext to 2.5 GB for 30 s
        # (review 2026-10-10): one extraction at a time, bounded memory/CPU/pages/output.
        if not _PDF_SEM.acquire(timeout=_PDF_TIMEOUT_S):
            raise HTTPException(status_code=429, detail="Another PDF is being read; try again in a moment")
        try:
            argv = [exe, "-q", "-l", str(_PDF_MAX_PAGES), "-enc", "UTF-8", "-", "-"]
            if os.name == "posix" and _sh.which("sh"):
                # rlimits via the shell (preexec_fn is unsafe in a threaded server); exe and args are
                # positional parameters, never part of the script text
                argv = ["sh", "-c", f'ulimit -v {_PDF_MEM_BYTES // 1024} && ulimit -t {_PDF_TIMEOUT_S} && exec "$@"', "pdftotext", *argv]
            r = subprocess.run(argv, input=data, capture_output=True, timeout=_PDF_TIMEOUT_S, check=False)
        except subprocess.TimeoutExpired:
            raise HTTPException(status_code=422, detail=f"{name}: PDF extraction timed out") from None
        finally:
            _PDF_SEM.release()
        if r.returncode != 0:
            raise HTTPException(status_code=422, detail=f"{name}: not a readable PDF")
        return r.stdout[: _MAX_SOURCE_CHARS * 4].decode("utf-8", "replace")
    raise HTTPException(status_code=415, detail=f"{name}: unsupported type (text, Markdown, JSON, CSV, YAML or PDF)")


@router.post("/attachments/extract")
async def extract_attachments(files: List[UploadFile] = File(...), rec=_SessionRec):
    """Turn uploads into source-material text. Nothing is stored; the text travels with the job request,
    where the pre-spawn gates (L44, L34, L35) see it as part of the task."""
    if not files or len(files) > _MAX_SOURCES:
        raise HTTPException(status_code=400, detail=f"Attach 1 to {_MAX_SOURCES} files")
    out = []
    for f in files:
        name = Path(f.filename or "attachment").name[:120] or "attachment"
        data = await f.read(_MAX_UPLOAD_BYTES + 1)
        if not data:
            raise HTTPException(status_code=400, detail=f"{name}: empty file")
        if len(data) > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail=f"{name}: larger than 2 MiB")
        text = (await asyncio.to_thread(_extract_text, name, data)).replace("\x00", "").strip()
        if not text:
            raise HTTPException(status_code=422, detail=f"{name}: no text found")
        out.append({"name": name, "text": text[:_MAX_SOURCE_CHARS], "chars": len(text), "truncated": len(text) > _MAX_SOURCE_CHARS})
    return {"sources": out}


@router.post("/jobs")
async def create_video_job(req: CreateJobRequest, rec=_SessionRec):
    """Create a job and start production in the background (non-blocking)."""
    shown = (req.task or "").strip()
    if not shown:
        raise HTTPException(status_code=400, detail="Task cannot be empty")
    base_job = None
    if req.base_job_id:
        _check_job_id(req.base_job_id)
    task = shown
    if not VideoJob or not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    if not get_runner:
        # Refuse instead of saving a job that nothing will ever run.
        raise HTTPException(status_code=503, detail="Video production is not available on this build (runner missing)")
    if req.base_job_id:
        base_job = _store(rec).get_job(req.base_job_id)
        if not base_job:
            raise HTTPException(status_code=404, detail="The video to revise was not found")
        if base_job.status != "complete":
            raise HTTPException(status_code=409, detail="Only a produced video can be revised")
        base_out = _store(rec).get_video_output(base_job.id)
        base_lang = ((getattr(base_out, "metadata", None) or {}).get("language")) if base_out else None
        task = _revision_brief(base_job, shown, base_lang)
    task += _sources_block(req.sources)
    web_style = await asyncio.to_thread(_resolve_job_style, rec, req.style_id, req.base_job_id)
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

    backend, model = await asyncio.to_thread(_storyboard_backend, rec.tenant_id, chat_key, task)
    grounding_pack, grounding_status = await asyncio.to_thread(
        _grounding_for_job, rec.tenant_id, chat_key, job_id, task, backend, settings["tts_engine"],
    )
    # audit-first: the record exists before the style can reach a pixel
    # (a plain job that never chose a style - no default, no style_id - has nothing to record)
    if (web_style is not None or req.style_id == _BUILTIN_STYLE_ID) and not _audit(
            rec.tenant_id, "video_producer.style_applied",
            {"job_id": job_id, "style_id": web_style.id if web_style is not None else _BUILTIN_STYLE_ID}):
        raise HTTPException(status_code=503, detail="The style could not be recorded; the job was not started")
    storage = _store(rec)
    job = VideoJob(id=job_id, task=shown, status="pending")
    with _ACTIVE_JOBS_LOCK:  # count + save is one step, so two parallel requests cannot both pass
        if _active_job_count(storage) >= _MAX_ACTIVE_JOBS_PER_TENANT:
            raise HTTPException(status_code=429, detail=(
                f"{_MAX_ACTIVE_JOBS_PER_TENANT} videos are already being produced for this workspace; "
                "start the next one when one of them has finished"))
        storage.save_job(job)
    if req.base_job_id:
        _record_revision(rec, job_id, req.base_job_id)

    config = {
        "storage_base": str(_tenant_base(rec)),
        "tts_engine": settings["tts_engine"],
        "max_duration_minutes": settings["max_duration_minutes"],
        "storyboard_backend": backend,
        "storyboard_model": model,
    }
    if web_style is not None:
        config["web_style"] = web_style
    if grounding_pack is not None:
        config["grounding_pack"] = grounding_pack
    elif grounding_status is not None:
        config["grounding_status"] = grounding_status
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
        revision_of=_revisions(rec).get(job.id),
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
    revs = _revisions(rec)
    return {
        "jobs": [
            JobResponse(
                id=j.id,
                task=j.task,
                revision_of=revs.get(j.id),
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


# ── Styles (PLAN-0945 P2) ────────────────────────────────────────────────────
# A style is the tenant's own look (palette, fonts, logo, decoration, a logo mark).
# The client is never trusted: ids, timestamps and provenance are server-made, every draft is fully
# re-validated by the plugin, and a job can only ever name a style by an id that resolves in THIS
# tenant's store - no client path, no client tokens.

_BUILTIN_STYLE_ID = "corvin"
_BUILTIN_STYLE_NAME = "CorvinOS"
_MAX_STYLE_UPLOAD_BYTES = 25 * 1024 * 1024
_MAX_STYLE_JSON_BYTES = 8 * 1024 * 1024  # two 2 MiB PNGs as base64 plus the tokens, with headroom
_DRAFT_PLACEHOLDER_ID = "sty_00000000"
_STYLE_NOT_FOUND = "Style not found"
# One browser per preview, at most this many at once on the whole host.
_preview_slots = threading.BoundedSemaphore(2)
# Deck imports are CPU-bound (zip + XML + image work): two at a time host-wide, and a bounded
# queue behind them, so a burst of uploads cannot starve the event loop's thread pool.
_IMPORT_RUNNING = 2
_IMPORT_MAX_INFLIGHT = 8  # running + waiting
_import_slots = threading.BoundedSemaphore(_IMPORT_RUNNING)
_import_lock = threading.Lock()
_import_inflight = 0
# Acceptable-use verdicts for brand text, so a debounced preview does not respawn the classifier
# on every keystroke: (tenant, sha256(name+wordmark)) -> (monotonic time, refusal or None).
_GATE_CACHE_MAX = 128
_GATE_CACHE_TTL_S = 600.0
_gate_cache: "collections.OrderedDict[tuple, tuple]" = collections.OrderedDict()
_gate_cache_lock = threading.Lock()


def _style_mod(name: str):
    _ensure_plugin()
    if not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    try:
        return importlib.import_module(f"{_PLUGIN_PKG}.{name}")
    except ImportError:
        raise HTTPException(status_code=501, detail="Custom styles are not available on this build") from None


def _style_store(rec):
    return _style_mod("style_store").StyleStore(str(_tenant_base(rec)))


def _now_iso() -> str:
    from datetime import datetime, timezone  # noqa: PLC0415

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_own_style(rec, style_id: str):
    """One of THIS tenant's styles; the same 404 for an unknown id, a malformed id and another tenant's id."""
    sm, sp = _style_mod("style_store"), _style_mod("style_pack")
    if not isinstance(style_id, str) or not sp.ID_RE.fullmatch(style_id):
        raise HTTPException(status_code=404, detail=_STYLE_NOT_FOUND)
    try:
        return sm.StyleStore(str(_tenant_base(rec))).load(style_id)
    except sm.StyleNotFound:
        raise HTTPException(status_code=404, detail=_STYLE_NOT_FOUND) from None
    except sp.StyleError:
        raise HTTPException(status_code=409, detail="This style is damaged on disk; delete it and import it again") from None


def _resolve_job_style(rec, style_id: Optional[str], base_job_id: Optional[str]):
    """The Style object a job renders with, or None for the built-in Corvin look."""
    if style_id == _BUILTIN_STYLE_ID:
        return None
    if style_id is not None:
        return _load_own_style(rec, style_id)
    if base_job_id:
        # A revision keeps the look of the video it revises: the copy that travelled with it.
        # (A base without a snapshot was built-in, so the tenant default does not apply to it.)
        sm, sp = _style_mod("style_store"), _style_mod("style_pack")
        try:
            return sm.load_snapshot(_store(rec).videos_dir / base_job_id / "style")
        except sp.StyleError:
            # Never fall back to the built-in look: the video was NOT built-in, it just cannot be read.
            raise HTTPException(
                status_code=409,
                detail="The original video's style could not be read; pick a style explicitly.",
            ) from None
    default = _style_store(rec).get_default()
    if not default:
        return None
    try:
        return _load_own_style(rec, default)
    except HTTPException:
        # A damaged tenant default must not stop jobs that never asked for it; the styles list
        # reports it as default_style_error. An EXPLICIT style_id above still answers 409.
        logger.warning("video producer: the tenant default style is unreadable; using the built-in look")
        return None


async def _read_json_body(request, limit: int = _MAX_STYLE_JSON_BYTES) -> dict:
    import json  # noqa: PLC0415

    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > limit:
        raise HTTPException(status_code=413, detail="Request body is too large")
    buf = bytearray()
    async for chunk in request.stream():
        buf += chunk
        if len(buf) > limit:
            raise HTTPException(status_code=413, detail="Request body is too large")
    try:
        body = json.loads(bytes(buf).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, RecursionError):
        raise HTTPException(status_code=400, detail="Body must be JSON") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    return body


def _draft_style(wire: Any, style_id: str = _DRAFT_PLACEHOLDER_ID):
    sp = _style_mod("style_pack")
    try:
        return sp.draft_from_wire(wire, style_id=style_id, imported_at=_now_iso())
    except sp.StyleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    except (TypeError, ValueError, RecursionError, AttributeError, KeyError):
        # Defence in depth: a malformed token shape is the client's mistake, never a 500.
        raise HTTPException(status_code=422, detail="The style draft is malformed") from None


async def _brand_text_refusal(rec, style, chat_key: str, *, cached: bool) -> Optional[str]:
    """The acceptable-use verdict on the name + wordmark (they end up on screen in every video)."""
    import hashlib  # noqa: PLC0415
    import time  # noqa: PLC0415

    text = f"{style.name}\n{style.wordmark}".strip()
    key = (rec.tenant_id, hashlib.sha256(text.encode("utf-8")).hexdigest())
    if cached:
        with _gate_cache_lock:
            hit = _gate_cache.get(key)
            if hit and time.monotonic() - hit[0] < _GATE_CACHE_TTL_S:
                _gate_cache.move_to_end(key)
                return hit[1]
    from .._spawn_gates import check_console_spawn_or_refusal  # noqa: PLC0415

    refusal = await asyncio.to_thread(
        check_console_spawn_or_refusal, text,
        tenant_id=rec.tenant_id, persona="assistant", channel="web",
        chat_key=chat_key, engine_id="video_producer",
    )
    if cached:
        with _gate_cache_lock:
            _gate_cache[key] = (time.monotonic(), refusal)
            _gate_cache.move_to_end(key)
            while len(_gate_cache) > _GATE_CACHE_MAX:
                _gate_cache.popitem(last=False)
    return refusal


async def _previews(style, themes: Optional[List[str]] = None) -> Dict[str, Any]:
    """Three sample slides in the style's look as data: URIs. Never raises for render problems."""
    mod = _style_mod("style_preview")
    if not _preview_slots.acquire(blocking=False):
        return {"previews": [], "notes": ["Previews are busy right now; try again in a moment."]}
    try:
        return await mod.render_previews(style, themes=themes)
    finally:
        _preview_slots.release()


def _public_style(style) -> Dict[str, Any]:
    return style.public()


@router.get("/styles")
def list_styles(rec=_SessionRec):
    sm = _style_mod("style_store")
    store = sm.StyleStore(str(_tenant_base(rec)))
    default_id = store.get_default()
    default_error = None
    if default_id:
        try:
            store.load(default_id)
        except Exception:  # noqa: BLE001 - any failure to read it means jobs fall back to the built-in look
            default_error = "The default style is damaged; videos use the built-in look until you delete it or pick another."
    return {
        "styles": [_public_style(st) for st in store.list()],
        "default_style_id": default_id,
        "default_style_error": default_error,
        "builtin": {"id": _BUILTIN_STYLE_ID, "name": _BUILTIN_STYLE_NAME},
        "limits": {"max_styles": sm.MAX_STYLES, "max_upload_bytes": _MAX_STYLE_UPLOAD_BYTES},
    }


@router.post("/styles/import")
async def import_style(file: UploadFile = File(...), rec=_SessionRec):
    """Read a PowerPoint deck's look into a DRAFT. Stateless: the upload is held in memory for this
    request only (Starlette may spool a large multipart body to its own temp file, which is removed
    when the request closes) and nothing is stored until the draft is committed."""
    import hashlib  # noqa: PLC0415

    imp = _style_mod("style_import_pptx")
    sp = _style_mod("style_pack")
    buf = bytearray()
    too_big = False
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_STYLE_UPLOAD_BYTES:
            too_big = True  # stop reading: the bytes held so far are all that is hashed and counted
            break
    data = bytes(buf)
    size = len(data)
    digest = hashlib.sha256(data).hexdigest()[:12]
    name = Path(file.filename or "deck.pptx").name[:120]

    def refuse(status: int, message: str, warnings: int = 0):
        if not _audit(rec.tenant_id, "video_producer.style_imported",
                      {"bytes": size, "warning_count": warnings, "sha256_prefix": digest, "outcome": "refused"}):
            raise HTTPException(status_code=503, detail="The import could not be recorded")
        raise HTTPException(status_code=status, detail=message)

    if too_big:
        refuse(413, "The file is larger than 25 MB")
    if not data:
        refuse(400, "The file is empty")
    if not data.startswith(b"PK\x03\x04"):
        refuse(415, "This is not a PowerPoint (.pptx or .potx) file")
    global _import_inflight
    with _import_lock:
        busy = _import_inflight >= _IMPORT_MAX_INFLIGHT
        if not busy:
            _import_inflight += 1
    if busy:
        raise HTTPException(status_code=429, detail="Too many imports are running; try again in a moment",
                            headers={"Retry-After": "5"})

    def _import_bounded():
        # The slot is held and released inside the worker thread, so a cancelled request cannot leak it.
        global _import_inflight
        try:
            with _import_slots:
                return imp.import_pptx(data, name)
        finally:
            with _import_lock:
                _import_inflight -= 1

    try:
        result = await asyncio.to_thread(_import_bounded)
    except sp.StyleError as e:  # PptxImportError is one
        refuse(422, str(e))
    except Exception as e:  # noqa: BLE001 - a hostile deck must not become a 500 with internals
        logger.warning("video producer: style import failed (%s)", type(e).__name__)
        refuse(422, "The deck could not be read")
    del data, buf
    style = result.style
    if not _audit(rec.tenant_id, "video_producer.style_imported",
                  {"bytes": size, "warning_count": len(style.warnings), "sha256_prefix": digest, "outcome": "accepted"}):
        raise HTTPException(status_code=503, detail="The import could not be recorded")
    pv = await _previews(style)
    return {"draft": sp.draft_to_wire(style), "previews": pv["previews"], "notes": [*result.notes, *pv["notes"]]}


@router.post("/styles/preview")
async def preview_draft(request: Request, rec=_SessionRec):
    body = await _read_json_body(request)
    style = _draft_style(body.get("draft"))
    refusal = await _brand_text_refusal(rec, style, "video-style:preview", cached=True)
    if refusal is not None:
        raise HTTPException(status_code=403, detail=refusal)
    return await _previews(style)


@router.post("/styles", status_code=201)
async def save_style(request: Request, rec=_SessionRec):
    body = await _read_json_body(request)
    set_default = body.get("set_default", False)
    if not isinstance(set_default, bool):
        raise HTTPException(status_code=422, detail="set_default must be true or false")
    sm = _style_mod("style_store")
    store = sm.StyleStore(str(_tenant_base(rec)))
    style_id = store.new_id()
    style = _draft_style(body.get("draft"), style_id)
    if len(store.ids()) >= sm.MAX_STYLES:
        raise HTTPException(status_code=409, detail=f"At most {sm.MAX_STYLES} styles per tenant; delete one first")
    refusal = await _brand_text_refusal(rec, style, f"video-style:{style_id}", cached=False)
    if refusal is not None:
        raise HTTPException(status_code=403, detail=refusal)
    if not _audit(rec.tenant_id, "video_producer.style_saved",
                  {"style_id": style_id, "source_kind": style.source.get("kind", "tokens"),
                   "has_mark": style.mark_png is not None}):
        raise HTTPException(status_code=503, detail="The style could not be recorded; it was not saved")
    try:
        store.save(style)
        if set_default:
            store.set_default(style_id)
    except sm.StyleQuotaExceeded as e:
        raise HTTPException(status_code=409, detail=str(e)) from None
    except _style_mod("style_pack").StyleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from None
    return JSONResponse(status_code=201, content={"style": _public_style(style)})


class DefaultStyleRequest(BaseModel):
    style_id: Optional[str] = Field(None, max_length=32)


@router.put("/styles/default")
def set_default_style(req: DefaultStyleRequest, rec=_SessionRec):
    store = _style_store(rec)
    if req.style_id is None:
        store.set_default(None)
        return {"default_style_id": None}
    _load_own_style(rec, req.style_id)
    store.set_default(req.style_id)
    return {"default_style_id": req.style_id}


@router.delete("/styles/{style_id}", status_code=204)
def delete_style(style_id: str, rec=_SessionRec):
    sm = _style_mod("style_store")
    store = sm.StyleStore(str(_tenant_base(rec)))
    sp = _style_mod("style_pack")
    if not sp.ID_RE.fullmatch(style_id) or style_id not in store.ids():
        raise HTTPException(status_code=404, detail=_STYLE_NOT_FOUND)
    if not _audit(rec.tenant_id, "video_producer.style_deleted", {"style_id": style_id}):
        raise HTTPException(status_code=503, detail="The deletion could not be recorded; the style was kept")
    try:
        store.delete(style_id)  # also clears the tenant default when it pointed here
    except sm.StyleNotFound:
        raise HTTPException(status_code=404, detail=_STYLE_NOT_FOUND) from None
    return Response(status_code=204)


@router.get("/styles/{style_id}/preview")
async def preview_saved_style(style_id: str, rec=_SessionRec):
    style = await asyncio.to_thread(_load_own_style, rec, style_id)
    return await _previews(style)
