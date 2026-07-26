"""Live model catalog cache — keeps the console's model pickers current.

The engine model registry (``engine_model_registry.yaml``, ADR-0119) ships a
CURATED model list per engine. A curated list is a snapshot: the day Anthropic
ships a new model (Opus 5, …) every install is stale until the next CorvinOS
release lands a new YAML.

This module is the second, self-updating source: providers whose
``model_source`` supports a live catalogue (currently ``anthropic`` — see
``engine_providers.fetch_models``) have their model list fetched, cached on
disk, and merged into the registry by ``engine_models.load_registry()``. The
curated list stays the fallback, so an install with no API key (a Claude Code
subscription login never exposes one) keeps a working, if snapshot-aged, list.

Design constraints:

* **No network in this module.** Fetching is network egress and must pass the
  L35 gate, so it happens in the console route / ``engine_providers`` only.
  This module reads and writes the cache file, nothing else. ``load_registry()``
  runs on every engine spawn — it must never block on HTTP.
* **Last-good wins.** A failed refresh never clears the cache; an unreadable
  cache never clears the curated list.
* **No PII, no secrets.** The file holds model ids and display names only.

Cache file: ``<corvin_home>/global/model_catalog.json`` (mode 0600, atomic
replace, mtime-keyed in-process cache).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# Refresh cadence for a background/opportunistic refresh. Model launches are
# rare; a half-day cadence keeps the list fresh without hammering the API.
DEFAULT_TTL_SECONDS: float = 12 * 60 * 60

_CACHE_VERSION = 1

# Hard cap so a provider that returns thousands of ids can never blow up the
# registry payload the console renders.
_MAX_MODELS_PER_PROVIDER = 200


def catalog_path() -> Path:
    home_env = os.environ.get("CORVIN_HOME", "")
    corvin_home = Path(home_env).expanduser() if home_env else Path.home() / ".corvin"
    return corvin_home / "global" / "model_catalog.json"


# ---------------------------------------------------------------------------
# Read side (hot path — mtime-keyed, never raises)
# ---------------------------------------------------------------------------

_cache: dict[str, Any] | None = None
_cache_key: tuple[str, float, int] | None = None


def load_catalog(force_reload: bool = False) -> dict[str, Any]:
    """Return the parsed catalog ``{"version": int, "providers": {...}}``.

    Re-reads the file only when its (path, mtime, size) changed, so the engine
    spawn path can call this per turn without paying a disk read each time.
    Any read/parse failure yields an empty catalog — the caller falls back to
    the curated registry list."""
    global _cache, _cache_key  # noqa: PLW0603
    p = catalog_path()
    try:
        st = p.stat()
        key = (str(p), st.st_mtime, st.st_size)
    except OSError:
        _cache, _cache_key = {}, None
        return {}
    if not force_reload and _cache is not None and _cache_key == key:
        return _cache
    try:
        raw = json.loads(p.read_text("utf-8"))
        if not isinstance(raw, dict) or not isinstance(raw.get("providers"), dict):
            raw = {}
    except Exception:  # noqa: BLE001 — a corrupt cache must not break the registry
        raw = {}
    _cache, _cache_key = raw, key
    return raw


def catalog_models(provider: str) -> list[dict[str, str]]:
    """Cached ``[{"id","label"}, …]`` for a provider (empty when unknown)."""
    entry = (load_catalog().get("providers") or {}).get(provider)
    if not isinstance(entry, dict):
        return []
    out: list[dict[str, str]] = []
    for m in entry.get("models") or []:
        if isinstance(m, dict) and isinstance(m.get("id"), str) and m["id"].strip():
            mid = m["id"].strip()
            label = str(m.get("label") or "").strip() or mid
            out.append({"id": mid, "label": label})
    return out[:_MAX_MODELS_PER_PROVIDER]


def fetched_at(provider: str) -> float | None:
    """Unix timestamp of the last successful refresh, or None."""
    entry = (load_catalog().get("providers") or {}).get(provider)
    if not isinstance(entry, dict):
        return None
    try:
        return float(entry.get("fetched_at"))
    except (TypeError, ValueError):
        return None


def is_stale(provider: str, ttl_seconds: float = DEFAULT_TTL_SECONDS) -> bool:
    """True when the provider has never been fetched or the entry aged out.

    A clock that jumped backwards (``fetched_at`` in the future) counts as
    stale rather than pinning the entry as fresh forever."""
    ts = fetched_at(provider)
    if ts is None:
        return True
    age = time.time() - ts
    return age >= ttl_seconds or age < 0


# ---------------------------------------------------------------------------
# Write side (refresh path only)
# ---------------------------------------------------------------------------

def store_models(provider: str, models: list[dict[str, Any]]) -> bool:
    """Persist a freshly fetched model list for ``provider``.

    An EMPTY list is rejected: a provider answering 200-with-no-models (auth
    scoped to zero models, an upstream hiccup) must not wipe a good cache and
    leave the picker with the curated list only. Returns True when written."""
    clean: list[dict[str, str]] = []
    seen: set[str] = set()
    for m in models or []:
        if not isinstance(m, dict):
            continue
        mid = str(m.get("id") or "").strip()
        if not mid or mid in seen:
            continue
        seen.add(mid)
        clean.append({"id": mid, "label": str(m.get("label") or "").strip() or mid})
        if len(clean) >= _MAX_MODELS_PER_PROVIDER:
            break
    if not clean:
        return False

    p = catalog_path()
    current = load_catalog(force_reload=True)
    providers = dict(current.get("providers") or {})
    providers[provider] = {"fetched_at": time.time(), "models": clean}
    payload = {"version": _CACHE_VERSION, "providers": providers}
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, sort_keys=True, indent=2), "utf-8")
        tmp.chmod(0o600)
        tmp.replace(p)
    except OSError:
        return False
    load_catalog(force_reload=True)
    return True
