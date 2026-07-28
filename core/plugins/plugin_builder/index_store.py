"""Tenant-scoped index of scaffolds the Plugin-Builder has written (ADR-0253).

Purely a record of "what got generated, when, where" so the Console's Plugins
page can show a Plugin-Builder author their own scaffolds alongside installed
plugins — see ``docs/claude-ref/layer-plugins.md``. This module writes
metadata only; it never touches ``corvin_plugins``' registry/loader, so
ADR-0244's "the Builder emits, never loads" invariant holds even for this
bookkeeping (a scaffold recorded here is NOT an installed plugin — the
Console page must render the two lists as visibly distinct).

Same tenant-scoped-JSON-side-file shape as ``feature_flags.py``'s
``features.json`` overlay: mtime is irrelevant here (this is an append-only
log, not a config snapshot to cache), so it's a plain read-modify-write
guarded by a process lock, atomic-renamed on write.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .generators.scaffold import ScaffoldResult
from .models import PluginIdea

log = logging.getLogger("corvin.plugin_builder.index_store")

_INDEX_NAME = "plugin_builder_index.json"
#: Same order of magnitude as session_store.MAX_SESSIONS — bounds the index
#: so a long-lived tenant can't grow this file forever; oldest entries drop.
MAX_ENTRIES = 500

_lock = threading.Lock()


@dataclass(frozen=True)
class ScaffoldRecord:
    plugin_id: str
    display_name: str
    kind: str
    tier: str
    plugin_type: str | None
    path: str
    created_at: float


def _index_path(tenant_id: str) -> Path:
    # `forge` lives under operator/ and only reaches sys.path through the host's
    # bootstrap (corvin_console._operator_bootstrap in a wheel, a path insert in
    # a checkout). Importing it at MODULE level made `import plugin_builder.turn`
    # — the shared, transport-agnostic entry point both the Console and the
    # bridges drive — fail with ModuleNotFoundError anywhere that bootstrap had
    # not run yet, and made this package's own 7 test modules uncollectable,
    # which is why they had never been added to a CI job. Deferred to call time,
    # exactly like `turn.output_dir` already did.
    from forge import paths as _forge_paths  # noqa: PLC0415
    return _forge_paths.tenant_global_dir(tenant_id) / _INDEX_NAME


def _read(tenant_id: str) -> list[dict[str, Any]]:
    try:
        raw = json.loads(_index_path(tenant_id).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — missing/corrupt index → empty, never raise
        return []
    return raw if isinstance(raw, list) else []


def _write(tenant_id: str, records: list[dict[str, Any]]) -> None:
    path = _index_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, indent=2, sort_keys=True), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def record(tenant_id: str, idea: PluginIdea, result: ScaffoldResult) -> None:
    """Append one entry for a just-written scaffold.

    Best-effort by design: called right after :func:`~.generators.scaffold.
    write_artifacts` already wrote real files to disk, so a failure HERE must
    never be raised back into the interview turn — callers should wrap this
    in a try/except and log, not fail the confirm step over a listing update.
    """
    classification = result.classification
    with _lock:
        records = _read(tenant_id)
        records.append(asdict(ScaffoldRecord(
            plugin_id=result.plugin_id,
            display_name=idea.plugin_name,
            kind=classification.kind.value,
            tier=classification.tier.value,
            plugin_type=classification.plugin_type,
            path=str(result.dest),
            created_at=time.time(),
        )))
        if len(records) > MAX_ENTRIES:
            records = records[-MAX_ENTRIES:]
        _write(tenant_id, records)


def list_scaffolds(tenant_id: str) -> list[dict[str, Any]]:
    """All recorded scaffolds for this tenant, oldest first."""
    with _lock:
        return _read(tenant_id)


__all__ = ["ScaffoldRecord", "record", "list_scaffolds", "MAX_ENTRIES"]
