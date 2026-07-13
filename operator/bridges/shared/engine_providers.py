"""ADR-0181 — live model-list fetch per provider.

Given a provider spec (from ``engine_models``), fetch the model IDs the provider
actually offers right now:
  * ``ollama``     → GET {base_url}/api/tags               (local + cloud)
  * ``openrouter`` → GET {base_url}/models                 (public catalogue)
  * ``static``     → no live list (use the curated registry entries)

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
import provider_keys as _provider_keys  # type: ignore  # noqa: E402


def _get_json(url: str, *, bearer: str = "", timeout: float = 8.0) -> Any:
    headers = {"Accept": "application/json"}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed provider URL
        return json.loads(resp.read().decode("utf-8", "replace"))


def _label_for(model_id: str) -> str:
    return model_id


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
    try:
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
