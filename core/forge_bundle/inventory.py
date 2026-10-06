"""What this install has, read from each forge's own store (ADR-2229 Phase 3/4).

Two views over the same stores:

* :func:`known` — the ``{kind: {id: versions}}`` map ``validate_bundle`` needs
  for its staleness stage on import.
* :func:`exportable` — what the console's export dialog may offer.

Tool Forge tools carry no version (``ToolSpec`` has none), so in ``known`` a
tool that exists satisfies a requirement on ANY version (:class:`AnyVersion`).
Plugins are not offered for console export: a plugin payload is a wheel the
operator built with Plugin Builder, which lives at an operator-chosen path the
console does not know. The CLI covers that (``--plugin ID@VER:WHEEL``).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class AnyVersion:
    """A version collection that contains every version."""

    def __contains__(self, _item: object) -> bool:
        return True

    def __iter__(self):
        return iter(())

    def __repr__(self) -> str:
        return "AnyVersion()"


def _corvin_home() -> Path:
    from forge import paths as _forge_paths

    return _forge_paths.corvin_home()


def _forged_skills() -> list[dict[str, Any]]:
    out = []
    root = _corvin_home() / "skills_gen"
    if root.is_dir():
        for folder in sorted(root.iterdir()):
            manifest = folder / "skill.json"
            if not manifest.is_file():
                continue
            try:
                data = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            sid, ver = data.get("skill_id"), data.get("version")
            if isinstance(sid, str) and isinstance(ver, str):
                out.append({"id": sid, "version": ver, "description": str(data.get("description", ""))[:200]})
    return out


def _installed_skills() -> dict[str, list[str]]:
    reg = _corvin_home() / "skills_installed" / "skills_registry.json"
    try:
        data = json.loads(reg.read_text())
    except (OSError, ValueError):
        return {}
    return {sid: [e.get("version") for e in entries if isinstance(e, dict)]
            for sid, entries in data.items() if isinstance(entries, list)}


def _tools(tenant_id: str) -> list[dict[str, Any]]:
    from forge.multi_registry import MultiRegistry

    return [{"id": s.name, "description": s.description[:200], "runtime": s.runtime}
            for s in MultiRegistry(tenant_id=tenant_id).list()]


def _layers(tenant_id: str) -> list[dict[str, Any]]:
    from core.orchestration.layer_forge.orchestrator import layer_forge_home
    from core.orchestration.layer_forge.registry import LayerRegistry

    root = layer_forge_home(tenant_id) / "registry"
    if not root.is_dir():
        return []
    return [{"id": m.get("id"), "version": m.get("version"), "status": m.get("status")}
            for m in LayerRegistry(root).list_all()]


def known(tenant_id: str) -> dict[str, dict[str, Any]]:
    skills: dict[str, set[str]] = {}
    for s in _forged_skills():
        skills.setdefault(s["id"], set()).add(s["version"])
    for sid, versions in _installed_skills().items():
        skills.setdefault(sid, set()).update(v for v in versions if isinstance(v, str))
    layers: dict[str, set[str]] = {}
    for m in _layers(tenant_id):
        layers.setdefault(m["id"], set()).add(m["version"])
    return {
        "skill": skills,
        "tool": {t["id"]: AnyVersion() for t in _tools(tenant_id)},
        "layer": layers,
        # Installed plugins are not enumerated here yet; a bundle requiring an
        # external plugin is therefore refused as stale (fail-closed).
        "plugin": {},
    }


def exportable(tenant_id: str) -> dict[str, list[dict[str, Any]]]:
    return {
        "skills": _forged_skills(),
        "tools": _tools(tenant_id),
        "layers": _layers(tenant_id),
        "plugins": [],
    }
