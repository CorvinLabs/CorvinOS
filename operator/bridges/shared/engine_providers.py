"""ADR-0181 — live model-list fetch per provider.

Given a provider spec (from ``engine_models``), fetch the model IDs the provider
actually offers right now:
  * ``anthropic``  → GET {base_url}/v1/models              (paginated, cached)
  * ``bedrock``    → GET /foundation-models + /inference-profiles (SigV4-signed)
  * ``ollama``     → GET {base_url}/api/tags               (local + cloud)
  * ``openrouter`` → GET {base_url}/models                 (public catalogue)
  * ``openai``     → GET {base_url}/models                 (requires an API key)
  * ``static``     → no live list (use the curated registry entries)

Two sources WRITE what they find to ``model_catalog`` so
``engine_models.load_registry()`` can merge it into the curated picker —
``anthropic`` and ``bedrock``. That merge is the whole point: a fetch whose
result nobody stores changes nothing the operator can see. ``anthropic`` is
additionally the only PAGINATED source (``has_more``/``last_id``) and the only
one with a benign no-credential case — a Claude Code subscription login exposes
no API key, so a keyless call returns an explanation and does not egress.
``bedrock`` is the only source that needs no ``credential_env`` at all: it
authenticates through the AWS credential chain, not the L16 key vault.

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
import aws_sigv4 as _aws_sigv4  # type: ignore  # noqa: E402
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


def _bedrock_host(region: str) -> str:
    return f"bedrock.{region}.amazonaws.com"


def _bedrock_get(
    *, credentials: Any, region: str, path: str, query: dict[str, str], timeout: float
) -> Any:
    host = _bedrock_host(region)
    headers = _aws_sigv4.signed_get_headers(
        credentials=credentials, host=host, region=region,
        service="bedrock", canonical_uri=path, query=query,
    )
    url = f"https://{host}{path}"
    canonical = _aws_sigv4.canonical_query(query)
    if canonical:
        url = f"{url}?{canonical}"
    req = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed AWS endpoint
        return json.loads(resp.read().decode("utf-8", "replace"))


def _fetch_bedrock(result: dict, *, timeout: float) -> dict:
    """List what THIS AWS account can actually invoke, via two Bedrock calls.

    Both are needed and neither is redundant:

    * ``ListFoundationModels`` returns base ids (``anthropic.claude-sonnet-5-v1:0``).
    * ``ListInferenceProfiles`` returns the cross-region ids (``us.anthropic.
      claude-sonnet-5``) — and those are what Claude Code actually passes as
      ``ANTHROPIC_MODEL`` on a Bedrock install, so a picker built from foundation
      models alone offers ids the operator cannot select.

    A profile listing that fails does not sink the foundation-model list (and vice
    versa): partial truth beats an empty picker, and ``result["error"]`` names
    what was missed. Never raises.
    """
    region = _aws_sigv4.resolve_region()
    if not region:
        result["error"] = (
            "no AWS region configured (AWS_REGION / AWS_DEFAULT_REGION or the "
            "profile's `region`) — cannot address a Bedrock endpoint"
        )
        return result

    credentials, reason = _aws_sigv4.resolve_credentials()
    if credentials is None:
        result["error"] = reason or "no AWS credentials configured on this host"
        return result

    models: list[dict] = []
    partial_errors: list[str] = []
    # A session token cached in ~/.aws/credentials outlives its validity: the file
    # still parses, so the chain hands it over and only AWS knows it is dead. On
    # that specific rejection, re-run credential_process ONCE and retry.
    refreshed = False

    def _call(path: str, query: dict[str, str]) -> Any:
        nonlocal credentials, refreshed
        try:
            return _bedrock_get(credentials=credentials, region=region, path=path,
                                query=query, timeout=timeout)
        except urllib.error.HTTPError as exc:
            if exc.code not in (401, 403) or refreshed or not _aws_sigv4.has_credential_process():
                raise
            refreshed = True
            fresh, refresh_reason = _aws_sigv4.resolve_credentials(force_refresh=True)
            if fresh is None:
                raise RuntimeError(refresh_reason or "credential refresh produced nothing") from exc
            credentials = fresh
            return _bedrock_get(credentials=credentials, region=region, path=path,
                                query=query, timeout=timeout)

    try:
        data = _call("/foundation-models", {"byOutputModality": "TEXT"}) or {}
        for summary in data.get("modelSummaries") or []:
            if not isinstance(summary, dict) or not summary.get("modelId"):
                continue
            model_id = summary["modelId"]
            vendor = summary.get("providerName") or ""
            name = summary.get("modelName") or model_id
            models.append({
                "id": model_id,
                "label": f"{vendor} {name}".strip() if vendor else name,
                "bedrock_kind": "foundation_model",
            })
    except Exception as exc:  # noqa: BLE001
        partial_errors.append(f"ListFoundationModels: {_brief(exc)}")

    try:
        next_token = ""
        for _ in range(_MAX_MODEL_PAGES):
            query = {"maxResults": "100"}
            if next_token:
                query["nextToken"] = next_token
            data = _call("/inference-profiles", query) or {}
            for summary in data.get("inferenceProfileSummaries") or []:
                if not isinstance(summary, dict) or not summary.get("inferenceProfileId"):
                    continue
                models.append({
                    "id": summary["inferenceProfileId"],
                    "label": summary.get("inferenceProfileName")
                             or summary["inferenceProfileId"],
                    "bedrock_kind": "inference_profile",
                })
            next_token = data.get("nextToken") or ""
            if not next_token:
                break
    except Exception as exc:  # noqa: BLE001
        partial_errors.append(f"ListInferenceProfiles: {_brief(exc)}")

    if not models:
        result["error"] = "; ".join(partial_errors) or "Bedrock returned no models"
        return result

    seen: set[str] = set()
    unique = [m for m in models if not (m["id"] in seen or seen.add(m["id"]))]
    result.update(reachable=True, models=unique, count=len(unique))
    result["region"] = region
    result["credential_source"] = credentials.source
    if partial_errors:
        result["error"] = "; ".join(partial_errors)
    try:
        result["cached"] = bool(_model_catalog.store_models("bedrock", unique))
    except Exception:  # noqa: BLE001 — a cache write must not cost the response
        result["cached"] = False
    return result


def _brief(exc: BaseException) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"HTTP {exc.code}"
    return f"{type(exc).__name__}: {str(exc)[:120]}"


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
        # The wording matters: this response carries models=[]. Saying "showing
        # the curated model list" claimed a list THIS response does not contain
        # (merging the curated list is the caller's job, and neither live caller
        # does it) — which reads as a bug in the picker rather than an absent key.
        result["error"] = (
            f"no {credential_env or 'ANTHROPIC_API_KEY'} configured, so no live "
            f"model list could be fetched. Add an API key under Settings → API "
            f"Keys to see Anthropic's live models."
        )
        return result

    try:
        if model_source == "anthropic":
            return _fetch_anthropic(result, base=base, key=key, timeout=timeout)
        if model_source == "bedrock":
            # No credential_env: Bedrock authenticates with the AWS credential
            # chain (env / profile / credential_process), not an API key in the
            # L16 vault, so there is nothing for provider_keys to resolve.
            return _fetch_bedrock(result, timeout=timeout)
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
        elif model_source == "openai":
            if not key:
                result["error"] = (
                    f"no {credential_env or 'OPENAI_API_KEY'} configured — add an API "
                    f"key under Settings → API Keys to see OpenAI's live model list."
                )
                return result
            data = _get_json(f"{base}/models", bearer=key, timeout=timeout)
            items = (data or {}).get("data") or []
            models = [
                {"id": m.get("id", ""), "label": m.get("id", "")}
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
