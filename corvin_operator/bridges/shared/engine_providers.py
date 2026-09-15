"""ADR-0181 — live model-list fetch per provider.

Given a provider spec (from ``engine_models``), fetch the model IDs the provider
actually offers right now:
  * ``anthropic``  → GET {base_url}/v1/models              (paginated, cached)
  * ``ollama``     → GET {base_url}/api/tags               (local + cloud)
  * ``openrouter`` → GET {base_url}/models                 (public catalogue)
  * ``openai``     → GET {base_url}/models                 (requires an API key)
  * ``static``     → no live list (use the curated registry entries)
  * ``bedrock``    → ListFoundationModels + ListInferenceProfiles (SigV4)
  * ``vertex``     → publishers/anthropic/models (OAuth2 bearer)
  * ``foundry``    → deployments list (Azure AD bearer)

The last three are ADR-0759 PLATFORM sources. They differ from the api_key
sources in what "no credential" means: an api_key source is unreachable without
its one env var, whereas a platform source may be authenticated by an instance
role, an AWS profile or ``gcloud`` ADC with no env var set at all. So they never
report "no API key configured"; they attempt the real signed call and report
what the platform answered — including "no resolvable credentials", which names
the chain that was tried instead of a single missing variable.

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
import os
import subprocess
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


# ---------------------------------------------------------------------------
# ADR-0759 — platform sources (Bedrock / Vertex / Foundry)
# ---------------------------------------------------------------------------

#: Anthropic model families on Bedrock/Vertex. Used only to FILTER a platform
#: catalogue that also lists every other vendor — never to invent an id.
_ANTHROPIC_MARKER = "anthropic"


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
        # Not a failure of this source, and the console renders the two
        # differently: a host that never configured AWS is UNUSED here, not
        # down. Same flag the keyless anthropic path sets (ADR-0759).
        result["credential_absent"] = True
        return result

    credentials, reason = _aws_sigv4.resolve_credentials()
    if credentials is None:
        result["error"] = reason or "no AWS credentials configured on this host"
        result["credential_absent"] = True
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


def _cli_token(argv: list[str]) -> str:
    """Access token from a cloud CLI, or "" — the CLI is how an operator on a
    workstation is actually logged in, and shelling out to it is the only way to
    honour that without adding an SDK dependency."""
    try:
        out = subprocess.run(argv, capture_output=True, text=True, timeout=15)  # noqa: S603
    except Exception:  # noqa: BLE001 — CLI absent
        return ""
    if out.returncode != 0:
        return ""
    return (out.stdout or "").strip()


def _fetch_vertex(result: dict, *, region: str, project: str, timeout: float) -> dict:
    """Anthropic publisher models on Vertex. Token via ``gcloud`` ADC."""
    if not project:
        result["error"] = ("no Vertex project — set ANTHROPIC_VERTEX_PROJECT_ID")
        result["credential_absent"] = True
        return result
    token = _cli_token(["gcloud", "auth", "print-access-token"])
    if not token:
        result["error"] = ("no Google access token (gcloud not installed or not "
                           "logged in — run: gcloud auth application-default login)")
        result["credential_absent"] = True
        return result
    host = f"{region}-aiplatform.googleapis.com"
    url = (f"https://{host}/v1/projects/{project}/locations/{region}"
           f"/publishers/anthropic/models")
    try:
        data = _get_json(url, bearer=token, timeout=timeout) or {}
    except urllib.error.HTTPError as exc:
        result["error"] = f"vertex returned HTTP {exc.code}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"vertex unreachable: {type(exc).__name__}"
        return result
    models = []
    for item in data.get("publisherModels") or data.get("models") or []:
        if not isinstance(item, dict):
            continue
        # "publishers/anthropic/models/claude-sonnet-5" → "claude-sonnet-5"
        mid = str(item.get("name") or "").rsplit("/", 1)[-1]
        if mid:
            models.append({"id": mid, "label": item.get("displayName") or mid})
    result.update(reachable=True, models=models, count=len(models))
    result["region"] = region
    result["credential_source"] = "gcloud ADC"
    try:
        result["cached"] = bool(_model_catalog.store_models("vertex", models))
    except Exception:  # noqa: BLE001
        result["cached"] = False
    return result


def _fetch_foundry(result: dict, *, resource: str, timeout: float) -> dict:
    """Claude deployments on a Microsoft Foundry resource. Token via ``az``."""
    if not resource:
        result["error"] = "no Foundry resource — set ANTHROPIC_FOUNDRY_RESOURCE"
        result["credential_absent"] = True
        return result
    raw = _cli_token(["az", "account", "get-access-token", "--resource",
                      "https://ai.azure.com", "-o", "json"])
    token = ""
    if raw:
        try:
            token = (json.loads(raw) or {}).get("accessToken") or ""
        except Exception:  # noqa: BLE001
            token = ""
    if not token:
        result["error"] = ("no Azure access token (az CLI not installed or not "
                           "logged in — run: az login)")
        result["credential_absent"] = True
        return result
    host = f"{resource}.services.ai.azure.com"
    try:
        data = _get_json(f"https://{host}/openai/deployments?api-version=2024-10-21",
                         bearer=token, timeout=timeout) or {}
    except urllib.error.HTTPError as exc:
        result["error"] = f"foundry returned HTTP {exc.code}"
        return result
    except Exception as exc:  # noqa: BLE001
        result["error"] = f"foundry unreachable: {type(exc).__name__}"
        return result
    models = []
    for item in data.get("data") or []:
        if isinstance(item, dict) and item.get("id"):
            models.append({"id": item["id"], "label": item.get("model") or item["id"]})
    result.update(reachable=True, models=models, count=len(models))
    result["credential_source"] = "az CLI"
    return result


def fetch_models(
    provider: str,
    *,
    base_url: str,
    model_source: str,
    credential_env: str = "",
    timeout: float = 8.0,
    platform_env: dict | None = None,
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

    if model_source in ("bedrock", "vertex", "foundry"):
        pe = platform_env or {}

        def _first(names_key: str, default: str = "") -> str:
            for name in pe.get(names_key) or []:
                val = (os.environ.get(str(name)) or "").strip()
                if val:
                    return val
            return default

        region = _first("region_env", str(pe.get("default_region") or ""))
        try:
            if model_source == "bedrock":
                return _fetch_bedrock(result, timeout=timeout)
            if model_source == "vertex":
                return _fetch_vertex(result, region=region,
                                     project=_first("project_env"), timeout=timeout)
            return _fetch_foundry(result, resource=_first("resource_env"), timeout=timeout)
        except Exception as exc:  # noqa: BLE001 — contract: never raises
            result["error"] = f"{provider}: {type(exc).__name__}: {str(exc)[:120]}"
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
        # A FACT about this host, not a display decision: the console needs to
        # tell "never had a key here" apart from "the key is wrong", because the
        # first is the normal state of every subscription/Bedrock login and must
        # not be rendered as a broken source.
        result["credential_absent"] = True
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
        elif model_source == "openai":
            if not key:
                result["error"] = (
                    f"no {credential_env or 'OPENAI_API_KEY'} configured — add an API "
                    f"key under Settings → API Keys to see OpenAI's live model list."
                )
                result["credential_absent"] = True
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
