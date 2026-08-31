"""Personas — bundled + user-override enumeration.

Bundle: ``<repo>/operator/cowork/personas/*.json``
User:   ``<corvin_home>/global/cowork/personas/*.json`` (or the
        legacy ``~/.config/claude-cowork/personas/`` symlink).

Returns one entry per persona with the curated metadata fields.
The full body (description, append_system, mcp_servers, etc.) is
served on the per-persona detail endpoint (Phase C).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

import re
from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
_REPO = _bootstrap._REPO


router = APIRouter()


def _resolve_bundle_dir() -> Path:
    """Locate the shipped bundle personas in source-tree OR wheel layout.

    Source checkout: ``<repo>/operator/cowork/personas``. In a wheel install the
    repo-relative path resolves into site-packages where no ``operator/`` exists,
    so fall back to the vendored copy under ``corvin_console/_vendor/operator``
    (path-audit 2026-06-25 #MED6 — the same gap ``landing.py`` already closed).
    Without this fallback a fresh ``pip install`` showed an EMPTY persona list —
    the bundle personas ship with the wheel but were looked up at the wrong path.
    """
    repo = _REPO / "operator" / "cowork" / "personas"
    if repo.is_dir():
        return repo
    try:
        from .._operator_bootstrap import vendor_operator_root
        vroot = vendor_operator_root()
    except Exception:  # noqa: BLE001
        vroot = None
    if vroot is not None:
        vendored = vroot / "cowork" / "personas"
        if vendored.is_dir():
            return vendored
    return repo


_BUNDLE_DIR = _resolve_bundle_dir()


def _load_persona(path: Path) -> dict[str, Any] | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Disabled-persona registry (per-tenant) ────────────────────────────
# Deactivating a persona must work uniformly for BOTH bundle (read-only,
# shipped) and user personas without copying or mutating the bundle file, so we
# keep a tiny per-tenant registry of disabled NAMES rather than an ``enabled``
# field on the JSON. The console owns writes; the cowork resolver reads the same
# file so a disabled persona is actually dropped from runtime auto-routing
# (resolver.list_available), not just hidden in the UI. Stored as a hidden file
# so the persona enumerators (which only pick up ``*.json`` non-dot files) skip
# it. An explicit per-chat pin (resolver.load by name) still resolves — disabling
# means "don't offer it", not "brick an active chat".
_DISABLED_FILE = ".disabled.json"


def _disabled_path(tid: str) -> Path:
    return _forge_paths.tenant_cowork_dir(tid) / "personas" / _DISABLED_FILE


def _load_disabled(tid: str) -> set[str]:
    try:
        raw = json.loads(_disabled_path(tid).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    names = raw.get("disabled") if isinstance(raw, dict) else None
    return {str(n) for n in names} if isinstance(names, list) else set()


def _save_disabled(tid: str, names: set[str]) -> None:
    path = _disabled_path(tid)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({"disabled": sorted(names)}, indent=2)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(payload, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


def _project(p: dict[str, Any], *, source: str, path: Path) -> dict[str, Any]:
    return {
        "name":               p.get("name"),
        "source":             source,
        "is_bundle":          source == "bundle",
        "description":        p.get("description") or "",
        "permission_mode":    p.get("permission_mode"),
        "default_engine":     p.get("engine") or p.get("default_engine"),
        "engine":             p.get("engine"),
        "os_model":           p.get("os_model"),
        "worker_model":       p.get("worker_model"),
        "engine_lock":        bool(p.get("engine_lock")),
        "model":              p.get("model"),
        "disabled":           False,  # overwritten per-tenant in list_personas
        "tool_namespace":     p.get("tool_namespace"),
        "forge_enabled":      bool(p.get("forge_enabled")),
        "skill_forge_enabled": bool(p.get("skill_forge_enabled")),
        "inject_skills":      p.get("inject_skills") if "inject_skills" in p else True,
        "ldd_preset":         p.get("ldd_preset"),
        "mcp_count":          len(p.get("mcp_servers", {}) or {}),
        "tools_allowed":      len(p.get("allowed_tools", []) or []),
        "tools_disallowed":   len(p.get("disallowed_tools", []) or []),
    }


def _find_persona_by_name(tid: str, name: str) -> tuple[dict[str, Any], str, Path] | None:
    """Resolve a persona by name. User overrides win over bundle.

    Returns ``(body, source, path)`` or ``None`` when not found.
    """
    user_dir = _forge_paths.tenant_cowork_dir(tid) / "personas"
    user_path = user_dir / f"{name}.json"
    if user_path.exists():
        body = _load_persona(user_path)
        if body is not None:
            return body, "user", user_path
    bundle_path = _BUNDLE_DIR / f"{name}.json"
    if bundle_path.exists():
        body = _load_persona(bundle_path)
        if body is not None:
            return body, "bundle", bundle_path
    return None


@router.get("/personas/{name}")
def persona_detail(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the full persona body — description, append_system,
    mcp_servers, allowed_tools, ldd_layers, etc."""
    found = _find_persona_by_name(rec.tenant_id, name)
    if found is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"persona {name!r} not found",
        )
    body, source, path = found
    return {
        "name":           body.get("name"),
        "source":         source,
        "is_bundle":      source == "bundle",
        "body":           body,
        "editable":       source == "user",
        "disabled":       name in _load_disabled(rec.tenant_id),
        "path":           str(path),
    }


# ── Persona update (Phase E) ──────────────────────────────────────────


_PERSONA_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")
_PERSONA_BODY_MAX_BYTES = 64 * 1024


class PersonaUpdateRequest(BaseModel):
    body:           dict[str, Any] = Field(..., description="full persona JSON body")
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


@router.put("/personas/{name}")
def persona_update(
    name: str,
    body: PersonaUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create-or-replace a USER-scope persona.

    Bundle personas are read-only by contract — the operator copies
    a bundle persona into the user dir first (separate "copy" action,
    Phase E2), then edits it here.
    """
    if not _PERSONA_NAME_RE.match(name):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid persona name (lowercase, dns-label-like)",
        )

    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="persona.write",
            target_kind="persona",
            target_id=name,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    # Forbid editing a bundle persona via this endpoint — the user
    # MUST first copy-into-user. Bundle integrity is structural.
    bundle_path = _BUNDLE_DIR / f"{name}.json"
    if bundle_path.exists():
        # Bundle name collision → only allow if the user-override
        # already exists (i.e. caller is updating an existing override).
        user_path_check = _forge_paths.tenant_cowork_dir(rec.tenant_id) / "personas" / f"{name}.json"
        if not user_path_check.exists():
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="persona.write",
                target_kind="persona",
                target_id=name,
                reason="bundle-readonly",
            )
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="bundle persona is read-only; copy to user-scope first",
            )

    # Schema sanity: must carry a name field that matches the URL.
    if not isinstance(body.body, dict) or body.body.get("name") != name:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="body.name must equal URL persona name",
        )

    raw = json.dumps(body.body, indent=2, sort_keys=True)
    if len(raw.encode("utf-8")) > _PERSONA_BODY_MAX_BYTES:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"persona body exceeds {_PERSONA_BODY_MAX_BYTES} bytes",
        )

    user_dir = _forge_paths.tenant_cowork_dir(rec.tenant_id) / "personas"
    user_path = user_dir / f"{name}.json"
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        tmp = user_path.with_suffix(user_path.suffix + ".tmp")
        tmp.write_text(raw, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(user_path)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="persona.write",
            target_kind="persona",
            target_id=name,
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"write failed: {e}",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="persona.write",
        target_kind="persona",
        target_id=name,
    )
    return {"name": name, "source": "user", "is_bundle": False, "ok": True}


@router.post("/personas/{name}/copy-from-bundle")
def persona_copy_from_bundle(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Copy a bundle persona into the user-scope dir so it becomes
    editable. Idempotent if user-override already exists (returns
    ``copied=False``)."""
    bundle_path = _BUNDLE_DIR / f"{name}.json"
    if not bundle_path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"bundle persona {name!r} not found",
        )
    user_dir = _forge_paths.tenant_cowork_dir(rec.tenant_id) / "personas"
    user_path = user_dir / f"{name}.json"
    if user_path.exists():
        return {"name": name, "copied": False,
                "is_bundle": False, "ok": True}
    try:
        user_dir.mkdir(parents=True, exist_ok=True)
        text = bundle_path.read_text(encoding="utf-8")
        user_path.write_text(text, encoding="utf-8")
        try:
            user_path.chmod(0o600)
        except OSError:
            pass
    except OSError:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="persona_copy_failed",
        )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="persona.copy_from_bundle",
        target_kind="persona",
        target_id=name,
    )
    return {"name": name, "copied": True,
            "is_bundle": False, "ok": True}


# ── Persona delete + activate/deactivate ─────────────────────────────


class PersonaDeleteRequest(BaseModel):
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


@router.delete("/personas/{name}")
def persona_delete(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: PersonaDeleteRequest | None = None,
) -> dict[str, Any]:
    """Delete a USER-scope persona override.

    Bundle personas are read-only shipped files and cannot be deleted — only a
    user-scope override (created via PUT or copy-from-bundle) is removable.
    Deleting an override that shadows a bundle persona reverts to the bundle
    copy; deleting a purely-user persona removes it entirely (and prunes any
    stale disabled-registry entry). Guarded by CSRF + re-auth, like the write.
    """
    if not _PERSONA_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            detail="invalid persona name")
    re_auth = body.re_auth_token if body is not None else None
    if not verify_reauth(rec, re_auth):
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="persona.delete", target_kind="persona", target_id=name,
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, detail="re-auth failed")

    user_path = _forge_paths.tenant_cowork_dir(rec.tenant_id) / "personas" / f"{name}.json"
    if not user_path.exists():
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="persona.delete", target_kind="persona", target_id=name,
            reason="not-user-scope",
        )
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            detail="no user-scope persona to delete (bundle personas are read-only)",
        )
    try:
        user_path.unlink()
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
            action="persona.delete", target_kind="persona", target_id=name,
            reason="io-error",
        )
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                            detail=f"delete failed: {e}")

    # If the name no longer resolves to any persona (no bundle underneath),
    # prune a stale disabled-registry entry so the registry can't accrue ghosts.
    reverted_to_bundle = (_BUNDLE_DIR / f"{name}.json").exists()
    if not reverted_to_bundle:
        disabled = _load_disabled(rec.tenant_id)
        if name in disabled:
            disabled.discard(name)
            _save_disabled(rec.tenant_id, disabled)

    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="persona.delete", target_kind="persona", target_id=name,
    )
    return {"name": name, "deleted": True,
            "reverted_to_bundle": reverted_to_bundle, "ok": True}


def _set_persona_active(rec: session_auth.SessionRecord, name: str, *, active: bool) -> dict[str, Any]:
    """Shared body for enable/disable — toggles the per-tenant disabled set."""
    if not _PERSONA_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST,
                            detail="invalid persona name")
    # The name must resolve to a real persona (bundle or user) to be toggled.
    if _find_persona_by_name(rec.tenant_id, name) is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            detail=f"persona {name!r} not found")
    disabled = _load_disabled(rec.tenant_id)
    changed = (name in disabled) if active else (name not in disabled)
    if active:
        disabled.discard(name)
    else:
        disabled.add(name)
    if changed:
        try:
            _save_disabled(rec.tenant_id, disabled)
        except OSError as e:
            console_audit.action_failed(
                tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
                action="persona.enable" if active else "persona.disable",
                target_kind="persona", target_id=name, reason="io-error",
            )
            raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                                detail=f"registry write failed: {e}")
    console_audit.action_performed(
        tenant_id=rec.tenant_id, sid_fingerprint=rec.sid_fingerprint,
        action="persona.enable" if active else "persona.disable",
        target_kind="persona", target_id=name,
    )
    return {"name": name, "disabled": not active, "ok": True}


@router.post("/personas/{name}/disable")
def persona_disable(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Deactivate a persona — it is dropped from runtime auto-routing and shown
    as off in the console. Reversible via the enable endpoint. Works for bundle
    and user personas alike (name-level registry, no file mutation)."""
    return _set_persona_active(rec, name, active=False)


@router.post("/personas/{name}/enable")
def persona_enable(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Re-activate a previously deactivated persona."""
    return _set_persona_active(rec, name, active=True)


# ── Per-persona engine & model config (ADR-0123 M3) ──────────────────


_ADR123_FIELDS = frozenset({"engine", "os_model", "worker_model", "engine_lock"})
# Worker-only engines that must not be set as OS engine (ADR-0071).
_WORKER_ONLY_ENGINES = frozenset({"copilot"})


def _allowed_engines_for_tenant(tid: str) -> list[str]:
    """Return the tenant's allowed_engines list (from tenant.corvin.yaml).

    Falls back to the five known engine IDs on any error.
    """
    _default = ["claude_code", "hermes", "codex_cli", "opencode", "copilot"]
    try:
        import yaml as _yaml  # type: ignore
        # SSOT: resolve via forge.paths.tenant_global_dir (same resolver every
        # other reader uses — engine.py, adapter.py, browser.py). The config
        # lives under the ``global/`` subdir; the old inline resolver dropped
        # ``global/`` so the file was never found and the allowed_engines gate
        # silently fell open to the default engine list for every tenant.
        yf = _forge_paths.tenant_global_dir(tid) / "tenant.corvin.yaml"
        if not yf.exists():
            return _default
        data = _yaml.safe_load(yf.read_text(encoding="utf-8")) or {}
        elist = (data.get("spec") or {}).get("allowed_engines") or []
        return list(elist) if elist else _default
    except Exception:  # noqa: BLE001
        return _default


def _get_engine_model_registry() -> dict[str, Any]:
    """Return the engine model registry (fail-open → empty dict)."""
    try:
        from engine_models import registry_as_dict  # type: ignore[import]
        return registry_as_dict()
    except Exception:  # noqa: BLE001
        return {}


class PersonaEngineRequest(BaseModel):
    engine:       str | None = None
    os_model:     str | None = None
    worker_model: str | None = None
    engine_lock:  bool = False
    model_config = {"extra": "forbid"}


@router.get("/personas/{name}/engine")
def persona_engine_get(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the persona's current engine/model pin + available choices.

    ADR-0123 M3.
    """
    found = _find_persona_by_name(rec.tenant_id, name)
    if found is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"persona {name!r} not found",
        )
    body, _source, _path = found
    current = {f: body.get(f) for f in _ADR123_FIELDS}
    if current.get("engine_lock") is None:
        current["engine_lock"] = False

    avail_engines = _allowed_engines_for_tenant(rec.tenant_id)
    registry = _get_engine_model_registry()

    engine_id = current.get("engine") or ""
    avail_os = []
    avail_wm = []
    if engine_id and engine_id in registry:
        avail_os = [m["id"] for m in registry[engine_id].get("os_models") or []]
        avail_wm = [m["id"] for m in registry[engine_id].get("worker_models") or []]

    return {
        **current,
        "available_engines":       avail_engines,
        "available_os_models":     avail_os,
        "available_worker_models": avail_wm,
        "registry":                registry,
    }


@router.put("/personas/{name}/engine")
def persona_engine_put(
    name: str,
    body: PersonaEngineRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Write engine/model pin into the persona's user-scope JSON.

    ADR-0123 M3 — audit-first.  If the persona only exists as a bundle
    entry, it is copied into user scope before being updated.
    """
    if not _PERSONA_NAME_RE.match(name):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid persona name",
        )

    # Validate engine value.
    if body.engine is not None:
        avail = _allowed_engines_for_tenant(rec.tenant_id)
        if body.engine not in avail:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="persona.engine_updated",
                target_kind="persona",
                target_id=name,
                reason="engine-not-allowed",
            )
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"engine {body.engine!r} not in tenant allowed_engines",
            )
        if body.engine in _WORKER_ONLY_ENGINES:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="persona.engine_updated",
                target_kind="persona",
                target_id=name,
                reason="worker-only-engine",
            )
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail=f"engine {body.engine!r} is worker-only and cannot be set as OS engine (ADR-0071)",
            )

    # Validate model IDs against registry (when set).
    if body.engine and (body.os_model or body.worker_model):
        registry = _get_engine_model_registry()
        eng_reg = registry.get(body.engine) or {}
        if body.os_model:
            os_ids = {m["id"] for m in eng_reg.get("os_models") or []}
            if os_ids and body.os_model not in os_ids:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"os_model {body.os_model!r} not in registry for engine {body.engine!r}",
                )
        if body.worker_model:
            wm_ids = {m["id"] for m in eng_reg.get("worker_models") or []}
            if wm_ids and body.worker_model not in wm_ids:
                raise HTTPException(
                    status_code=http_status.HTTP_400_BAD_REQUEST,
                    detail=f"worker_model {body.worker_model!r} not in registry for engine {body.engine!r}",
                )

    # Audit-first invariant.
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="persona.engine_updated",
        target_kind="persona",
        target_id=name,
    )

    # Ensure a user-scope persona file exists (copy from bundle if needed).
    # Use exclusive-create semantics (open "x") to avoid TOCTOU race: if two
    # concurrent requests both see the file missing and both try to copy,
    # the second gets FileExistsError and silently continues — it will read
    # the file the first request already wrote.
    user_dir = _forge_paths.tenant_cowork_dir(rec.tenant_id) / "personas"
    user_path = user_dir / f"{name}.json"
    if not user_path.exists():
        bundle_path = _BUNDLE_DIR / f"{name}.json"
        if not bundle_path.exists():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"persona {name!r} not found",
            )
        try:
            user_dir.mkdir(parents=True, exist_ok=True)
            text = bundle_path.read_text(encoding="utf-8")
            try:
                with open(user_path, "x", encoding="utf-8") as _f:
                    _f.write(text)
                try:
                    user_path.chmod(0o600)
                except OSError:
                    pass
            except FileExistsError:
                pass  # concurrent request already created the file — safe to continue
        except OSError as e:
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=f"copy from bundle failed: {e}",
            ) from e

    # Patch the four ADR-0123 fields in-place.
    try:
        existing = json.loads(user_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"read failed: {e}",
        ) from e

    for field in ("engine", "os_model", "worker_model"):
        val = getattr(body, field)
        if val is None:
            existing.pop(field, None)
        else:
            existing[field] = val
    existing["engine_lock"] = body.engine_lock

    raw = json.dumps(existing, indent=2, sort_keys=True)
    try:
        tmp = user_path.with_suffix(user_path.suffix + ".tmp")
        tmp.write_text(raw, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(user_path)
    except OSError as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"write failed: {e}",
        ) from e

    return {
        "name": name, "ok": True,
        "engine": body.engine, "os_model": body.os_model,
        "worker_model": body.worker_model, "engine_lock": body.engine_lock,
    }


@router.get("/personas")
def list_personas(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    tid = rec.tenant_id
    items: list[dict[str, Any]] = []
    disabled = _load_disabled(tid)

    # Bundle personas (read-only).
    if _BUNDLE_DIR.exists():
        for f in sorted(_BUNDLE_DIR.iterdir()):
            if f.suffix != ".json" or f.name.startswith("."):
                continue
            p = _load_persona(f)
            if p is None or not p.get("name"):
                continue
            items.append(_project(p, source="bundle", path=f))

    # User-override personas (per-tenant cowork dir).
    user_dir = _forge_paths.tenant_cowork_dir(tid) / "personas"
    if user_dir.exists():
        for f in sorted(user_dir.iterdir()):
            if f.suffix != ".json" or f.name.startswith("."):
                continue
            p = _load_persona(f)
            if p is None or not p.get("name"):
                continue
            items.append(_project(p, source="user", path=f))

    # Mark deactivated personas (per-tenant registry) so the UI can render an
    # "off" state with a re-enable toggle. Disabled is a name-level flag, so a
    # user override and its shadowed bundle entry share the same state.
    for it in items:
        it["disabled"] = it["name"] in disabled

    # Sort: bundle first (alphabetical), then user (alphabetical).
    items.sort(key=lambda r: (r["source"] != "bundle", r["name"] or ""))
    return {
        "tenant_id":  tid,
        "count":      len(items),
        "personas":   items,
    }
