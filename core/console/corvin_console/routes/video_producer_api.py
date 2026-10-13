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
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Literal, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
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
# R1a go (2026-10-10, ADR-2247): process is the default - a job can be killed (cancel, budget), a thread
# cannot. An installed plugin that predates ProcessJobRunner falls back to threads.
_JOB_RUNNER = "process"

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


# F3 (ADR-2247, T-0093): NOTHING is loaded at import. The plugin is third-party-style code; importing the
# console must not execute it, and no tenant may cause it to be executed without having enabled it. The
# only way in is ``_ensure_plugin(tenant_id)``, which loads the source once - and only for a tenant whose
# registry has the plugin installed AND enabled. Until then these stay None and every route answers 404
# (the router gate) or 503 (the source is missing).
VideoJob = _get_storage = get_runner = PLUGIN_SOURCE = None
_load_lock = threading.Lock()

PLUGIN_ID = "video_producer"


def _enabled_for(tenant_id: str) -> bool:
    """Installed AND enabled in THIS tenant's registry. Fail-closed: an unreadable registry is "no"."""
    from .capabilities import _plugin_is_enabled  # noqa: PLC0415

    return bool(tenant_id) and _plugin_is_enabled(tenant_id, PLUGIN_ID)


def _ensure_plugin(tenant_id: str) -> bool:
    """Load the plugin source on demand, for a tenant that enabled it; True iff ``tenant_id`` did.

    The gate is here, in the one primitive every path goes through (router gate, ``_store`` - also reached
    from ``video_learning_api``, which has no router gate -, the style module loader and the boot audit),
    not at each caller. Loading is retried while nothing is loaded (a fresh install has no source until
    the marketplace sync) and is a no-op once it succeeded. A tenant that did not enable the plugin never
    triggers a load - even when another tenant already did, it gets False and no access.
    """
    global VideoJob, _get_storage, get_runner, PLUGIN_SOURCE
    if not _enabled_for(tenant_id):
        return False
    if _get_storage is None:
        with _load_lock:
            if _get_storage is None:
                VideoJob, _get_storage, get_runner, PLUGIN_SOURCE = _load_plugin()
    return True


async def _require_plugin_enabled(rec=Depends(require_session_csrf_on_mutation)):
    """The API lives and dies with the plugin: installed AND enabled for THIS tenant.

    The router is mounted unconditionally, so without this gate an uninstalled (or
    merely installed, consent not yet given) Video Producer still accepted jobs — and
    every job sends its narration text to Google TTS. Fail-closed: an unreadable
    registry means "not enabled". 404, like every other absent surface.
    """
    if not await asyncio.to_thread(_ensure_plugin, rec.tenant_id):
        raise HTTPException(status_code=404, detail="The Video Producer plugin is not installed and enabled for this tenant")
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
    # also reached from video_learning_api, which has no router gate: the enabled check is in _ensure_plugin
    if not _ensure_plugin(rec.tenant_id):
        raise HTTPException(status_code=404, detail="The Video Producer plugin is not installed and enabled for this tenant")
    if not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    store = _get_storage(str(_tenant_base(rec)))
    if getattr(store, "interrupted_ids", None):  # a route opened the store before the boot audit did
        _drain_interrupted(rec.tenant_id, store)
    return store


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
    # ADR-2249 D4 (T-0113): the tenant's website-capture switch; persisted, default off
    capture_enabled: Optional[bool] = None


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
        # Website capture (ADR-2249 D4): usable only when the switch is on AND an egress policy exists
        "capture_enabled": _capture_enabled(rec),
        "capture_policy_configured": _capture_egress_gate(rec.tenant_id) is not None,
        # ... and only on a host that can capture at all (Chrome, posix_spawn, own addresses known)
        "capture_host_problem": _capture_host_problem(),
    }


def _capture_host_problem() -> Optional[str]:
    """None when this host can capture, else the plugin's closed reason (no_browser | unsupported)."""
    try:
        return importlib.import_module(f"{_PLUGIN_PKG}.capture").host_support()
    except Exception:  # noqa: BLE001 - no capture module on this build
        return "unsupported"


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

def _vjs():
    """The host's job-store module (``corvin_operator/bridges/shared/video_job_store.py``): the two
    sidecars (owners, revisions) under one locked read-modify-write, a job's files, its deletion and
    the store's size. The GDPR erasure layer ``L-video-producer`` uses the same module, so a job is
    deleted the same way by the operator and by an Art. 17 request (ADR-2247 R3)."""
    shared = str(_repo_root / "corvin_operator" / "bridges" / "shared")
    if shared not in sys.path:
        sys.path.insert(0, shared)
    import video_job_store  # type: ignore[import-not-found]  # noqa: PLC0415

    return video_job_store


def _revisions(rec) -> Dict[str, str]:
    """job id -> the job it revises (tenant-scoped sidecar; the plugin's job record is untouched)."""
    return {k: v for k, v in _vjs().read_map(_tenant_base(rec), _vjs().REVISIONS_FILE).items()
            if _JOB_ID_RE.match(v)}


def _record_revision(rec, job_id: str, base_job_id: str) -> None:
    _vjs().record_revision(_tenant_base(rec), job_id, base_job_id)


def _job_owner(rec) -> str:
    """The principal that creates a job, as written to ``owners.json`` and matched by the erasure
    layer. The console has no per-user login today: the only live login is local-login, the tenant's
    owner (tier ``owner``), so a console job belongs to ``console-owner:<tenant>``. The first
    credential login that gets built must name its user here. Never a name, never an e-mail."""
    return f"console-owner:{rec.tenant_id}"


# ── Storage quota (ADR-2247 R3) ──────────────────────────────────────────────
# Per tenant, from tenant.corvin.yaml ``spec.video_producer.storage_quota_gb`` (default 5). The whole
# store counts (records, videos, styles). A new job is refused while the store is at or above it.
_DEFAULT_QUOTA_GB = 5.0
_MAX_QUOTA_GB = 100_000.0


def _storage_quota_gb(tenant_id: str) -> float:
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    path = tenant_home(tenant_id) / "global" / "tenant.corvin.yaml"
    try:
        if not path.is_file():
            return _DEFAULT_QUOTA_GB
        import yaml  # noqa: PLC0415

        spec = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("spec") or {}
        raw = (spec.get("video_producer") or {}).get("storage_quota_gb") if isinstance(spec, dict) else None
    except Exception as e:  # noqa: BLE001 - an unreadable file must not lift the quota
        logger.warning("video producer: tenant config unreadable (%s); default storage quota", type(e).__name__)
        return _DEFAULT_QUOTA_GB
    if raw is None:
        return _DEFAULT_QUOTA_GB
    if isinstance(raw, bool) or not isinstance(raw, (int, float)) or not (0 < raw <= _MAX_QUOTA_GB):
        logger.warning("video producer: spec.video_producer.storage_quota_gb is not a positive number; default used")
        return _DEFAULT_QUOTA_GB
    return float(raw)


def _storage(rec) -> Dict[str, Any]:
    quota_gb = _storage_quota_gb(rec.tenant_id)
    quota = int(quota_gb * 1024 ** 3)
    used = _vjs().store_usage_bytes(_tenant_base(rec))
    return {"used_bytes": used, "quota_bytes": quota, "quota_gb": quota_gb, "full": used >= quota}


def _fmt_size(n: int) -> str:
    if n < 1024 ** 2:
        return f"{n / 1024:.0f} KB"
    if n < 1024 ** 3:
        return f"{n / 1024 ** 2:.2f} MB"
    return f"{n / 1024 ** 3:.2f} GB"


_SKILL_ID = "os.video_producer"

# ADR-0534: a rating is accepted only for a skill that really ran in the last 24 h (reality check
# against the tenant's own `skill_executed` learning events). A production is an execution, so the
# host records one when the job reaches an end state - the job's own record is the ground truth,
# never the start (a job that never ran must not make feedback valid). The plugin cannot do it: it
# has no access to the learning store, and in process mode it is a different process.
_EXEC_POLL_S = 2.0
_EXEC_WATCH_MAX_S = 3 * 3600
_FEEDBACK_WINDOW_S = 24 * 3600


def _write_execution_event(tenant_id: str, job, meta: Optional[dict]) -> Optional[str]:
    from core.learning.event_store import EventStore  # noqa: PLC0415
    from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415
    from core.paths.tenant import tenant_home  # noqa: PLC0415

    meta = meta if isinstance(meta, dict) else {}
    started, done = getattr(job, "started_at", None), getattr(job, "completed_at", None)
    seconds = round((done - started).total_seconds(), 1) if started and done else None
    event = LearningEvent.create(
        event_type=EventType.SKILL_EXECUTED,
        skill_id=_SKILL_ID,
        tenant_id=tenant_id,
        # ids, counts and enums only - never the task text, narration or an error message
        signal={"task_id": job.id, "status": job.status, "seconds": seconds,
                "output": {"job_id": job.id, "status": job.status, "scenes": meta.get("scenes"),
                           "language": meta.get("language"), "tts_provider": meta.get("tts_provider_used")}},
        lom="corvin_console.routes.video_producer_api:_record_job_execution",
    )
    return EventStore(tenant_home(tenant_id), tenant_id=tenant_id).write_event(event)


def _record_job_execution(tenant_id: str, job_id: str, storage_base: str) -> None:
    """Wait for the job to end, then record that the Video Producer ran (audit-first). Runs on its
    own daemon thread: it must outlive the request's event loop (a test client closes that loop with
    the request) and it is bound to the host process exactly like the job it watches."""
    try:
        storage = _get_storage(storage_base)
        deadline = time.monotonic() + _EXEC_WATCH_MAX_S
        job = None
        while time.monotonic() < deadline:
            job = storage.get_job(job_id)
            if job is None:
                return  # deleted (DELETE /jobs/{id} or an erasure): nothing left to record
            if job.status in _TERMINAL:
                break
            time.sleep(_EXEC_POLL_S)
        else:
            logger.warning("[%s] job did not end within the watch window; no execution recorded", job_id)
            return
        try:
            _audit_job_end(tenant_id, job, storage, "watcher")
        except Exception as e:  # noqa: BLE001 - best-effort: the learning record below still runs
            logger.error("[%s] lifecycle record failed (%s)", job_id, type(e).__name__)
        out = storage.get_video_output(job_id)
        ref = _write_execution_event(tenant_id, job, getattr(out, "metadata", None))
        if not ref:
            logger.error("[%s] execution event not recorded: feedback for this job will be refused", job_id)
    except Exception as e:  # noqa: BLE001 - never takes the host down; the cost is a refused rating
        logger.error("[%s] execution event not recorded (%s)", job_id, type(e).__name__)


def _watch_job_execution(tenant_id: str, job_id: str, storage_base: str) -> None:
    threading.Thread(target=_record_job_execution, args=(tenant_id, job_id, storage_base),
                     name=f"vp-exec-{job_id}", daemon=True).start()


# ── Job lifecycle audit (ADR-2247 R2b) ───────────────────────────────────────
# The HOST writes video_producer.job_started|completed|failed|cancelled|degraded to the tenant's
# chain from what it observes: the runner accepted the job, the job's record became terminal (the
# record is written by the job process or by the runner's reaper - the host only reads it), the
# job's own report.json, and the closed reason vocabulary of the store. The plugin never gets audit
# access. Ids, counts, durations and closed enums only - never the task, a narration or a message.
# Best-effort, unlike the grounding events: a chain write that does not commit is logged and counted
# (``lifecycle_audit_failures`` in /video/overview) and never stops or fails a video.
_LIFECYCLE_REASONS = frozenset({"interrupted", "cancelled", "child_exit", "budget", "job_error", "start_failed"})
_LIFECYCLE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:+\-]{1,64}$")
_LIFECYCLE_MAX_REPORT_BYTES = 128 * 1024
_LIFECYCLE_SEEN_MAX = 4096
_lifecycle_lock = threading.Lock()
# (tenant, job) pairs whose terminal record THIS process wrote: one terminal event per job
_lifecycle_terminal: "collections.OrderedDict[tuple, str]" = collections.OrderedDict()
_lifecycle_failures = 0


def _lifecycle_audit(tenant_id: str, event: str, details: Dict[str, Any]) -> bool:
    global _lifecycle_failures
    ok = _audit(tenant_id, event, {k: v for k, v in details.items() if v is not None})
    if not ok:
        with _lifecycle_lock:
            _lifecycle_failures += 1
        logger.error("[%s] lifecycle record %s not written to the chain (counted; the job is unaffected)",
                     details.get("job_id"), event)
    return ok


def lifecycle_audit_failures() -> int:
    with _lifecycle_lock:
        return _lifecycle_failures


def _claim_terminal(tenant_id: str, job_id: str, event: str) -> Optional[str]:
    """Reserve the one terminal record of a job. Returns None when the claim is new, else the event
    an earlier observer already wrote."""
    key = (tenant_id, job_id)
    with _lifecycle_lock:
        prev = _lifecycle_terminal.get(key)
        if prev is not None:
            return prev
        _lifecycle_terminal[key] = event
        while len(_lifecycle_terminal) > _LIFECYCLE_SEEN_MAX:
            _lifecycle_terminal.popitem(last=False)
    return None


def _lc_token(value: Any) -> Optional[str]:
    return value if isinstance(value, str) and _LIFECYCLE_TOKEN_RE.match(value) else None


def _lc_int(value: Any) -> Optional[int]:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _report_facts(storage, job_id: str) -> Dict[str, Any]:
    """Numbers and closed identifiers from the job's own report.json (schema 1). A missing,
    oversized or malformed report gives nothing - never an exception, never text."""
    import json  # noqa: PLC0415

    path = Path(storage.videos_dir) / job_id / "report.json"
    try:
        if path.is_symlink() or path.stat().st_size > _LIFECYCLE_MAX_REPORT_BYTES:
            return {}
        rep = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(rep, dict) or rep.get("job_id") != job_id:
        return {}
    scenes = [s for s in rep.get("scenes") or [] if isinstance(s, dict)]
    degr = [d for d in rep.get("degradations") or [] if isinstance(d, dict)]
    verdicts = {s.get("verdict") for s in rep.get("signals") or [] if isinstance(s, dict)}
    verdict = next((v for v in ("fail", "warn", "pass") if v in verdicts), None)
    tiers = sorted({t for t in (_lc_token(s.get("tts_tier")) for s in scenes) if t})
    kinds = sorted({k for k in (_lc_token(d.get("kind")) for d in degr) if k})
    ds = rep.get("degraded_scenes") if isinstance(rep.get("degraded_scenes"), dict) else {}
    total = rep.get("total_seconds")
    return {
        "plugin_version": _lc_token(rep.get("plugin_version")),
        "scene_count": len(scenes),
        "web_scenes": sum(1 for s in scenes if s.get("renderer") == "web"),
        "pillow_scenes": sum(1 for s in scenes if s.get("renderer") == "pillow"),
        "tts_tiers": ",".join(tiers) or None,
        "degradation_count": len(degr),
        "degraded_scenes": _lc_int(ds.get("n")),
        "degradation_kinds": ",".join(kinds) or None,
        "verdict": verdict,
        "failed_stage": _lc_token(rep.get("failed_stage")),
        "error_class": _lc_token(rep.get("error_class")),
        "report_seconds": round(float(total), 1) if isinstance(total, (int, float)) and not isinstance(total, bool) else None,
    }


def _audit_job_end(tenant_id: str, job, storage, observed_by: str) -> Optional[str]:
    """Write the one terminal lifecycle record of ``job`` (its record is terminal). Returns the event
    written, or None when another observer had already written it."""
    status = getattr(job, "status", None)
    if status not in _TERMINAL:
        return None
    event = {"complete": "video_producer.job_completed", "cancelled": "video_producer.job_cancelled"}.get(
        status, "video_producer.job_failed")
    prev = _claim_terminal(tenant_id, job.id, event)
    if prev is not None and (prev == event or status == "cancelled"):
        return None  # the cancel route already recorded this end
    reason = getattr(job, "reason", None)
    if status == "error":
        reason = reason if reason in _LIFECYCLE_REASONS else "job_error"
    elif status == "cancelled":
        reason = "cancelled"
    else:
        reason = None
    facts = _report_facts(storage, job.id)
    started, done = getattr(job, "started_at", None), getattr(job, "completed_at", None)
    duration = round((done - started).total_seconds(), 1) if started and done else facts.get("report_seconds")
    details = {"job_id": job.id, "runner": _JOB_RUNNER, "status": status, "reason": reason,
               "duration_s": duration, "observed_by": observed_by,
               **{k: v for k, v in facts.items() if k not in ("report_seconds", "degradation_kinds")}}
    _lifecycle_audit(tenant_id, event, details)
    if status == "complete" and facts.get("degradation_count"):
        _lifecycle_audit(tenant_id, "video_producer.job_degraded", {
            "job_id": job.id, "degradation_count": facts["degradation_count"],
            "degraded_scenes": facts.get("degraded_scenes"), "scene_count": facts.get("scene_count"),
            "degradation_kinds": facts.get("degradation_kinds"), "verdict": facts.get("verdict")})
    return event


def _drain_interrupted(tenant_id: str, storage) -> int:
    """Audit every job the store's first open in this process closed as ``interrupted`` (its console
    died mid-job). ``take_interrupted_ids`` hands each id out once, so a job gets exactly one record."""
    take = getattr(storage, "take_interrupted_ids", None)
    if take is None:  # an installed plugin older than R2b
        return 0
    n = 0
    for job_id in take():
        if not _JOB_ID_RE.match(str(job_id)) or _claim_terminal(tenant_id, job_id, "video_producer.job_failed"):
            continue
        job = storage.get_job(job_id)
        started, done = getattr(job, "started_at", None), getattr(job, "completed_at", None)
        _lifecycle_audit(tenant_id, "video_producer.job_failed", {
            "job_id": job_id, "runner": _JOB_RUNNER, "status": "error", "reason": "interrupted",
            "observed_by": "boot",
            "duration_s": round((done - started).total_seconds(), 1) if started and done else None})
        n += 1
    return n


def audit_interrupted_jobs() -> int:
    """At host start: open every tenant's existing video store once, which closes the jobs a killed
    console left running, and audit each one (``job_failed reason=interrupted``). Both hosts call this
    at boot (``start_boot_audit``) - not lazily on the first route, which would leave the record
    unwritten until somebody opened the panel. Returns the number of records written."""
    from core.paths.tenant import corvin_home, tenant_home  # noqa: PLC0415

    root = corvin_home() / "tenants"
    if not root.is_dir():
        return 0
    n = 0
    for d in sorted(root.iterdir()):
        base = d / "video_producer"
        if not (base / "jobs").is_dir():
            continue  # never create a store for a tenant that has none
        try:
            tenant_id = d.name
            if tenant_home(tenant_id).resolve() != d.resolve():  # validates the id
                continue
            # F3: only a tenant that has the plugin enabled loads it; a disabled tenant's interrupted jobs
            # are closed and audited by the first store open after it enables the plugin again (_store)
            if not _ensure_plugin(tenant_id) or not _get_storage:
                continue
            n += _drain_interrupted(tenant_id, _get_storage(str(base)))
        except Exception as e:  # noqa: BLE001 - one tenant's damaged store must not stop the others
            logger.warning("video producer: boot lifecycle audit skipped a tenant (%s)", type(e).__name__)
    return n


def start_boot_audit() -> None:
    """Run ``audit_interrupted_jobs`` on a daemon thread: a held job lock costs the store open up to
    the plugin's INTERRUPT_GRACE_S, which must not delay the host's start."""
    def _run() -> None:
        try:
            n = audit_interrupted_jobs()
            if n:
                logger.warning("video producer: %d interrupted job(s) closed and audited at start", n)
        except Exception as e:  # noqa: BLE001
            logger.warning("video producer: boot lifecycle audit failed (%s)", type(e).__name__)

    threading.Thread(target=_run, name="vp-boot-audit", daemon=True).start()


def _feedback_refusal_detail(job) -> str:
    """Why a rating was not recorded, as far as the host can tell."""
    done = getattr(job, "completed_at", None)
    if done is not None and (datetime.now() - done).total_seconds() > _FEEDBACK_WINDOW_S:
        return "This video was produced more than 24 hours ago; ratings are only accepted for recent productions"
    if getattr(job, "status", None) not in _TERMINAL:
        return "Rate a video once it has been produced"
    return "Feedback could not be recorded"


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
    store_use = await asyncio.to_thread(_storage, rec)
    if store_use["full"]:
        raise HTTPException(status_code=409, detail=(
            f"Video storage is full ({_fmt_size(store_use['used_bytes'])} of {_fmt_size(store_use['quota_bytes'])} used). "
            "Delete videos you no longer need to produce a new one."))
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
    try:
        # who created it: per-user erasure attributes the job by this entry, never by its free text
        await asyncio.to_thread(_vjs().record_owner, _tenant_base(rec), job_id, _job_owner(rec))
        if req.base_job_id:
            await asyncio.to_thread(_record_revision, rec, job_id, req.base_job_id)
    except Exception as e:  # noqa: BLE001 - a job without an owner could not be erased per user
        logger.error("[%s] owner not recorded (%s); job withdrawn", job_id, type(e).__name__)
        await asyncio.to_thread(_vjs().delete_job, _tenant_base(rec), job_id)
        raise HTTPException(status_code=503, detail="The job could not be recorded; it was not started") from None

    config = {
        "storage_base": str(_tenant_base(rec)),
        "tts_engine": settings["tts_engine"],
        "max_duration_minutes": settings["max_duration_minutes"],
        "storyboard_backend": backend,
        "storyboard_model": model,
    }
    if web_style is not None:
        config["web_style"] = web_style
    if base_job is not None:
        # revision_locality (ADR-2247, information): the job compares its storyboard with the base video's
        config["revision_base_narrations"] = [str(getattr(sc, "narration_text", "") or "")
                                              for sc in (getattr(getattr(base_job, "storyboard", None), "scenes", None) or [])]
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
        if _claim_terminal(rec.tenant_id, job_id, "video_producer.job_failed") is None:
            await asyncio.to_thread(_lifecycle_audit, rec.tenant_id, "video_producer.job_failed", {
                "job_id": job_id, "runner": _JOB_RUNNER, "status": "error", "reason": "start_failed",
                "observed_by": "route"})
        raise HTTPException(status_code=500, detail="Failed to start the job") from None

    # the runner accepted the job (process mode: spawned or queued behind the cap) - lifecycle start
    await asyncio.to_thread(_lifecycle_audit, rec.tenant_id, "video_producer.job_started", {
        "job_id": job_id, "runner": _JOB_RUNNER, "tts_engine": settings["tts_engine"],
        "storyboard_backend": backend, "is_revision": bool(req.base_job_id),
        "grounded": grounding_pack is not None, "styled": web_style is not None,
        "max_duration_minutes": settings["max_duration_minutes"]})
    _watch_job_execution(rec.tenant_id, job_id, config["storage_base"])
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


_CANCEL_WAIT_S = 8.0   # the runner's SIGTERM grace (5 s) plus the reap


@router.post("/jobs/{job_id}/cancel")
async def cancel_video_job(job_id: str, rec=_SessionRec):
    """Stop a job that is being produced (ADR-2247, PLAN-0946 R1b). Session + CSRF (router dependency),
    tenant-scoped through the tenant's own store: another tenant's job id is a 404, like any absent job.

    Process mode kills the job's whole process group - the job process, ffmpeg and any Chromium that
    outlived its driver - SIGTERM, then SIGKILL after the grace period, and the runner records
    ``cancelled``. Thread mode flags the job's budget and stops its in-flight external call. The answer
    is the job's state after the kill, waited for up to a few seconds; ``cancelling`` means the
    runner has been told and the record is not terminal yet."""
    _check_job_id(job_id)
    storage = _store(rec)
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status in _TERMINAL:
        raise HTTPException(status_code=409, detail=f"The job has already ended ({job.status})")
    runner = get_runner() if get_runner else None
    cancel = getattr(runner, "cancel_job", None)
    if cancel is None:
        raise HTTPException(status_code=501, detail="This build cannot cancel a running video job")
    # the runner is process-wide, the store is the tenant's: ownership was proven by the lookup above
    if not await asyncio.to_thread(cancel, job_id):
        raise HTTPException(status_code=409, detail="The job is not running in this console process")
    end = time.monotonic() + _CANCEL_WAIT_S
    state = job.status
    while time.monotonic() < end:
        cur = await asyncio.to_thread(storage.get_job, job_id)
        state = cur.status if cur else state
        if state in _TERMINAL:
            break
        await asyncio.sleep(0.1)
    outcome = state if state in _TERMINAL else "cancelling"
    # One terminal record per job (ADR-2247 R2b): the route writes job_cancelled when the kill is
    # done or under way and claims the job's end, so the watcher does not write it twice. A job that
    # finished before the kill landed ("complete"/"error") is left to the watcher, which records what
    # really happened. Best-effort: a failed chain write never un-cancels a job.
    if outcome in ("cancelled", "cancelling") and \
            _claim_terminal(rec.tenant_id, job_id, "video_producer.job_cancelled") is None:
        await asyncio.to_thread(_lifecycle_audit, rec.tenant_id, "video_producer.job_cancelled", {
            "job_id": job_id, "runner": _JOB_RUNNER, "outcome": outcome, "status": "cancelled",
            "reason": "cancelled", "observed_by": "route"})
    return {"job_id": job_id, "status": outcome}


@router.delete("/jobs/{job_id}")
def delete_video_job(job_id: str, rec=_SessionRec):
    """Delete a finished job whole (ADR-2247 R3): its record, ``videos/<id>/`` (scenes, audio,
    posters, caches, the MP4 and the report), run directory, thumbnails and its entries in the
    ``owners.json`` / ``revisions.json`` sidecars. Session + CSRF (router dependency); the tenant
    comes from the session, so another tenant's job id is a 404 like any absent job. A job that is
    still being produced is a 409 - cancel it first. Audit-first: ``video_producer.job_deleted`` is
    written to the tenant chain before anything is removed; a record that does not commit keeps the
    job (503). No bulk delete."""
    _check_job_id(job_id)
    storage = _store(rec)
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    base = _tenant_base(rec)
    vjs = _vjs()
    if job.status not in _TERMINAL or vjs.job_is_live(base, job_id):
        raise HTTPException(status_code=409, detail="The video is still being produced; cancel it first")
    files, size = vjs.job_footprint(base, job_id)
    if not _audit(rec.tenant_id, "video_producer.job_deleted",
                  {"job_id": job_id, "status": job.status, "files_removed": files, "bytes_freed": size}):
        raise HTTPException(status_code=503, detail="The deletion could not be recorded; the video was kept")
    try:
        files, size = vjs.delete_job(base, job_id)
    except Exception as e:  # noqa: BLE001 - the record goes last, so the job is still listed and can be retried
        logger.error("[%s] deletion incomplete (%s)", job_id, type(e).__name__)
        raise HTTPException(status_code=500, detail="The video could not be deleted completely; try again") from None
    return {"job_id": job_id, "deleted": True, "files_removed": files, "bytes_freed": size}


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
    if req.capture_enabled is not None and req.capture_enabled != _capture_enabled(rec):
        # audit-first: the record exists before the switch changes
        if not _audit(rec.tenant_id, "video_producer.capture_setting_changed", {"enabled": req.capture_enabled}):
            raise HTTPException(status_code=503, detail="The change could not be recorded; the setting was kept")
        _write_capture_switch(rec, req.capture_enabled)
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

    # The job's production report lives in THIS tenant's store (the session's tenant, never env);
    # a directory that resolves outside it is not read.
    job_dir = Path(storage.videos_dir) / job_id
    if not _inside(job_dir, _tenant_base(rec)):
        job_dir = None
    return measure(_job_dict(job), _output_dict(storage.get_video_output(job_id)), job_dir=job_dir, with_report=True)


# ── Post-hoc measurement (ADR-2247 cue_alignment, PLAN-0946 R5, T-0094) ─────────────────────

# whisper-1 receives the narration AUDIO of the job: the gate runs under the OpenAI speech profile -
# the same engine id the job's own OpenAI TTS is gated under (``_TTS_GATE_ENGINE["openai"]``).
_MEASURE_GATE_ENGINE = _TTS_GATE_ENGINE["openai"]
_MEASURE_SIGNALS = ("cue_alignment",)
_MEASURING: set = set()
_MEASURING_LOCK = threading.Lock()
_REFUSED_REASON = "refused by the pre-spawn gates (L44/L34/L35)"


def _plugin_signals():
    try:
        sg = importlib.import_module(f"{_PLUGIN_PKG}.signals")
    except Exception:  # noqa: BLE001 - an older plugin without post-hoc signals
        return None
    return sg if hasattr(sg, "measure_posthoc") else None


def _openai_key() -> Optional[str]:
    return os.environ.get("CORVIN_TTS_OPENAI_KEY") or os.environ.get("OPENAI_API_KEY") or None


def _ms(value: Any) -> Optional[int]:
    return int(round(float(value) * 1000)) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


@router.post("/jobs/{job_id}/measure")
async def measure_video_job(job_id: str, rec=_SessionRec):
    """Run the post-hoc ``cue_alignment`` signal on a finished job, on the operator's explicit request.

    The narration audio of every scene goes to OpenAI whisper-1, so the request passes the SAME pre-spawn
    gates as the job's own OpenAI TTS (L44 acceptable use, capabilities, L34 classification of the
    narration, L35 egress to the OpenAI host); a grounded job's narration is classified ``INTERNAL``, the
    class its pack was released under, so its audio goes only where its text already went. A refusal is
    not an error: the signal is recorded as ``skip`` with the reason, in the report and in the answer.
    Audit-first: ``video_producer.measure_started`` commits before any audio leaves (no commit, no call);
    ``measure_refused`` / ``measure_completed`` close it. Ids, counts and numbers only - never narration.
    Session + CSRF (router dependency); the tenant comes from the session, another tenant's job is a 404."""
    _check_job_id(job_id)
    storage = _store(rec)
    job = storage.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    if job.status != "complete":
        raise HTTPException(status_code=409, detail="Only a produced video can be measured")
    job_dir = Path(storage.videos_dir) / job_id
    if not _inside(job_dir, _tenant_base(rec)) or not job_dir.is_dir():
        raise HTTPException(status_code=404, detail="Video not found")
    sg = _plugin_signals()
    if sg is None:
        raise HTTPException(status_code=501, detail="This build of the Video Producer has no post-hoc measurement")
    out = storage.get_video_output(job_id)
    metadata = dict(getattr(out, "metadata", None) or {}) if out else {}
    scenes = sg.storyboard_scene_dicts(getattr(job, "storyboard", None))
    if not scenes:
        raise HTTPException(status_code=409, detail="The video has no storyboard to measure against")
    grounded = (metadata.get("grounding") or {}).get("status") in ("grounded", "grounded_with_unverified")
    base = {"job_id": job_id, "signal": "cue_alignment"}
    key = (rec.tenant_id, job_id)
    with _MEASURING_LOCK:
        if key in _MEASURING:
            raise HTTPException(status_code=409, detail="This video is already being measured")
        _MEASURING.add(key)
    try:
        refusal = None
        gate = None
        if not _openai_key():
            refusal, gate = "No OpenAI key is configured on this host (CORVIN_TTS_OPENAI_KEY or OPENAI_API_KEY).", "no_key"
        else:
            from .._spawn_gates import check_console_spawn_or_refusal  # noqa: PLC0415

            narration = "\n".join(s["narration_text"] for s in scenes if s["narration_text"])
            refusal = await asyncio.to_thread(
                check_console_spawn_or_refusal, narration, tenant_id=rec.tenant_id, persona="assistant",
                channel="web", chat_key=f"video:{job_id}:measure", engine_id=_MEASURE_GATE_ENGINE,
                classification="INTERNAL" if grounded else None,
            )
            gate = "pre_spawn" if refusal is not None else None
        if refusal is not None:
            reason = "no OpenAI key on this host" if gate == "no_key" else _REFUSED_REASON
            result = await asyncio.to_thread(sg.measure_posthoc, job_dir, job.storyboard, metadata,
                                             only=_MEASURE_SIGNALS, refused=reason)
            await asyncio.to_thread(_audit, rec.tenant_id, "video_producer.measure_refused",
                                    {**base, "gate": gate, "stored": bool(result.get("stored"))})
            return {"job_id": job_id, "measured": False, "refusal": refusal, "signals": result["signals"],
                    "stored": bool(result.get("stored"))}
        if not await asyncio.to_thread(_audit, rec.tenant_id, "video_producer.measure_started", {
                **base, "engine": _MEASURE_GATE_ENGINE, "classification": "INTERNAL" if grounded else "classified",
                "scenes": len(scenes)}):
            raise HTTPException(status_code=503, detail="The measurement could not be recorded; nothing was sent")
        api_key = _openai_key()
        result = await asyncio.to_thread(sg.measure_posthoc, job_dir, job.storyboard, metadata,
                                         transcribe=lambda mp3: sg.whisper_words(mp3, api_key), only=_MEASURE_SIGNALS)
        recd = next((r for r in result["signals"] if r.get("id") == "cue_alignment"), {})
        detail = recd.get("detail") or {}
        await asyncio.to_thread(_audit, rec.tenant_id, "video_producer.measure_completed", {
            **base, "verdict": recd.get("verdict"), "matched": (recd.get("basis") or {}).get("n"),
            "items": (recd.get("basis") or {}).get("of"), "median_ms": _ms(recd.get("value")),
            "p90_ms": _ms(detail.get("p90_s")), "stored": bool(result.get("stored"))})
        return {"job_id": job_id, "measured": True, "signals": result["signals"], "stored": bool(result.get("stored"))}
    finally:
        with _MEASURING_LOCK:
            _MEASURING.discard(key)


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
        # lifecycle records that did not reach the chain since this console started (ADR-2247 R2b)
        "lifecycle_audit_failures": lifecycle_audit_failures(),
        # the whole store against the tenant's quota (spec.video_producer.storage_quota_gb, default 5)
        "storage": _storage(rec),
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
    job = _store(rec).get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    from .feedback_emitter_helper import emit_feedback_event

    audit_ref = await emit_feedback_event(
        skill_id=_SKILL_ID,
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
        raise HTTPException(status_code=503, detail=_feedback_refusal_detail(job))
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


_MOD_UNAVAILABLE = {"asset_store": "Uploaded images are not available on this build",
                    "capture": "Website capture is not available on this build"}


def _style_mod(name: str):
    # every caller is a route behind the router gate, which already loaded the plugin for its tenant
    if not _get_storage:
        raise HTTPException(status_code=503, detail=_UNAVAILABLE)
    try:
        return importlib.import_module(f"{_PLUGIN_PKG}.{name}")
    except ImportError:
        detail = _MOD_UNAVAILABLE.get(name, "Custom styles are not available on this build")
        raise HTTPException(status_code=501, detail=detail) from None


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


def _looks_like_svg(data: bytes) -> bool:
    head = data[:4096].lstrip(b"\xef\xbb\xbf \t\r\n")
    return head.startswith(b"<svg") or (head.startswith(b"<?xml") and b"<svg" in head)


@router.post("/styles/import")
async def import_style(file: UploadFile = File(...), rec=_SessionRec):
    """Read a PowerPoint deck's look into a DRAFT. Stateless: the upload is held in memory for this
    request only (Starlette may spool a large multipart body to its own temp file, which is removed
    when the request closes) and nothing is stored until the draft is committed."""
    import hashlib  # noqa: PLC0415

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
    # ADR-2257: a Penpot board exported as SVG is the second source of a style; sniffed, never the extension
    is_svg = _looks_like_svg(data)
    fmt = "penpot_svg" if is_svg else "pptx"
    name = Path(file.filename or ("board.svg" if is_svg else "deck.pptx")).name[:120]

    def refuse(status: int, message: str, warnings: int = 0):
        if not _audit(rec.tenant_id, "video_producer.style_imported",
                      {"bytes": size, "warning_count": warnings, "sha256_prefix": digest, "outcome": "refused",
                       "format": fmt}):
            raise HTTPException(status_code=503, detail="The import could not be recorded")
        raise HTTPException(status_code=status, detail=message)

    if too_big:
        refuse(413, "The file is larger than 25 MB")
    if not data:
        refuse(400, "The file is empty")
    if not is_svg and not data.startswith(b"PK\x03\x04"):
        refuse(415, "This is not a PowerPoint (.pptx or .potx) or a Penpot SVG file")
    imp = _style_mod("penpot_import") if is_svg else _style_mod("style_import_pptx")
    import_fn = imp.import_penpot if is_svg else imp.import_pptx
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
                return import_fn(data, name)
        finally:
            with _import_lock:
                _import_inflight -= 1

    try:
        result = await asyncio.to_thread(_import_bounded)
    except sp.StyleError as e:  # PptxImportError and PenpotImportError are ones
        refuse(422, str(e))
    except Exception as e:  # noqa: BLE001 - a hostile deck must not become a 500 with internals
        logger.warning("video producer: style import failed (%s)", type(e).__name__)
        refuse(422, "The design could not be read" if is_svg else "The deck could not be read")
    del data, buf
    style = result.style
    if not _audit(rec.tenant_id, "video_producer.style_imported",
                  {"bytes": size, "warning_count": len(style.warnings), "sha256_prefix": digest, "outcome": "accepted",
                   "format": fmt}):
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


# ── Image assets for `still` scenes (ADR-2249 D3, T-0111) ────────────────────
# <tenant>/video_producer/assets/<ast_8hex>/: PNG re-encoded by the plugin, caption and hotspots typed
# and clicked by the user (never derived from the image), uploaded_by = the session's principal (the
# key the L-video-producer erasure layer matches). Every mutation and every read of the image bytes is
# recorded on the tenant chain FIRST; a record that cannot be written stops the action.
_ASSET_NOT_FOUND = "Image not found"
_MAX_ASSET_UPLOAD_BYTES = 8 * 1024 * 1024
_MAX_ASSET_FORM_CHARS = 8 * 1024


def _asset_mod():
    return _style_mod("asset_store")


def _asset_store(rec):
    return _asset_mod().AssetStore(str(_tenant_base(rec)))


def _public_asset(doc: Dict[str, Any]) -> Dict[str, Any]:
    keep = ("id", "caption", "hotspots", "width", "height", "bytes", "created", "source")
    return {k: doc.get(k) for k in keep}


def _refusal_code(message: str) -> str:
    m = message.lower()
    for needle, code in (("larger than", "too_large"), ("only png", "not_png"), ("not a readable", "unreadable"),
                         ("pixels per side", "dimensions"), ("animated", "animated"), ("empty", "empty"),
                         ("per workspace", "quota"), ("caption", "caption"), ("hotspot", "hotspots"),
                         ("source", "source")):
        if needle in m:
            return code
    return "invalid"


@router.get("/assets")
def list_assets(rec=_SessionRec):
    am = _asset_mod()
    return {"assets": [_public_asset(a) for a in _asset_store(rec).list()],
            "limits": {"max_assets": am.MAX_ASSETS, "max_image_bytes": am.MAX_IMAGE_BYTES,
                       "max_upload_bytes": _MAX_ASSET_UPLOAD_BYTES, "max_caption": am.MAX_CAPTION,
                       "max_hotspots": am.MAX_SPOTS}}


@router.post("/assets", status_code=201)
async def upload_asset(file: UploadFile = File(...), caption: str = Form(""), hotspots: str = Form("[]"),
                       source: str = Form(""), rec=_SessionRec):
    """One PNG plus what the user typed (caption, provenance) and clicked (hotspots). Nothing of the
    upload is kept but its pixels; the record is written to the chain before the asset is visible."""
    import hashlib  # noqa: PLC0415
    import json  # noqa: PLC0415

    am = _asset_mod()
    buf = bytearray()
    too_big = False
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_ASSET_UPLOAD_BYTES:
            too_big = True
            break
    data = bytes(buf)
    base = {"upload_bytes": len(data), "sha256_prefix": hashlib.sha256(data).hexdigest()[:12]}

    def refuse(status: int, message: str, code: str):
        if not _audit(rec.tenant_id, "video_producer.asset_uploaded", {**base, "outcome": "refused", "refusal": code}):
            raise HTTPException(status_code=503, detail="The upload could not be recorded")
        raise HTTPException(status_code=status, detail=message)

    if too_big:
        refuse(413, f"The file is larger than {_MAX_ASSET_UPLOAD_BYTES // (1024 * 1024)} MB", "too_large")
    if len(caption) > _MAX_ASSET_FORM_CHARS or len(hotspots) > _MAX_ASSET_FORM_CHARS or len(source) > _MAX_ASSET_FORM_CHARS:
        refuse(413, "The form fields are too long", "invalid")
    try:
        spots = json.loads(hotspots or "[]")
    except ValueError:
        refuse(422, "hotspots must be a JSON list", "hotspots")

    def record(doc: Dict[str, Any]) -> bool:
        return _audit(rec.tenant_id, "video_producer.asset_uploaded", {
            **base, "outcome": "accepted", "asset_id": doc["id"], "bytes": doc["bytes"], "width": doc["width"],
            "height": doc["height"], "hotspot_count": len(doc["hotspots"]), "has_source": bool(doc["source"])})

    try:
        doc = await asyncio.to_thread(_asset_store(rec).add, data, caption=caption, uploaded_by=_job_owner(rec),
                                      source=source or None, hotspots=spots, before_commit=record)
    except am.AssetNotRecorded:
        raise HTTPException(status_code=503, detail="The upload could not be recorded; nothing was stored") from None
    except am.AssetQuotaExceeded as e:
        refuse(409, str(e), "quota")
    except am.AssetError as e:
        refuse(422, str(e), _refusal_code(str(e)))
    return JSONResponse(status_code=201, content={"asset": _public_asset(doc)})


@router.post("/assets/import-penpot", status_code=201)
async def import_penpot_asset(file: UploadFile = File(...), caption: str = Form(""), rec=_SessionRec):
    """A Penpot board (SVG export) as an image for `still` scenes (ADR-2257 strand A). The upload is
    reduced to the plugin's closed shape model and only that model is rendered - by the producer's
    hardened Chromium, JavaScript off, network aborted - so no byte of the upload reaches the browser.
    The image is stored like any other asset, with the same audit-first order."""
    import hashlib  # noqa: PLC0415

    pim, am = _style_mod("penpot_import"), _asset_mod()
    buf = bytearray()
    too_big = False
    while True:
        chunk = await file.read(64 * 1024)
        if not chunk:
            break
        buf += chunk
        if len(buf) > _MAX_ASSET_UPLOAD_BYTES:
            too_big = True
            break
    data = bytes(buf)
    base = {"upload_bytes": len(data), "sha256_prefix": hashlib.sha256(data).hexdigest()[:12],
            "format": "penpot_svg"}

    def refuse(status: int, message: str, code: str):
        if not _audit(rec.tenant_id, "video_producer.asset_uploaded", {**base, "outcome": "refused", "refusal": code}):
            raise HTTPException(status_code=503, detail="The import could not be recorded")
        raise HTTPException(status_code=status, detail=message)

    if too_big:
        refuse(413, f"The file is larger than {_MAX_ASSET_UPLOAD_BYTES // (1024 * 1024)} MB", "too_large")
    if not data:
        refuse(400, "The file is empty", "empty")
    if len(caption) > _MAX_ASSET_FORM_CHARS:
        refuse(413, "The form fields are too long", "invalid")
    if not _looks_like_svg(data):
        refuse(415, "This is not an SVG file. Export a board from Penpot as SVG.", "not_svg")
    global _import_inflight
    with _import_lock:
        busy = _import_inflight >= _IMPORT_MAX_INFLIGHT
        if not busy:
            _import_inflight += 1
    if busy:
        raise HTTPException(status_code=429, detail="Too many imports are running; try again in a moment",
                            headers={"Retry-After": "5"})

    def _parse_bounded():
        global _import_inflight
        try:
            with _import_slots:
                return pim.parse_design(data)
        finally:
            with _import_lock:
                _import_inflight -= 1

    try:
        design = await asyncio.to_thread(_parse_bounded)
    except _style_mod("style_pack").StyleError as e:
        refuse(422, str(e), "unreadable")
    except Exception as e:  # noqa: BLE001 - a hostile file must not become a 500 with internals
        logger.warning("video producer: penpot import failed (%s)", type(e).__name__)
        refuse(422, "The design could not be read", "unreadable")
    if len(await asyncio.to_thread(_asset_store(rec).ids)) >= am.MAX_ASSETS:  # before a browser is spent on it
        refuse(409, f"At most {am.MAX_ASSETS} images per workspace; delete one first", "quota")
    if not _preview_slots.acquire(blocking=False):
        raise HTTPException(status_code=429, detail="Rendering is busy right now; try again in a moment",
                            headers={"Retry-After": "5"})
    try:
        png = await pim.render_png(design)
    except _style_mod("web_renderer").WebRenderError as e:
        logger.warning("video producer: penpot render failed (%s)", type(e).__name__)
        refuse(503, "The design could not be rendered on this host (is Chromium installed?)", "render_failed")
    finally:
        _preview_slots.release()
    text = caption.strip() or Path(file.filename or "Penpot board").stem[:60] or "Penpot board"

    def record(doc: Dict[str, Any]) -> bool:
        return _audit(rec.tenant_id, "video_producer.asset_uploaded", {
            **base, "outcome": "accepted", "asset_id": doc["id"], "bytes": doc["bytes"], "width": doc["width"],
            "height": doc["height"], "hotspot_count": 0, "has_source": False})

    try:
        doc = await asyncio.to_thread(_asset_store(rec).add, png, caption=text, uploaded_by=_job_owner(rec),
                                      source=None, hotspots=[], before_commit=record)
    except am.AssetNotRecorded:
        raise HTTPException(status_code=503, detail="The import could not be recorded; nothing was stored") from None
    except am.AssetQuotaExceeded as e:
        refuse(409, str(e), "quota")
    except am.AssetError as e:
        refuse(422, str(e), _refusal_code(str(e)))
    notes = [f"{what} not imported" for what in pim.describe_dropped(design.dropped)]
    return JSONResponse(status_code=201, content={"asset": _public_asset(doc), "notes": notes})


def _own_asset(rec, asset_id: str) -> Dict[str, Any]:
    """One of THIS tenant's assets; the same 404 for an unknown, malformed or foreign id."""
    am = _asset_mod()
    if not isinstance(asset_id, str) or not am.ID_RE.fullmatch(asset_id):
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND)
    try:
        return _asset_store(rec).public(asset_id)
    except am.AssetNotFound:
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND) from None
    except am.AssetError:
        raise HTTPException(status_code=409, detail="This image is damaged on disk; delete it and upload it again") from None


@router.get("/assets/{asset_id}/image")
def asset_image(asset_id: str, rec=_SessionRec):
    doc = _own_asset(rec, asset_id)
    am = _asset_mod()
    if not _audit(rec.tenant_id, "video_producer.asset_accessed", {"asset_id": doc["id"], "purpose": "preview"}):
        raise HTTPException(status_code=503, detail="The access could not be recorded")
    try:
        png = _asset_store(rec).get(asset_id)["png"]
    except (am.AssetNotFound, am.AssetError):
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND) from None
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Security-Policy": "default-src 'none'"})


@router.delete("/assets/{asset_id}", status_code=204)
def delete_asset(asset_id: str, rec=_SessionRec):
    doc = _own_asset(rec, asset_id)
    if not _audit(rec.tenant_id, "video_producer.asset_deleted", {"asset_id": doc["id"], "bytes": doc["bytes"]}):
        raise HTTPException(status_code=503, detail="The deletion could not be recorded; the image was kept")
    try:
        _asset_store(rec).delete(asset_id)
    except _asset_mod().AssetNotFound:
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND) from None
    return Response(status_code=204)


class AssetUpdateRequest(BaseModel):
    caption: Optional[str] = Field(None, max_length=1000)
    hotspots: Optional[List[Dict[str, Any]]] = Field(None, max_length=8)


@router.patch("/assets/{asset_id}")
async def update_asset(asset_id: str, req: AssetUpdateRequest, rec=_SessionRec):
    """Change a stored image's caption and/or hotspots (placed by clicking the picture); the image itself
    never changes. Session + CSRF (router dependency); audit-first: no record, no change."""
    am = _asset_mod()
    doc = await asyncio.to_thread(_own_asset, rec, asset_id)

    def record(new: Dict[str, Any]) -> bool:
        return _audit(rec.tenant_id, "video_producer.asset_updated", {
            "asset_id": doc["id"], "hotspot_count": len(new.get("hotspots") or []),
            "caption_changed": new.get("caption") != doc.get("caption")})

    try:
        new = await asyncio.to_thread(_asset_store(rec).update, asset_id, caption=req.caption,
                                      hotspots=req.hotspots, before_commit=record)
    except am.AssetNotRecorded:
        raise HTTPException(status_code=503, detail="The change could not be recorded; nothing was changed") from None
    except am.AssetNotFound:
        raise HTTPException(status_code=404, detail=_ASSET_NOT_FOUND) from None
    except am.AssetError as e:
        raise HTTPException(status_code=422, detail=str(e)[:200]) from None
    return {"asset": _public_asset(new)}


# ── Website capture as an asset (ADR-2249 D4, T-0113, T-0194) ─────────────────
# A user-initiated panel action, never a model decision. Off per tenant by default (a persisted switch
# the panel's Settings turn on), and off wherever the tenant has no ENABLED egress policy (spec.egress
# in tenant.corvin.yaml). The named host must pass the tenant's real L35 gate (a refusal answers at once
# and says so: the tenant's own policy is no secret). Every other host the page loads must be requested
# by the page itself and pass the same rules (forbidden / allowed / default, handed to the plugin's proxy
# as data) and the address policy (allow-by-global, pinned connect). Every failure that depends on the
# TARGET - refused by the address policy, unreachable, hung, the page navigated to a refused address,
# the browser failed - answers the same 422 at the same instant, ``_capture_fail_floor()`` after the
# request arrived, which is longer than the plugin's whole capture budget. Input, quota and the switch
# are checked before anything is fetched. Audit: host + sha256 prefix of the URL only, never the URL, its
# path or query; ``capture_started`` before any egress and always followed by ``capture_finished``.
# Audit writes never run on the event loop.
_CAPTURE_SWITCH_FILE = "capture_settings.json"
# > capture.CAPTURE_DEADLINE_S (12 s) + the kill grace (0.5 s, measured: a hung worker returns at 12.50 s)
# + the steps before the fetch (gate, two audit writes, name lookups): review 2 measured 13 s as too tight
_CAPTURE_FAIL_FLOOR_S = 14.0
_CAPTURE_NOT_POSSIBLE = "This address cannot be captured"


def _capture_fail_floor(cap) -> float:
    return max(_CAPTURE_FAIL_FLOOR_S, float(getattr(cap, "CAPTURE_DEADLINE_S", 0.0)) + 2.0)


def _capture_switch_path(rec) -> Path:
    return _tenant_base(rec) / _CAPTURE_SWITCH_FILE


def _capture_enabled(rec) -> bool:
    """The tenant's switch; anything but a readable ``{"enabled": true}`` is off (fail-closed)."""
    import json  # noqa: PLC0415

    try:
        p = _capture_switch_path(rec)
        if p.is_symlink() or not p.is_file() or p.stat().st_size > 1024:
            return False
        return json.loads(p.read_text("utf-8")).get("enabled") is True
    except (OSError, ValueError, AttributeError):
        return False


def _write_capture_switch(rec, enabled: bool) -> None:
    import json  # noqa: PLC0415
    import tempfile  # noqa: PLC0415

    p = _capture_switch_path(rec)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=f".{p.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump({"enabled": bool(enabled), "changed": _now_iso()}, f)
        os.chmod(tmp, 0o600)
        os.replace(tmp, p)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _capture_egress_gate(tenant_id: str):
    """The tenant's L35 egress gate when a policy is configured AND enabled, else None (= capture off).
    Unlike the engine-spawn gate this is fail-CLOSED: no file, no spec, a disabled spec or an import
    failure all mean "no policy"."""
    try:
        try:
            from corvin_operator.bridges.shared.egress_gate import load_egress_gate_for_tenant  # noqa: PLC0415
        except ImportError:
            from egress_gate import load_egress_gate_for_tenant  # type: ignore[import]  # noqa: PLC0415
        gate = load_egress_gate_for_tenant(tenant_id)
    except Exception:  # noqa: BLE001
        return None
    if gate is None or not getattr(getattr(gate, "policy", None), "enabled", False):
        return None
    return gate


def _capture_egress_rules(gate) -> Dict[str, Any]:
    """The tenant's L35 rules as data for the capture proxy (exact host match, forbidden wins)."""
    pol = gate.policy
    return {"forbidden": sorted(pol.forbidden_hosts), "allowed": sorted(pol.allowed_hosts),
            "default": pol.default_action}


def _console_names(request: Request) -> List[str]:
    import socket  # noqa: PLC0415

    names = {request.url.hostname or "", socket.gethostname(), socket.getfqdn()}
    return sorted(n for n in names if n)


class CaptureRequest(BaseModel):
    url: str = Field(..., max_length=2048)
    viewport: Literal["desktop", "mobile"] = "desktop"
    caption: str = Field("", max_length=1000)
    hotspots: List[Dict[str, Any]] = Field(default_factory=list, max_length=8)


@router.post("/capture", status_code=201)
async def capture_asset(req: CaptureRequest, request: Request, rec=_SessionRec):
    """"Add website screenshot": capture ``req.url`` once and store it as one of this tenant's assets.
    Session + CSRF (router dependency); 403 while the tenant switch is off, no egress policy exists or
    the policy does not allow the host."""
    import hashlib  # noqa: PLC0415
    from urllib.parse import urlsplit  # noqa: PLC0415

    t0 = time.monotonic()
    cap = _style_mod("capture")
    am = _asset_mod()
    tid = rec.tenant_id
    raw = req.url.strip()
    try:
        target = cap.check_url(raw)
    except cap.CaptureRefused:
        target = None
    base = {"host": (target or {}).get("host", "")[:253], "viewport": req.viewport,
            "url_sha256_prefix": hashlib.sha256(raw.encode("utf-8", "replace")).hexdigest()[:12]}

    async def finish(outcome: str, refusal: Optional[str] = None, **extra) -> bool:
        details = {**base, "outcome": outcome, "refusal": refusal, **extra,
                   "elapsed_ms": int((time.monotonic() - t0) * 1000)}
        return await asyncio.to_thread(_audit, tid, "video_producer.capture_finished", details)

    async def refuse(status: int, message: str, code: str):
        if not await finish("refused", code):
            raise HTTPException(status_code=503, detail="The request could not be recorded")
        raise HTTPException(status_code=status, detail=message)

    async def not_capturable(code: str, **extra):
        # the record is written AT the answer instant: its timestamp is readable (audit/tail), and written
        # earlier it would tell a hang (12.5 s) from a refusal (ms) after all (refutation 2026-10-14)
        await asyncio.sleep(max(0.0, t0 + _capture_fail_floor(cap) - time.monotonic()))
        recorded = await finish("failed", code, **extra)
        if not recorded:
            raise HTTPException(status_code=503, detail="The request could not be recorded")
        raise HTTPException(status_code=422, detail=_CAPTURE_NOT_POSSIBLE)

    if not await asyncio.to_thread(_capture_enabled, rec):
        await refuse(403, "Website capture is off for this workspace; turn it on in the Video Producer settings",
                     "switch_off")
    gate = await asyncio.to_thread(_capture_egress_gate, tid)
    if gate is None:
        await refuse(403, "Website capture needs an egress policy for this workspace (spec.egress in "
                          "tenant.corvin.yaml)", "no_egress_policy")
    if target is None:
        await refuse(422, "Enter a plain http:// or https:// address", "invalid_url")
    store = _asset_store(rec)
    try:
        meta = am.validate_meta(req.caption, req.hotspots)
    except am.AssetError as e:
        await refuse(422, str(e)[:200], _refusal_code(str(e)))
    if not await asyncio.to_thread(store.has_room):
        await refuse(409, f"At most {am.MAX_ASSETS} images per workspace; delete one first", "quota")
    try:
        decision = await asyncio.to_thread(gate.validate, target["host"], engine_id="video_producer_capture")
    except Exception:  # noqa: BLE001 - a gate error is a denial
        decision = None
    if decision is None or not decision.allowed:
        by_rule = decision is not None and not str(getattr(decision, "reason", "")).startswith("malformed") \
            and getattr(decision, "matched_rule", "") in ("forbidden_explicit", "default_deny")
        if by_rule:  # one of the tenant's own rules decided: no secret, say so at once
            await refuse(403, "This workspace's egress policy does not allow this host", "egress_denied")
        await not_capturable("egress_denied")  # e.g. an IP literal the gate cannot even parse
    names = await asyncio.to_thread(_console_names, request)
    if not await asyncio.to_thread(_audit, tid, "video_producer.capture_started", base):
        raise HTTPException(status_code=503, detail="The capture could not be recorded; nothing was fetched")
    try:
        res = await asyncio.to_thread(cap.capture_url, target["url"], viewport=req.viewport, console_names=names,
                                      egress=_capture_egress_rules(gate), tenant=tid,
                                      fail_hold_until=t0 + _capture_fail_floor(cap))
    except asyncio.CancelledError:
        # the client went away: close the record synchronously (one write; awaiting is no longer possible).
        # The worker thread runs on to its own deadline (12 s) and is killed there as a group.
        _audit(tid, "video_producer.capture_finished", {**base, "outcome": "failed", "refusal": "cancelled",
                                                        "elapsed_ms": int((time.monotonic() - t0) * 1000)})
        raise
    except Exception:  # noqa: BLE001 - started is always followed by finished
        await not_capturable("error")
    hosts = res.get("hosts") if isinstance(res.get("hosts"), dict) else {}
    counts = {"hosts_connected": int(hosts.get("connected", 0)),
              "hosts_refused": int(sum(v for k, v in hosts.items() if k != "connected"))}
    if not res["ok"]:
        if res["reason"] == "busy":
            await refuse(429, "A capture of this workspace is already running, or two are running on this host; "
                              "try again in a moment", "busy")
        if res["reason"] in ("no_browser", "unsupported"):
            await refuse(503, "Website capture is not available on this host", res["reason"])
        await not_capturable(res["reason"], **counts)
    final = res.get("final_url") or target["url"]
    final_host = (urlsplit(final).hostname or "")[:253]

    def record(doc: Dict[str, Any]) -> bool:
        return _audit(tid, "video_producer.capture_finished", {
            **base, "outcome": "accepted", "refusal": None, "asset_id": doc["id"], "bytes": doc["bytes"],
            "width": doc["width"], "height": doc["height"], "final_host": final_host, **counts,
            "elapsed_ms": int((time.monotonic() - t0) * 1000)})

    try:
        doc = await asyncio.to_thread(store.add, res["png"], caption=meta["caption"], uploaded_by=_job_owner(rec),
                                      source=final, hotspots=meta["hotspots"], before_commit=record)
    except am.AssetNotRecorded:
        await finish("failed", "not_recorded", **counts)
        raise HTTPException(status_code=503, detail="The capture could not be recorded; nothing was stored") from None
    except am.AssetQuotaExceeded as e:
        await refuse(409, str(e), "quota")
    except am.AssetError as e:
        await refuse(422, str(e)[:200], _refusal_code(str(e)))
    except Exception:  # noqa: BLE001 - a disk error: recorded, never a capture_started without its end
        await finish("failed", "store_error", **counts)
        raise HTTPException(status_code=500, detail="The screenshot could not be stored") from None
    return JSONResponse(status_code=201, content={"asset": _public_asset(doc)})
