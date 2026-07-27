"""ADR-0181 — live model-list fetch per provider.

Given a provider spec (from ``engine_models``), fetch the model IDs the provider
actually offers right now:
  * ``anthropic``  → GET {base_url}/v1/models              (paginated, cached)
  * ``ollama``     → GET {base_url}/api/tags               (local + cloud)
  * ``openrouter`` → GET {base_url}/models                 (public catalogue)
  * ``static``     → no live list (use the curated registry entries)

The ``anthropic`` source differs from the other two in both directions: it walks
``has_more``/``last_id`` pages, and it WRITES what it finds to ``model_catalog``
so ``engine_models.load_registry()`` can merge it into the curated picker. That
merge is the whole point — a fetch whose result nobody stores changes nothing
the operator can see. It is also the one source with a benign no-credential
case: a Claude Code subscription login exposes no API key, so a keyless call
returns an explanation and does not egress.

Credentials: the provider's ``credential_env`` names an env var; its value
(the API key) is resolved via provider_keys.resolve_by_env_var at request
time (env override first, then service.env — so a key an operator just
saved through Settings -> API Keys is picked up immediately, without
needing the console process restarted) — the key value never lives in
config, code, logs, or audit. Cloud fetches are network egress; the provider
``base_url`` host must be on the L35 allowlist (the caller/route enforces).

stdlib only (urllib) — no new dependency. Never raises; returns a status dict.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

_SHARED_DIR = Path(__file__).resolve().parent
if str(_SHARED_DIR) not in sys.path:
    sys.path.insert(0, str(_SHARED_DIR))
import model_catalog as _model_catalog  # type: ignore  # noqa: E402
import provider_keys as _provider_keys  # type: ignore  # noqa: E402

#: Anthropic's Messages API version header. A dated constant, not a moving
#: target: the value pins the response SHAPE this parser was written against.
ANTHROPIC_VERSION = "2023-06-01"

#: Page-walk bound. /v1/models returns a handful of pages at most; the cap is a
#: backstop against a provider that reports `has_more` forever, not a real limit.
_MAX_MODEL_PAGES = 20


def _get_json(
    url: str,
    *,
    bearer: str = "",
    headers: dict | None = None,
    timeout: float = 8.0,
) -> Any:
    """GET JSON. ``bearer`` sets an Authorization header; ``headers`` merges extra
    ones — Anthropic authenticates with ``x-api-key``, not a bearer token."""
    _headers = {"Accept": "application/json"}
    if bearer:
        _headers["Authorization"] = f"Bearer {bearer}"
    if headers:
        _headers.update(headers)
    req = urllib.request.Request(url, headers=_headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed provider URL
        return json.loads(resp.read().decode("utf-8", "replace"))


def _label_for(model_id: str) -> str:
    return model_id


def _fetch_anthropic(result: dict, *, base: str, key: str, timeout: float) -> dict:
    """Walk ``GET /v1/models`` and cache the result. Never raises.

    Kept out of :func:`fetch_models`'s inline branches because this source is the
    only one that PAGINATES and the only one that writes the shared catalogue —
    inlining it would hide two concerns inside a branch that reads like the
    one-liners around it.
    """
    models: list[dict] = []
    after_id = ""
    for _ in range(_MAX_MODEL_PAGES):
        url = f"{base}/v1/models?limit=100"
        if after_id:
            url += f"&after_id={after_id}"
        data = _get_json(
            url,
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION},
            timeout=timeout,
        ) or {}
        for m in data.get("data") or []:
            if isinstance(m, dict) and m.get("id"):
                models.append({
                    "id": m["id"],
                    # display_name is the human label ("Claude Opus 5"); id is the
                    # wire value. Fall back to the id so a provider that omits the
                    # field still yields a usable picker entry.
                    "label": m.get("display_name") or m["id"],
                })
        last_id = data.get("last_id") or ""
        # `has_more: true` with no cursor is a provider bug, and trusting it would
        # spin the walk re-requesting page 1 forever. Stop instead.
        if not data.get("has_more") or not last_id:
            break
        after_id = last_id

    result.update(reachable=True, models=models, count=len(models))
    # The fetch exists to feed engine_models' merge — a reachable fetch whose
    # result never reaches the cache has accomplished nothing. Best-effort
    # though: a read-only or full disk must not turn a good response into a
    # failed one.
    try:
        result["cached"] = bool(_model_catalog.store_models("anthropic", models))
    except Exception:  # noqa: BLE001 — a cache write must not cost the response
        result["cached"] = False
    return result


def fetch_models(
    provider: str,
    *,
    base_url: str,
    model_source: str,
    credential_env: str = "",
    timeout: float = 8.0,
) -> dict:
    """Return {provider, reachable, models:[{id,label}], count, error}.

    ``models`` is empty for ``static`` sources (the console shows the curated
    registry list for those). Never raises."""
    result: dict[str, Any] = {"provider": provider, "reachable": False,
                              "models": [], "count": 0, "error": None}
    if model_source == "static":
        result.update(reachable=True, error=None)
        result["note"] = "static provider — use the curated model list"
        return result

    key = (_provider_keys.resolve_by_env_var(credential_env) or "") if credential_env else ""
    base = base_url.rstrip("/")

    if model_source == "anthropic" and not key:
        # The COMMON case, not an error: a Claude Code subscription login exposes
        # no API key at all. Explain it and do not egress — a keyless request
        # would come back 401 and read like a broken credential rather than an
        # absent one.
        result["error"] = (
            f"no {credential_env or 'ANTHROPIC_API_KEY'} configured — showing the "
            f"curated model list. Add an API key under Settings → API Keys to see "
            f"Anthropic's live model list."
        )
        return result

    try:
        if model_source == "anthropic":
            return _fetch_anthropic(result, base=base, key=key, timeout=timeout)
        if model_source == "ollama":
            data = _get_json(f"{base}/api/tags", bearer=key, timeout=timeout)
            items = (data or {}).get("models") or []
            models = [
                {"id": m.get("name", ""), "label": _label_for(m.get("name", ""))}
                for m in items if isinstance(m, dict) and m.get("name")
            ]
        elif model_source == "openrouter":
            data = _get_json(f"{base}/models", bearer=key, timeout=timeout)
            items = (data or {}).get("data") or []
            models = [
                {"id": m.get("id", ""), "label": m.get("name") or m.get("id", "")}
                for m in items if isinstance(m, dict) and m.get("id")
            ]
        else:
            result["error"] = f"unknown model_source '{model_source}'"
            return result
        result.update(reachable=True, models=models, count=len(models))
        return result
    except urllib.error.HTTPError as e:
        result["error"] = (f"{provider} returned HTTP {e.code}"
                           + (" — check the API key" if e.code in (401, 403) else ""))
        return result
    except Exception as e:  # noqa: BLE001 — best-effort, surface a clean message
        result["error"] = f"{provider} unreachable: {type(e).__name__}: {str(e)[:120]}"
        return result
