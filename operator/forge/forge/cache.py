"""Determinism cache for forged tools.

A cached entry lets us skip the subprocess+sandbox for tools the operator
declared deterministic. The cache key combines:

  - tool_sha     (the impl source hash — registry-tamper-protected)
  - input_sha    (sha256 of the augmented payload, sans non-deterministic keys)
  - python_tag   (sys.version_info[:3] joined — guards against minor lib drift)

The key NEVER includes ``_artifacts_dir`` (that path differs per run by
design), so repeated identical calls hit the cache.

Cache hits are recorded in the new run's manifest as ``replayed_from``.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any


CACHE_DIRNAME = "cache"


def _python_tag() -> str:
    return ".".join(str(n) for n in sys.version_info[:3])


def _payload_for_key(payload: dict[str, Any]) -> dict[str, Any]:
    """Strip per-run nondeterministic fields from the payload before hashing."""
    return {k: v for k, v in payload.items() if k != "_artifacts_dir"}


def _payload_for_key_with_schema(
    payload: dict[str, Any], input_schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """ADR-0013 §E — honour ``x-cache-key: true`` schema annotations.

    Per-field opt-in cache contribution: when at least one input-schema
    field carries ``"x-cache-key": true``, the cache key is built from
    the subset of payload fields that opted in (plus the existing
    ``_artifacts_dir`` strip). When NO field opted in, the full payload
    contributes — preserving back-compat for tools that don't know
    about the annotation.
    """
    if not isinstance(input_schema, dict):
        return _payload_for_key(payload)
    props = input_schema.get("properties") or {}
    if not isinstance(props, dict):
        return _payload_for_key(payload)
    opt_in: list[str] = [
        name for name, spec in props.items()
        if isinstance(spec, dict) and spec.get("x-cache-key") is True
    ]
    if not opt_in:
        return _payload_for_key(payload)
    return {k: v for k, v in payload.items()
            if k in opt_in and k != "_artifacts_dir"}


def cache_key(
    *,
    tool_sha: str,
    payload: dict[str, Any],
    input_schema: dict[str, Any] | None = None,
) -> str:
    blob = json.dumps(_payload_for_key_with_schema(payload, input_schema),
                      sort_keys=True, default=str)
    h = hashlib.sha256()
    h.update(tool_sha.encode())
    h.update(b"\0")
    h.update(blob.encode())
    h.update(b"\0")
    h.update(_python_tag().encode())
    return h.hexdigest()[:32]


def _cache_path(workspace_root: Path, key: str) -> Path:
    return Path(workspace_root) / CACHE_DIRNAME / f"{key}.json"


def lookup(workspace_root: Path, key: str) -> dict[str, Any] | None:
    p = _cache_path(workspace_root, key)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except json.JSONDecodeError:
        return None


def store(
    workspace_root: Path,
    key: str,
    *,
    envelope: dict[str, Any],
    run_id: str,
) -> None:
    p = _cache_path(workspace_root, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "key": key,
        "python_tag": _python_tag(),
        "run_id": run_id,
        "envelope": envelope,
    }
    fd, tmp = tempfile.mkstemp(prefix=p.name + ".", dir=str(p.parent))
    try:
        with os.fdopen(fd, "w") as fh:
            fh.write(json.dumps(record, indent=2))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, p)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


def invalidate(workspace_root: Path, key: str | None = None) -> int:
    """Remove cached entries. Returns count removed. ``None`` clears all."""
    cdir = Path(workspace_root) / CACHE_DIRNAME
    if not cdir.exists():
        return 0
    n = 0
    if key:
        p = cdir / f"{key}.json"
        if p.exists():
            p.unlink()
            n += 1
    else:
        for p in cdir.glob("*.json"):
            p.unlink()
            n += 1
    return n
