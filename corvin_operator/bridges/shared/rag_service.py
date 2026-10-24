"""RAG query service - the ONE place a retrieval query is gated, run, counted and audited.

Two callers, same behaviour:

* the console (``POST /v1/console/rag/query`` and the Knowledge page), and
* the chat tool ``rag_query`` (forge MCP server) - so an answer in a chat turn
  is grounded in exactly the data the Query Tester shows.

Steps: classification (highest of the caller's label and every provider asked) ->
L34 data-classification gate (fail-closed) -> orchestrator query -> per-provider
counters (``registry.json``) -> hash-chained audit record (metadata only, never the
query text or the retrieved documents).
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, Optional

from . import rag_registry_store as store

logger = logging.getLogger(__name__)

CLASSIFICATION_ORDER = ("PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET")

_L34_GATES: dict[str, Any] = {}
_AUDIT_EMITTERS: dict[str, Any] = {}


class VaultTokens:
    """Auth tokens for RAG providers, resolved from the secret vault on use.

    A manifest names the env var holding its token (``auth.token_env_var``); the
    value lives in the forge secret vault under that name. Looked up per request
    (never copied into the engine), vault ONLY - the process environment is
    deliberately not consulted: a manifest imported from the Hub names an arbitrary
    variable, and falling back to ``os.environ`` would send e.g. a provider API key
    to whatever endpoint that manifest points at.
    """

    _NEVER = ("ANTHROPIC", "OPENAI", "OPENROUTER", "OLLAMA", "CORVIN", "AWS_", "GOOGLE_")

    def get(self, name: Any, default: Any = None) -> Any:
        if not isinstance(name, str) or not name:
            return default
        if any(tok in name.upper() for tok in self._NEVER):
            return default
        try:
            from forge import secret_vault as _vault  # noqa: PLC0415

            return _vault.load_vault().get(name, default)
        except Exception:  # noqa: BLE001 - no vault / unreadable vault = no token
            return default


def health_word(status: Optional[dict]) -> str:
    """UI health vocabulary: healthy | unhealthy | unknown.

    The orchestrator reports a probe status (``success`` ...) and a circuit state
    (``closed|open|half_open``); the badge speaks healthy/unhealthy. The raw circuit
    state is returned separately as ``circuit_state``.
    """
    if not status:
        return "unknown"
    return "healthy" if status.get("status") == "success" else "unhealthy"


def manifest_summary(registry_dir: Path, provider_id: str) -> dict[str, Any]:
    """Display fields read from the provider's manifest (name, classification, zone...)."""
    out: dict[str, Any] = {}
    try:
        import yaml  # noqa: PLC0415

        path = store.find_manifest(registry_dir, provider_id)
        data = yaml.safe_load(path.read_text(encoding="utf-8")) if path else None
        if isinstance(data, dict):
            meta = data.get("metadata") or {}
            spec = data.get("spec") or {}
            out = {
                "name": meta.get("name") or provider_id,
                "description": meta.get("description") or "",
                "data_classification": str(spec.get("dataClassification") or "PUBLIC").upper(),
                "compliance_zone": spec.get("complianceZone") or "",
                "capabilities": list(spec.get("capabilities") or []),
            }
    except Exception:  # noqa: BLE001 - display-only
        pass
    return out


def effective_classification(requested: str, provider_ids: list[str], registry_dir: Path) -> str:
    """Highest of the caller's label and the classification of every provider asked.

    The UI always sent ``PUBLIC``, so the L34 gate never saw what the providers
    actually hold. The gate now judges the data the query will touch.
    """
    levels = [str(requested or "PUBLIC").upper()]
    for pid in provider_ids:
        levels.append(manifest_summary(registry_dir, pid).get("data_classification", "PUBLIC"))
    known = [lv for lv in levels if lv in CLASSIFICATION_ORDER]
    if not known:
        return "SECRET"  # unknown label: fail closed to the strictest level
    return max(known, key=CLASSIFICATION_ORDER.index)


def _l34_gate(tenant_id: str) -> Any:
    if tenant_id not in _L34_GATES:
        from .rag_data_classification import L34DataClassificationGate  # noqa: PLC0415

        _L34_GATES[tenant_id] = L34DataClassificationGate(tenant_id)
    return _L34_GATES[tenant_id]


def gate_classification(tenant_id: str, classification: str, text: str = "") -> tuple[bool, Optional[str]]:
    """L34 verdict for data of *classification* flowing to a chat engine (fail-closed)."""
    from .rag_data_classification import DataClassification  # noqa: PLC0415

    try:
        level = DataClassification[str(classification).upper()]
    except KeyError:
        return False, f"unknown classification {classification!r}"
    return _l34_gate(tenant_id).check(text, level)


def _emitter(tenant_id: str) -> Any:
    if tenant_id not in _AUDIT_EMITTERS:
        from .rag_audit_events import RAGAuditEmitter  # noqa: PLC0415

        _AUDIT_EMITTERS[tenant_id] = RAGAuditEmitter()
    return _AUDIT_EMITTERS[tenant_id]


def _blocked(error: str) -> dict[str, Any]:
    return {
        "items": [],
        "total_time_ms": 0,
        "providers_queried": 0,
        "cache_hit": False,
        "error": error,
        "blocked_by": "L34_DATA_CLASSIFICATION",
    }


async def run_query(
    tenant_id: str,
    registry_dir: Path,
    orchestrator: Any,
    text: str,
    *,
    limit: int = 5,
    preferred_providers: Optional[list[str]] = None,
    timeout_ms: int = 5000,
    classification: str = "PUBLIC",
    safe_url: Any = None,
) -> dict[str, Any]:
    """Gate, run, count and audit one query. Returns the API response dict."""
    from .rag_data_classification import DataClassification  # noqa: PLC0415
    from .rag_query_engine import RAGQuery  # noqa: PLC0415

    preferred = list(preferred_providers or [])
    try:
        asked = preferred or store.provider_ids(registry_dir)
        classification = effective_classification(classification, asked, registry_dir)
    except Exception:  # noqa: BLE001
        classification = "SECRET"  # cannot classify -> strictest level (fail closed)

    # ── L34: Data Classification Gate (fail-closed per ADR-0042) ──
    try:
        level = DataClassification[classification.upper()]
        allowed, reason = _l34_gate(tenant_id).check(text, level)
    except Exception as exc:  # noqa: BLE001
        logger.error("L34 gate check error (fail-closed): %s", exc)
        return _blocked("data_classification_gate_unavailable")
    if not allowed:
        try:
            _emitter(tenant_id).query_blocked(tenant_id, reason=reason, classification=level.name)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Audit emit failed: %s", exc)
        return _blocked(reason or "classification not allowed")

    if not (text or "").strip():
        return {"items": [], "total_time_ms": 0, "providers_queried": 0,
                "cache_hit": False, "error": "Empty query"}

    start = time.time()
    if orchestrator is None:
        return {"items": [], "total_time_ms": int((time.time() - start) * 1000),
                "providers_queried": 0, "cache_hit": False, "status": "no_providers_configured"}

    try:
        outcome = await orchestrator.query_with_meta(
            RAGQuery(query=text, limit=limit, preferred_providers=preferred or None,
                     timeout_ms=timeout_ms)
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Orchestrator query failed: %s", exc)
        return {"items": [], "total_time_ms": int((time.time() - start) * 1000),
                "providers_queried": 0, "cache_hit": False,
                "status": "query_failed", "error": "rag_query_failed"}

    elapsed_ms = int((time.time() - start) * 1000)
    if not outcome.cache_hit:
        store.record_query(registry_dir, outcome.provider_ids, elapsed_ms)
    try:
        _emitter(tenant_id).query_executed(
            tenant_id,
            provider_count=len(outcome.queried) if not outcome.cache_hit else 0,
            result_count=len(outcome.items),
            latency_ms=elapsed_ms,
            cache_hit=outcome.cache_hit,
            classification=classification,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Audit emit failed: %s", exc)

    if safe_url is None:
        from .rag_query_engine import safe_http_url as safe_url  # noqa: PLC0415
    return {
        "items": [
            {
                "content": item.content,
                "score": item.score,
                "metadata": item.metadata,
                # `source_url` originates from a REMOTE provider and is rendered as
                # an <a href>: allowlist http(s) at the API boundary as well as in
                # the query engine.
                "source_url": safe_url(item.source_url),
            }
            for item in outcome.items
        ],
        "total_time_ms": elapsed_ms,
        "providers_queried": len(outcome.queried),
        "providers_answered": outcome.provider_ids,
        "classification": classification,
        "cache_hit": outcome.cache_hit,
    }
