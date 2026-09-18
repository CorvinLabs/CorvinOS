"""
Engine Configuration API — Model Selection Dashboard

Routes:
  GET  /v1/engine/config              — Read current per-task-type config + real classification stats
  PUT  /v1/engine/config              — Update model/provider choice per task type (persisted)
  POST /v1/engine/external-provider/test — Real Ollama/OpenRouter/OpenAI connectivity test
  GET  /v1/engine/health              — Health check

Real data (2026-09-10), replacing the Phase-1 K=1 mock:
  - Persistence: core.models.model_selection_config (JSON file per tenant),
    consulted by the shadow classifier (model_selector_shadow.py) via
    ModelSelector(overrides=...) — a saved choice has a real effect on the
    next turn's classification, not just on this page.
  - Stats (run_count / confidence / last_updated): aggregated from the
    tenant's real hash-chained audit chain, filtered to
    ``skill.model_selector.classified`` events (emitted by the SHADOW
    classification wired into corvin_operator/bridges/shared/adapter.py's two real
    turn call sites — see model_selector_shadow.py for why). Honest empty
    state (0 samples) is expected and correct until real turns accrue.
  - "corvinOS" task type has no classifier equivalent (task classification
    itself is deterministic regex/keyword rules, not model-driven) — its
    stats are always 0/none; the field stays for UI symmetry.

ADR-0641: Engine Configuration Console
ADR-0642: Model Selector Skill
ADR-0007: Tenant isolation (all config per-tenant)
ADR-0314: Learning infrastructure
"""

from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Any, Optional

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "corvin_operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

router = APIRouter()

TASK_TYPES = ("corvinOS", "SIMPLE", "MEDIUM", "COMPLEX")

#: Fallback list of provider ids that can serve a "native Claude" model, used
#: only when the registry cannot be read. The live answer comes from
#: :func:`_claude_provider_ids` — hardcoding it here meant a provider added to
#: the registry (Vertex, Foundry — ADR-0759) was configurable but never queried,
#: so its models never appeared and its reachability was never reported.
_CLAUDE_PROVIDERS_FALLBACK = ("anthropic", "bedrock")


def _claude_provider_ids() -> tuple[str, ...]:
    """Every provider that can serve a native Claude model on this host.

    Which one APPLIES depends on how Claude Code is logged in (API key vs.
    ``CLAUDE_CODE_USE_BEDROCK=1`` vs. Vertex/Foundry), so all of them are always
    queried and each reports its own reachability — an install must be able to
    see that a source it is not using is simply not credentialed here, rather
    than see a list that silently omits it.
    """
    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        providers = load_providers()
    except Exception:  # noqa: BLE001
        return _CLAUDE_PROVIDERS_FALLBACK
    ids = [pid for pid, spec in providers.items()
           if pid == "anthropic" or getattr(spec, "is_platform", False)]
    return tuple(ids) or _CLAUDE_PROVIDERS_FALLBACK


# ─────────────────────────────────────────────────────────────────
# Request/Response models
# ─────────────────────────────────────────────────────────────────


class ModelConfigRequest(BaseModel):
    """Model configuration for one task type."""
    model_config = {"extra": "forbid"}

    task_type: str = Field(..., pattern="^(corvinOS|SIMPLE|MEDIUM|COMPLEX)$")
    selected_model: str = Field(..., min_length=1, max_length=128)
    provider: Optional[str] = Field(
        None,
        description="ADR-0181 provider id (anthropic/openai/ollama_local/ollama_cloud/"
                    "openrouter); null = native Anthropic",
    )
    alternatives: list[str] = Field(default_factory=list)


class EngineConfigRequest(BaseModel):
    """Full engine configuration update."""
    model_config = {"extra": "forbid"}

    models: dict[str, ModelConfigRequest]


class ExternalProviderTestRequest(BaseModel):
    model_config = {"extra": "forbid"}

    # A shape constraint, not an allowlist: the set of real providers lives in the
    # ADR-0181 registry and is checked against it in the handler. An enum here
    # froze the list at four ids, so a provider added to the registry (bedrock)
    # was rejected with a 422 before the handler could ever see it.
    provider: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_]+$")


class ExternalProviderTestResponse(BaseModel):
    is_connected: bool
    latency_ms: Optional[float]
    error_message: Optional[str]
    model_count: int = 0


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────


def _tenant_id(rec: session_auth.SessionRecord) -> str:
    return rec.tenant_id or "_default"


def _model_config_module():
    """core.models.model_selection_config — imported lazily so a stripped
    install without ``core`` degrades to an HTTP 503 rather than a boot crash."""
    try:
        from core.models import model_selection_config as m  # noqa: PLC0415
        return m
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"model_selection_config unavailable: {exc}") from exc


CLASSIFIED_EVENT = "skill.model_selector.classified"


def _classified_chain_path(tenant_id: str) -> Path | None:
    """THE tenant chain, through the pinned ``forge.paths.tenant_audit_chain``
    mirror (never a hand-composed path). ``forge`` is importable only after
    ``corvin_core._bootstrap`` — ``skill_audit._ensure_operator_on_path`` is
    what made it importable for the old inline reader, so it is called here
    explicitly. ``None`` when the path cannot be resolved: the caller then sees
    no records, exactly as the old reader degraded to zeros."""
    try:
        from core.skills.skill_audit import _ensure_operator_on_path  # noqa: PLC0415
        _ensure_operator_on_path()
        from forge.paths import (
            tenant_audit_chain,  # type: ignore[import-not-found]  # noqa: PLC0415
        )
        return tenant_audit_chain(tenant_id)
    except Exception:  # noqa: BLE001
        try:
            from core.skills.skill_audit import audit_chain_path  # noqa: PLC0415
            return audit_chain_path(tenant_id)
        except Exception:  # noqa: BLE001
            return None


def _iter_classified(
    tenant_id: str,
    *,
    since_ts: float = 0.0,
    newest_first: bool = False,
    max_lines: int | None = None,
):
    """Yield the tenant's ``skill.model_selector.classified`` records (ADR-0885).

    ONE reader for every consumer — ``_real_stats`` (windowed aggregate), the
    analytics ``recent`` list and the ``feedback`` resolver. ``since_ts`` is the
    ADR-0760 epoch filter (0 = no filter). ``newest_first`` walks the file from
    its end; with ``max_lines`` the walk stops after that many lines were
    SCANNED (classified or not), which bounds the cost on a long chain —
    measured density on the service chain is 0.44 % classified, so 50 000
    lines hold ~220 classified records. An unreadable or absent chain yields
    nothing and never raises.
    """
    path = _classified_chain_path(tenant_id)
    if path is None or not path.exists():
        return
    try:
        if newest_first:
            lines_iter = _iter_lines_backwards(path)
        else:
            lines_iter = path.open("r", encoding="utf-8")
        scanned = 0
        try:
            for line in lines_iter:
                scanned += 1
                if max_lines is not None and scanned > max_lines:
                    return
                line = line.strip()
                # Cheap substring prefilter: a 137k-line chain is 0.44 %
                # classified, and json.loads on every line is the cost.
                if not line or CLASSIFIED_EVENT not in line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if rec.get("event_type") != CLASSIFIED_EVENT:
                    continue
                rec_ts = rec.get("ts")
                if since_ts and isinstance(rec_ts, (int, float)) and rec_ts < since_ts:
                    continue
                yield rec
        finally:
            close = getattr(lines_iter, "close", None)
            if close:
                close()
    except OSError:
        return


def _iter_lines_backwards(path: Path, block_size: int = 64 * 1024):
    """Lines of ``path`` from the last to the first, without reading the whole
    file — the chain is append-only, so its newest records are at the end."""
    with path.open("rb") as fh:
        fh.seek(0, 2)
        pos = fh.tell()
        tail = b""
        while pos > 0:
            step = min(block_size, pos)
            pos -= step
            fh.seek(pos)
            chunk = fh.read(step) + tail
            parts = chunk.split(b"\n")
            tail = parts[0]
            # Empty parts are yielded too, so ``max_lines`` counts the same
            # thing in both directions (the forward reader counts blank lines);
            # the trailing empty after the file's final newline makes the
            # backwards count one higher, i.e. the effective window is
            # max_lines - 1 — irrelevant at 500k, noted for honesty.
            for part in reversed(parts[1:]):
                yield part.decode("utf-8", errors="replace")
        yield tail.decode("utf-8", errors="replace")


def _real_stats(tenant_id: str) -> tuple[dict[str, dict[str, Any]], int, str | None]:
    """Aggregate REAL ``skill.model_selector.classified`` audit events into
    per-task-type {run_count, confidence_score} — never fabricated.

    Returns (stats_by_task_type, total_samples, last_ts_iso). An unreadable
    or absent chain is a normal, expected state (no turns yet) — returns all
    zeros, never raises.

    Narrowed by the SAME counting window as every other number on these two
    pages. It read the whole chain before, so the Engine Configuration card
    said "0 of 288 classified turns" (all time) directly above a usage line
    reading "18.2% of all turns" (windowed) — two totals, one card, different
    periods. That is exactly the split the window exists to prevent, and it is
    invisible until someone divides one number by the other.
    """
    try:
        from .. import usage_epoch  # noqa: PLC0415

        since_ts = usage_epoch.epoch_ts(tenant_id)
    except Exception:  # noqa: BLE001
        since_ts = 0.0
    from core.models.model_selection_config import COMPLEXITY_BY_TASK_TYPE  # noqa: PLC0415
    complexity_to_task = {v: k for k, v in COMPLEXITY_BY_TASK_TYPE.items()}
    sums: dict[str, float] = {t: 0.0 for t in TASK_TYPES}
    counts: dict[str, int] = {t: 0 for t in TASK_TYPES}
    last_ts: float | None = None

    for rec in _iter_classified(tenant_id, since_ts=since_ts):
        details = rec.get("details") or {}
        task_type = complexity_to_task.get(details.get("complexity"))
        if task_type is None:
            continue
        confidence = details.get("confidence")
        if isinstance(confidence, (int, float)):
            sums[task_type] += float(confidence)
            counts[task_type] += 1
        ts = rec.get("ts")
        if isinstance(ts, (int, float)):
            last_ts = ts if last_ts is None else max(last_ts, ts)

    stats: dict[str, dict[str, Any]] = {}
    for t in TASK_TYPES:
        n = counts[t]
        stats[t] = {
            "run_count": n,
            "confidence_score": round(sums[t] / n, 4) if n else 0.0,
        }
    total = sum(counts.values())
    last_iso = (
        datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat() if last_ts is not None else None
    )
    return stats, total, last_iso


def _short_reason(error: str | None) -> str | None:
    """A ≤48-char summary of ONE source's failure, for the one-line source label.

    The panel used to print a full line per source, error text and all. Three
    stacked sentences under a dropdown read as a malfunction rather than as
    provenance — and the longest of them is the keyless-Anthropic case, which on
    a Bedrock host is the normal state, not a fault. So the panel now prints one
    line and keeps the full text on hover; this is the short half.

    ``error`` is always returned unchanged alongside it — a hint NEVER replaces
    the reason, it only shortens it for a place where the long form does not fit.
    Cut at the first clause boundary rather than hard-truncating: that keeps the
    cause ("no ANTHROPIC_API_KEY configured") and drops the remedy ("Add an API
    key under Settings → API Keys …"), which is the half that doesn't fit and the
    half the operator doesn't need until they act on it.
    """
    if not error:
        return None
    # Deliberately NOT splitting on ":" — "unreachable: connection refused" is
    # already short, and its half before the colon says nothing.
    head = re.split(r"[,.;]\s|\s[—–]\s", error.strip(), maxsplit=1)[0].strip()
    if not head:
        head = error.strip()
    return head if len(head) <= 48 else head[:47].rstrip() + "…"


def _is_claude_model_id(model_id: str) -> bool:
    """Does this id name a Claude model, in ANY of the forms a host addresses one?

    Bedrock hands back three shapes for the same family — ``anthropic.claude-…``
    (foundation model), ``us.anthropic.claude-…`` / ``global.anthropic.claude-…``
    (inference profiles) — and the Anthropic API hands back a bare ``claude-…``.
    Matching the vendor token rather than a prefix list covers all of them, and a
    new region prefix does not silently drop the model from the picker.
    """
    lowered = model_id.lower()
    return "claude" in lowered or "anthropic" in lowered


def claude_catalog_or_503() -> set[str]:
    """The offline Claude catalogue for a PUT validator — or 503 when the
    engine registry FAILED to load and nothing ever loaded (ADR-0885 step 2b).

    Before this, ``PUT /v1/engine/config`` accepted any id on an empty
    catalogue because an empty set could mean either "the YAML is unreadable"
    (refusing every save would lock the operator out) or "nothing declared";
    the two were indistinguishable. ``engine_models.registry_load_status``
    now separates them: a real load failure is a 503 the operator can act on,
    a loaded-but-empty catalogue is still accepted with a warning (as before),
    and a failure AFTER a good load is served from the stale-good cache and is
    not an error. Shared by ``engine_api`` and ``engine`` so both Save paths
    have ONE semantic.
    """
    # Load FIRST (through _claude_catalog_offline → load_registry), read the
    # status SECOND — in a process where nothing has loaded yet the status is
    # (True, None) until a load was attempted (review 2026-09-18).
    known = _claude_catalog_offline()
    try:
        from engine_models import registry_load_status  # type: ignore[import]  # noqa: PLC0415
        ok, error = registry_load_status()
    except Exception:  # noqa: BLE001 — stripped install: behave as before
        ok, error = True, None
    if not ok and not known:
        raise HTTPException(
            status_code=503,
            detail=f"The engine model registry could not be loaded ({error}); "
                   f"model ids cannot be validated. Fix the registry file and retry.",
        )
    return known


def _claude_catalog_offline() -> set[str]:
    """Ids a saved choice may name, resolved WITHOUT touching the network.

    A PUT must not be gated on provider reachability — a validator that calls out
    would reject every save the moment the network hiccups. Both sources here are
    on-disk and real: the ADR-0119 registry (shipped configuration) and
    ``model_catalog`` (what a provider ANSWERED on its last successful fetch).
    """
    ids: set[str] = set()
    try:
        from engine_models import load_registry  # type: ignore[import]  # noqa: PLC0415
        for spec in load_registry(force_reload=False).values():
            for role_attr in ("os_models", "worker_models"):
                for entry in getattr(spec, role_attr, None) or []:
                    entry_id = getattr(entry, "id", "")
                    if entry_id and _is_claude_model_id(entry_id):
                        ids.add(entry_id)
    except Exception:  # noqa: BLE001
        pass
    try:
        import model_catalog  # type: ignore[import]  # noqa: PLC0415
        for provider_id in _claude_provider_ids():
            for entry in model_catalog.catalog_models(provider_id) or []:
                entry_id = entry.get("id") if isinstance(entry, dict) else None
                if entry_id and _is_claude_model_id(entry_id):
                    ids.add(entry_id)
    except Exception:  # noqa: BLE001
        pass
    return ids


# ─────────────────────────────────────────────────────────────────
# API Routes
# ─────────────────────────────────────────────────────────────────


@router.get("/v1/engine/claude-models")
async def get_claude_models(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Every Claude model this host can actually select, from ALL live sources.

    Three sources are queried and UNIONED, because no single one is complete:

    * ``registry`` — the ADR-0119 curated list. Offline, always available, and a
      release-time snapshot, so it is the floor and never the whole answer.
    * ``anthropic_live`` — ``GET /v1/models``. Authoritative on an API-key login;
      returns nothing on a subscription or Bedrock login, which exposes no key —
      flagged ``credential_absent`` there, because an absent key on such a host is
      the normal state and must not be presented as a broken source.
    * ``bedrock_live`` — ``ListFoundationModels`` + ``ListInferenceProfiles``,
      SigV4-signed. Authoritative on a ``CLAUDE_CODE_USE_BEDROCK=1`` host, and the
      only source that knows the ``us.anthropic.claude-…`` inference-profile ids
      Claude Code actually passes as ``ANTHROPIC_MODEL`` there.

    Each entry names the sources it came from, and each source reports its own
    reachability and error, so an incomplete list is visible AS incomplete rather
    than looking like the full truth.
    """
    tenant_id = _tenant_id(rec)
    entries: dict[str, dict[str, Any]] = {}
    sources: list[dict[str, Any]] = []

    def _add(model_id: str, label: str, source: str, provider: str) -> None:
        entry = entries.setdefault(
            model_id,
            {"id": model_id, "label": label or model_id, "sources": [], "providers": []},
        )
        if source not in entry["sources"]:
            entry["sources"].append(source)
        if provider and provider not in entry["providers"]:
            entry["providers"].append(provider)
        # A live provider's own display_name beats the registry's hand-written
        # label — it is what the vendor calls the model today.
        if source != "registry" and label:
            entry["label"] = label

    # DISTINCT model ids, not entries scanned. This counted every occurrence,
    # and the registry lists the same model under both `os_models` and
    # `worker_models` — so a registry offering 7 Claude ids reported "12",
    # sitting directly beside a "7 models" union total it was supposed to
    # explain. Every other source here already reports distinct ids.
    registry_ids: set[str] = set()
    registry_error: str | None = None
    default_model_id = ""
    try:
        from engine_models import load_registry  # type: ignore[import]  # noqa: PLC0415
        registry = load_registry(force_reload=True)
        for spec in registry.values():
            live = getattr(spec, "live_models", None)
            provider = getattr(live, "provider", "") if live is not None else ""
            for role_attr in ("os_models", "worker_models"):
                for entry in getattr(spec, role_attr, None) or []:
                    entry_id = getattr(entry, "id", "")
                    if entry_id and _is_claude_model_id(entry_id):
                        _add(entry_id, getattr(entry, "label", ""), "registry", provider)
                        registry_ids.add(entry_id)
        # The registry's own marked default for Claude Code's worker role. The UI
        # needs SOME id to fall back to when the operator detaches an external
        # provider, and taking it from the registry keeps that fallback a real
        # configured value instead of a constant in the page.
        claude_code = registry.get("claude_code")
        for entry in getattr(claude_code, "worker_models", None) or []:
            if getattr(entry, "default", False) and getattr(entry, "id", ""):
                default_model_id = entry.id
                break
    except Exception as exc:  # noqa: BLE001
        registry_error = f"{type(exc).__name__}: {str(exc)[:120]}"
    sources.append({
        "id": "registry", "label": "Curated registry (ADR-0119)",
        # A one-word label for the compact source line, where the ADR reference
        # costs more width than it earns. The full label stays above it.
        "short_label": "Curated registry",
        "reachable": registry_error is None, "count": len(registry_ids),
        "error": registry_error, "hint": _short_reason(registry_error), "live": False,
        # The registry is on disk; it can never be waiting for a credential.
        "credential_absent": False,
    })

    for provider_id in _claude_provider_ids():
        fetched = _fetch_provider_models(provider_id, tenant_id)
        matched = 0
        for model in fetched.get("models") or []:
            model_id = model.get("id") if isinstance(model, dict) else None
            if model_id and _is_claude_model_id(model_id):
                _add(model_id, model.get("label") or "", f"{provider_id}_live", provider_id)
                matched += 1
        label = fetched.get("provider_label") or provider_id
        error = fetched.get("error")
        sources.append({
            "id": f"{provider_id}_live",
            "label": label,
            # "Anthropic (Claude)" → "Anthropic": in the compact line every source
            # is a Claude source, so the qualifier is noise there.
            "short_label": re.sub(r"\s*\([^)]*\)$", "", label).strip() or label,
            "reachable": bool(fetched.get("reachable")),
            # Claude models found, not every model the provider offers — Bedrock
            # returns ~160 across every vendor and only a slice is Claude.
            "count": matched,
            "error": error,
            "hint": _short_reason(error),
            "live": True,
            "detail": fetched.get("detail"),
            # Not a failure: this host authenticates elsewhere and never had a key
            # for this provider. Reported as a FACT, not as a display decision —
            # whether it is worth naming in the compact line depends on whether
            # any other live source answered, which is the panel's call.
            "credential_absent": bool(fetched.get("credential_absent")),
        })

    models = sorted(entries.values(), key=lambda m: m["id"])
    return {
        "tenant_id": tenant_id,
        "models": models,
        "count": len(models),
        "sources": sources,
        "default_model_id": default_model_id or None,
    }


def _fetch_provider_models(provider_id: str, tenant_id: str) -> dict[str, Any]:
    """Live-fetch one provider's models, honouring the L35 egress gate.

    Mirrors ``routes/engine.py::get_provider_models`` rather than calling it: that
    one is a FastAPI endpoint with a session dependency, so reusing it from here
    would mean constructing a request. Never raises.
    """
    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        from engine_providers import fetch_models  # type: ignore[import]  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"reachable": False, "models": [], "error": f"module error: {exc}"}

    spec = load_providers(force_reload=True).get(provider_id)
    if spec is None:
        return {"reachable": False, "models": [], "error": f"unknown provider '{provider_id}'"}

    if spec.kind == "cloud" and spec.egress_url:
        try:
            from .engine import _egress_denied  # noqa: PLC0415
            denied = _egress_denied(spec.egress_url, tenant_id)
        except Exception:  # noqa: BLE001
            denied = None
        if denied:
            return {"reachable": False, "models": [], "error": denied,
                    "provider_label": spec.label}

    result = fetch_models(
        provider_id, base_url=spec.base_url,
        model_source=spec.model_source, credential_env=spec.credential_env,
        platform_env=spec.platform_env,
    )
    result["provider_label"] = spec.label
    # Bedrock resolves its endpoint + credential at fetch time; naming both lets
    # the operator see WHICH account/region the list came from.
    region = result.get("region")
    credential_source = result.get("credential_source")
    if region or credential_source:
        result["detail"] = " · ".join(
            part for part in (region, credential_source) if part
        )
    return result


@router.get("/v1/engine/model-usage")
async def get_model_usage(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Per-model and per-provider usage share, counted from the audit chain.

    See ``model_usage`` for why the chain is the source and not a counter table.
    Read-only; emits no audit event of its own.
    """
    tenant_id = _tenant_id(rec)
    try:
        from ..model_usage import model_usage  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=503, detail=f"model_usage unavailable: {exc}") from exc
    return model_usage(tenant_id)


@router.get("/v1/engine/config")
async def get_engine_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Read current per-task-type config + REAL, LEARNED confidence.

    ``confidence_score``/``run_count`` per task type come from
    core.learning.model_selection_optimizer.ConfidenceOptimizer (ADR-0644,
    Bayesian + EMA) for the tier's CURRENTLY selected model — real outcome
    feedback closes this loop end to end (2026-09-10):
    corvin_operator/bridges/shared/model_selector_shadow.py classifies every real
    turn (SHADOW — never alters routing) and reports its real success/failure
    back into this same optimizer. 0 samples is the honest, expected state
    until real turns accrue; ``is_converged`` only turns true once the
    variance criterion (ADR-0644: n>=5, then <0.05 variance over the last 50
    samples) is genuinely met — never claimed early.
    """
    tenant_id = _tenant_id(rec)
    cfg_mod = _model_config_module()
    cfg = cfg_mod.load_config(tenant_id)
    classified, total_classified, last_ts = _real_stats(tenant_id)

    from core.learning.model_selection_optimizer import get_optimizer
    optimizer = get_optimizer()

    models: dict[str, Any] = {}
    converged_count = 0
    learned_samples = 0
    for task_type in TASK_TYPES:
        entry = cfg.get(task_type) or {}
        model_id = entry.get("selected_model") or ""
        if model_id:
            model_stats = optimizer.get_stats(task_type, model_id, tenant_id)
            is_converged = optimizer.is_converged(task_type, model_id, tenant_id)
        else:
            model_stats = None
            is_converged = False
        n = model_stats.n_samples if model_stats else 0
        learned_samples += n
        if is_converged:
            converged_count += 1
        models[task_type] = {
            "task_type": task_type,
            "selected_model": entry.get("selected_model"),
            "provider": entry.get("provider"),
            "alternatives": entry.get("alternatives") or [],
            "confidence_score": round(model_stats.confidence_score, 4) if model_stats else 0.0,
            "run_count": n,
            "is_converged": is_converged,
            # How many real turns the shadow classifier put in THIS tier. It was
            # already counted here and thrown away, which left the empty state
            # unable to say anything beyond "nothing learned yet" — and "nothing
            # learned yet" reads as a defect when three of four tiers show it.
            # With this number it reads as what it is: every turn so far landed
            # in one tier, so the others have genuinely never been exercised.
            # Distinct from run_count on purpose: run_count is outcome samples
            # for (tier, currently-selected model), so re-pointing a tier at
            # another model resets it to 0 while classified_count keeps counting.
            "classified_count": classified.get(task_type, {}).get("run_count", 0),
        }

    return {
        "tenant_id": tenant_id,
        "models": models,
        "last_updated": datetime.now(timezone.utc).isoformat(),
        # "idle" — no real turn classified yet. "learning" — real outcome
        # samples exist, not yet converged everywhere. "converged" — every
        # task type with a model assigned has met the real variance
        # criterion. Never fabricated; a fresh tenant genuinely starts idle.
        "learning_status": (
            "idle" if total_classified == 0
            else "converged"
            if (_tiers_with_model := sum(1 for t in TASK_TYPES if cfg.get(t, {}).get("selected_model")))
            and converged_count == _tiers_with_model
            else "learning"
        ),
        "last_learning_update": last_ts,
        "total_samples": total_classified,
        "total_learned_samples": learned_samples,
    }


@router.put("/v1/engine/config")
async def update_engine_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
    config: EngineConfigRequest = Body(...),
) -> dict[str, Any]:
    """Persist model/provider choice per task type.

    Validates: task_type is one of the four known ones; a null/anthropic
    provider requires a known Claude model id; a non-anthropic provider is
    checked against the live ADR-0181 provider registry.
    """
    tenant_id = _tenant_id(rec)
    if not config.models:
        raise HTTPException(status_code=400, detail="models cannot be empty")

    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        known_providers = set(load_providers(force_reload=True).keys())
    except Exception:  # noqa: BLE001
        known_providers = set()

    to_save: dict[str, dict[str, Any]] = {}
    for task_type, model_cfg in config.models.items():
        if task_type not in TASK_TYPES:
            raise HTTPException(status_code=400, detail=f"Invalid task_type: {task_type}")
        if model_cfg.provider is None:
            # 503 when the registry failed to load (ADR-0885 step 2b); a
            # loaded-but-empty catalogue still accepts, as before — refusing
            # every save because nothing is declared would lock the operator out.
            known_claude = claude_catalog_or_503()
            if known_claude and model_cfg.selected_model not in known_claude:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unknown Claude model {model_cfg.selected_model!r} for {task_type}. "
                          f"Not offered by the curated registry or by any provider's "
                          f"last live model list.",
                )
        elif known_providers and model_cfg.provider not in known_providers:
            raise HTTPException(
                status_code=400,
                detail=f"Unknown provider {model_cfg.provider!r} for {task_type}. "
                      f"Choose from: {sorted(known_providers)}",
            )
        to_save[task_type] = {
            "selected_model": model_cfg.selected_model,
            "provider": model_cfg.provider,
            "alternatives": model_cfg.alternatives,
        }

    cfg_mod = _model_config_module()
    merged = cfg_mod.load_config(tenant_id)
    merged.update(to_save)
    cfg_mod.save_config(tenant_id, merged)

    try:
        from .. import audit as console_audit  # noqa: PLC0415
        console_audit.action_performed(
            tenant_id=tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="model_selection.config_updated",
            target_kind="engine_config",
            target_id=",".join(sorted(to_save.keys())),
        )
    except Exception:  # noqa: BLE001
        pass

    return await get_engine_config(rec)


@router.post(
    "/v1/engine/external-provider/test",
    response_model=ExternalProviderTestResponse,
)
async def test_external_provider(
    _rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    test_req: ExternalProviderTestRequest = Body(...),
) -> dict[str, Any]:
    """Real connectivity test — reuses the same ADR-0181 live-fetch path the
    Engine Configuration (provider/model) endpoints use, so "Test Connection"
    reports the actual reachability + model count, not a canned success."""
    import time

    try:
        from engine_models import load_providers  # type: ignore[import]  # noqa: PLC0415
        from engine_providers import fetch_models  # type: ignore[import]  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        return {"is_connected": False, "latency_ms": None, "error_message": str(exc), "model_count": 0}

    spec = load_providers(force_reload=True).get(test_req.provider)
    if spec is None:
        return {
            "is_connected": False, "latency_ms": None,
            "error_message": f"unknown provider '{test_req.provider}'", "model_count": 0,
        }

    start = time.time()
    result = fetch_models(
        test_req.provider, base_url=spec.base_url,
        model_source=spec.model_source, credential_env=spec.credential_env,
        platform_env=spec.platform_env,
    )
    latency_ms = (time.time() - start) * 1000
    return {
        "is_connected": bool(result.get("reachable")),
        "latency_ms": round(latency_ms, 1),
        "error_message": result.get("error"),
        "model_count": result.get("count", 0),
    }


@router.get("/v1/engine/health")
async def engine_health() -> dict[str, Any]:
    return {"status": "ok", "timestamp": datetime.now(timezone.utc).isoformat()}
