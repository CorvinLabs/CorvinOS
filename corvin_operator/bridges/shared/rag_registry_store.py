"""RAG provider registry layout — the ONE owner of ``<tenant>/global/rag/``.

Layout (the one the orchestrator has always read)::

    <registry_dir>/
        registry.json            {"version": 1, "providers": [{"id", "name", ...}]}
        manifests/<id>.yaml      the provider manifest

Before this module three writers (Add Provider, Hub import, the manifest
importer) wrote a flat ``<registry_dir>/<id>.yaml`` while the orchestrator read
``registry.json`` + ``manifests/`` and the licence gate counted flat files, so a
provider created in the wizard was never visible and the "providers used"
counter and the badge disagreed. Every reader and writer now goes through here.

Legacy flat ``<id>.yaml`` files (written by older builds) are still listed and
are moved into ``manifests/`` by :func:`migrate_legacy` the first time a writer
or the orchestrator touches the directory. Nothing is ever deleted by the
migration: a flat file whose id already has a manifest is left where it is.

All writes are atomic (temp file + ``os.replace``) and serialised by a process
lock, so two concurrent creates cannot drop each other's registry entry.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import threading
import time
import types
from pathlib import Path
from typing import Any, Optional

PROVIDER_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")

def _process_lock() -> "threading.RLock":
    # The console imports this module under two names (``shared.`` and
    # ``bridges.shared.``); one lock per process, not one per import name.
    holder = sys.modules.get("_corvin_rag_registry_lock")
    if holder is None:
        holder = types.ModuleType("_corvin_rag_registry_lock")
        holder.lock = threading.RLock()  # type: ignore[attr-defined]
        sys.modules["_corvin_rag_registry_lock"] = holder
    return holder.lock  # type: ignore[attr-defined]


_LOCK = _process_lock()
_REGISTRY_FILE = "registry.json"
_MANIFESTS_DIR = "manifests"


def manifests_dir(registry_dir: Path) -> Path:
    return Path(registry_dir) / _MANIFESTS_DIR


def registry_file(registry_dir: Path) -> Path:
    return Path(registry_dir) / _REGISTRY_FILE


def manifest_path(registry_dir: Path, provider_id: str) -> Path:
    """Where the manifest of *provider_id* lives (canonical location).

    Raises ValueError for an id that is not a safe filename stem.
    """
    if not PROVIDER_ID_RE.match(str(provider_id)):
        raise ValueError(f"invalid provider_id: {provider_id!r}")
    return manifests_dir(registry_dir) / f"{provider_id}.yaml"


def find_manifest(registry_dir: Path, provider_id: str) -> Optional[Path]:
    """Existing manifest file for *provider_id* (canonical, else legacy flat)."""
    canonical = manifest_path(registry_dir, provider_id)
    if canonical.is_file():
        return canonical
    legacy = Path(registry_dir) / f"{provider_id}.yaml"
    if legacy.is_file():
        return legacy
    return None


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_index(registry_dir: Path) -> dict[str, Any]:
    path = registry_file(registry_dir)
    if not path.is_file():
        return {"version": 1, "providers": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"version": 1, "providers": []}
    if not isinstance(data, dict) or not isinstance(data.get("providers"), list):
        return {"version": 1, "providers": []}
    data["providers"] = [p for p in data["providers"] if isinstance(p, dict) and p.get("id")]
    return data


def _write_index(registry_dir: Path, data: dict[str, Any]) -> None:
    data["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _atomic_write(registry_file(registry_dir), json.dumps(data, indent=2))


def provider_ids(registry_dir: Path) -> list[str]:
    """Sorted ids of every provider that has a manifest file (canonical or legacy).

    A ``registry.json`` entry whose manifest is gone is NOT a provider, and a
    manifest with no index entry IS one (the file is the source of truth).
    """
    d = Path(registry_dir)
    ids: set[str] = set()
    md = manifests_dir(d)
    if md.is_dir():
        ids.update(p.stem for p in md.glob("*.yaml") if PROVIDER_ID_RE.match(p.stem))
    if d.is_dir():
        ids.update(p.stem for p in d.glob("*.yaml") if PROVIDER_ID_RE.match(p.stem))
    return sorted(ids)


def count(registry_dir: Path) -> int:
    """Number of registered providers — the single counter for badge and licence gate."""
    return len(provider_ids(registry_dir))


def _index_entry(provider_id: str, manifest_yaml: str, previous: Optional[dict]) -> dict[str, Any]:
    name, version = provider_id, "1.0"
    try:
        import yaml

        meta = (yaml.safe_load(manifest_yaml) or {}).get("metadata") or {}
        name = str(meta.get("name") or provider_id)
        version = str(meta.get("version") or version)
    except Exception:  # noqa: BLE001 - the index is a convenience, the file is the truth
        pass
    entry = dict(previous or {})
    entry.update({
        "id": provider_id,
        "name": name,
        "version": version,
        "status": "active",
    })
    entry.setdefault("registered_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    entry.setdefault("query_stats", {"total_queries": 0, "queries_today": 0,
                                     "average_latency_ms": 0, "day": ""})
    return entry


def write_manifest(registry_dir: Path, provider_id: str, manifest_yaml: str) -> Path:
    """Write the manifest and its index entry. Returns the manifest path."""
    path = manifest_path(registry_dir, provider_id)
    with _LOCK:
        migrate_legacy(registry_dir)
        _atomic_write(path, manifest_yaml)
        index = _read_index(registry_dir)
        previous = next((p for p in index["providers"] if p["id"] == provider_id), None)
        index["providers"] = [p for p in index["providers"] if p["id"] != provider_id]
        index["providers"].append(_index_entry(provider_id, manifest_yaml, previous))
        index["providers"].sort(key=lambda p: p["id"])
        _write_index(registry_dir, index)
    return path


def remove(registry_dir: Path, provider_id: str) -> bool:
    """Delete a provider (manifest + index entry). True when something was removed."""
    removed = False
    with _LOCK:
        for path in (manifest_path(registry_dir, provider_id),
                     Path(registry_dir) / f"{provider_id}.yaml"):
            if path.is_file():
                path.unlink()
                removed = True
        index = _read_index(registry_dir)
        kept = [p for p in index["providers"] if p["id"] != provider_id]
        if len(kept) != len(index["providers"]):
            index["providers"] = kept
            _write_index(registry_dir, index)
            removed = True
    return removed


def migrate_legacy(registry_dir: Path) -> list[str]:
    """Move legacy flat ``<id>.yaml`` files into ``manifests/`` and index them.

    Idempotent. A flat file whose id already has a canonical manifest is left in
    place (never overwritten, never deleted). Returns the ids that were moved.
    """
    d = Path(registry_dir)
    if not d.is_dir():
        return []
    moved: list[str] = []
    with _LOCK:
        for legacy in sorted(d.glob("*.yaml")):
            pid = legacy.stem
            if not PROVIDER_ID_RE.match(pid):
                continue
            target = manifest_path(d, pid)
            if target.exists():
                continue
            text = legacy.read_text(encoding="utf-8")
            _atomic_write(target, text)
            legacy.unlink()
            index = _read_index(d)
            previous = next((p for p in index["providers"] if p["id"] == pid), None)
            index["providers"] = [p for p in index["providers"] if p["id"] != pid]
            index["providers"].append(_index_entry(pid, text, previous))
            index["providers"].sort(key=lambda p: p["id"])
            _write_index(d, index)
            moved.append(pid)
    return moved


def record_query(registry_dir: Path, provider_ids_: list[str], latency_ms: int) -> None:
    """Count one executed query against each answering provider (registry.json).

    ``queries_today`` resets when the UTC day changes. Best-effort: a failure
    here must never fail the query.
    """
    if not provider_ids_:
        return
    today = time.strftime("%Y-%m-%d", time.gmtime())
    try:
        with _LOCK:
            index = _read_index(registry_dir)
            changed = False
            known = {entry["id"] for entry in index["providers"]}
            for pid in provider_ids_:
                # A manifest that was never indexed (written by an older build) is
                # still a provider: give it an index entry rather than drop the count.
                if pid not in known and manifest_path(registry_dir, pid).is_file():
                    index["providers"].append(_index_entry(pid, "", None))
                    changed = True
            for entry in index["providers"]:
                if entry["id"] not in provider_ids_:
                    continue
                qs = entry.setdefault("query_stats", {})
                if qs.get("day") != today:
                    qs["day"] = today
                    qs["queries_today"] = 0
                total = int(qs.get("total_queries", 0))
                avg = float(qs.get("average_latency_ms", 0))
                qs["total_queries"] = total + 1
                qs["queries_today"] = int(qs.get("queries_today", 0)) + 1
                qs["average_latency_ms"] = int((avg * total + latency_ms) / (total + 1))
                changed = True
            if changed:
                _write_index(registry_dir, index)
    except Exception:  # noqa: BLE001
        pass


def query_stats(registry_dir: Path) -> dict[str, dict[str, Any]]:
    """``{provider_id: {total_queries, queries_today, average_latency_ms}}`` (today-aware)."""
    today = time.strftime("%Y-%m-%d", time.gmtime())
    out: dict[str, dict[str, Any]] = {}
    for entry in _read_index(registry_dir)["providers"]:
        qs = entry.get("query_stats") or {}
        out[entry["id"]] = {
            "total_queries": int(qs.get("total_queries", 0)),
            "queries_today": int(qs.get("queries_today", 0)) if qs.get("day") == today else 0,
            "average_latency_ms": int(qs.get("average_latency_ms", 0)),
        }
    return out
