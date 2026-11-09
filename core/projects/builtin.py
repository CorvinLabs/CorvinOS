"""The built-in "Corvin" project (CONCEPT-0129): Corvin itself is a project on every install.

It stands for the tenant's OWN knowledge base (the operator's bound repository or the tenant's ``<tenant_home>/knowledge``), so the
structure, the change view, the explorer, the board and the graph - everything a project has - work for Corvin's own work too. It has no
``project.yaml`` and no registry entry: it is synthesised on every read, can never be removed, renamed, moved, repaired or have its
protection changed (always ``internal`` and read-only), and its id is reserved so no user project can take it.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

ID = "corvin"
TITLE = "Corvin"
FEATURES = {"core": True, "kb": True, "tasks": True, "graph": True}


def is_builtin(project_id: Optional[str]) -> bool:
    return project_id == ID


def repo(tenant_id: str) -> Optional[Path]:
    """The tenant's own knowledge base, or None while none exists yet (no plugin / not created). Never another tenant's."""
    try:
        from corvin_console import kb_projection  # noqa: PLC0415
        r = kb_projection.kb_repo(tenant_id)
        return Path(r) if r is not None and kb_projection._is_kb(r) else None
    except Exception:  # noqa: BLE001
        return None


def entry(tenant_id: str) -> dict[str, Any]:
    """A registry-shaped entry, so every reader that resolves a project by id works unchanged."""
    r = repo(tenant_id)
    return {"id": ID, "path": str(r) if r else "", "template": "builtin@1", "added_at": None, "builtin": True}


def manifest(tenant_id: str) -> dict[str, Any]:
    return {"schema": 1, "id": ID, "title": TITLE, "tenant": tenant_id, "template": "builtin@1", "docs_mode": "plain",
            "classification": "internal", "assistant_access": "read", "features": dict(FEATURES)}
