"""Settings — read-only viewer for the configurable YAML / JSON files.

Phase F: surfaces six known files so the operator can SEE what their
instance is configured with. Edits land in Phase E2 (separate
session — needs YAML round-trip preservation + per-file Pydantic
validation).

Files surfaced:
  * tenant.corvin.yaml       (per-tenant policy: zone, engines, budget)
  * data_policy.yaml          (Layer 24 PII redaction strategies)
  * ldd.json                  (Layer 14 LDD layer toggles)
  * dialectic.json            (Layer 11 dialectical-decide site modes)
  * relay.json                (cross-bridge notification routing)
  * branding.yaml             (ADR-0010 disclosure-card extras)

Returns one entry per file with: path, present, mode (octal), size,
mtime, raw body (markdown-style code-block on the SPA side).

The body is capped at 64 KiB to keep the response small. Any of
these files larger than that is structurally suspicious anyway.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated, Any

import json as _json

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

try:
    import yaml as _yaml          # opt-in: pyyaml ships in the console venv
except Exception:                 # pragma: no cover
    _yaml = None  # type: ignore[assignment]

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()

_BODY_CAP_B = 64 * 1024


def _launcher_config_path() -> Path:
    """Resolve ~/.config/corvin-launcher/config.json regardless of tenant."""
    return Path.home() / ".config" / "corvin-launcher" / "config.json"


def _get_installed_version() -> str:
    """Return the installed corvinos package version. Works for pip installs and
    dev/source installs where importlib.metadata has no entry."""
    import importlib.metadata as _meta  # noqa: PLC0415
    for pkg in ("corvinos", "corvin-console", "corvinOS"):
        try:
            return _meta.version(pkg)
        except Exception:
            pass
    # Source / editable install: walk pyproject.toml files upward from this module.
    try:
        here = Path(__file__).resolve()
        for parent in here.parents:
            p = parent / "pyproject.toml"
            if p.exists():
                for line in p.read_text(encoding="utf-8").splitlines():
                    s = line.strip()
                    if s.startswith("version") and "=" in s:
                        v = s.split("=", 1)[1].strip().strip('"').strip("'")
                        if v and v[0].isdigit():
                            return v
    except Exception:
        pass
    return "unknown"


def _files_for_tenant(tid: str) -> list[tuple[str, Path, str]]:
    """Return ``(label, path, kind)`` triples for every known config."""
    g = _forge_paths.tenant_global_dir(tid)
    return [
        ("tenant.corvin.yaml",   g / "tenant.corvin.yaml",  "yaml"),
        ("data_policy.yaml",      g / "data_policy.yaml",    "yaml"),
        ("ldd.json",              g / "ldd.json",             "json"),
        ("dialectic.json",        g / "dialectic.json",       "json"),
        ("relay.json",            g / "relay.json",           "json"),
        ("branding.yaml",         g / "branding.yaml",        "yaml"),
        ("corvin-launcher.json",  _launcher_config_path(),    "json"),
    ]


def _project(label: str, path: Path, kind: str) -> dict[str, Any]:
    if not path.exists():
        return {
            "label":     label,
            "path":      str(path),
            "kind":      kind,
            "present":   False,
            "mode":      None,
            "size_bytes": None,
            "mtime":     None,
            "body":      None,
            "truncated": False,
        }
    try:
        st = path.stat()
    except OSError:
        return {
            "label": label, "path": str(path), "kind": kind,
            "present": False, "mode": None, "size_bytes": None,
            "mtime": None, "body": None, "truncated": False,
        }
    body: str | None = None
    truncated = False
    try:
        if st.st_size > _BODY_CAP_B:
            with path.open("rb") as fh:
                body = fh.read(_BODY_CAP_B).decode("utf-8", errors="replace")
            truncated = True
        else:
            body = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        body = None
    return {
        "label":      label,
        "path":       str(path),
        "kind":       kind,
        "present":    True,
        "mode":       f"0o{(st.st_mode & 0o777):o}",
        "size_bytes": st.st_size,
        "mtime":      st.st_mtime,
        "body":       body,
        "truncated":  truncated,
    }


@router.get("/settings")
def settings_index(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    tid = rec.tenant_id
    files = [_project(label, path, kind)
             for label, path, kind in _files_for_tenant(tid)]
    present_count = sum(1 for f in files if f["present"])
    return {
        "tenant_id":      tid,
        "ts":             time.time(),
        "global_dir":     str(_forge_paths.tenant_global_dir(tid)),
        "files":          files,
        "present_count":  present_count,
        "total_count":    len(files),
        "edit_phase":     "E2 — editable with Re-Auth + structural validation",
    }


# ── Phase E2 — Settings PUT with structural validation ────────────────


class SettingsWriteRequest(BaseModel):
    body:           str = Field(..., description="full file body (YAML or JSON)")
    re_auth_token: str | None = None
    model_config = {"extra": "forbid"}


def _file_meta(label: str) -> tuple[Path, str] | None:
    """Resolve a label to (path, kind). Returns None on unknown label."""
    # We can't resolve via tenant_id without a session; defer the
    # tenant-scoped path lookup to the caller.
    for known_label, _path, kind in _files_for_tenant("_default"):
        if known_label == label:
            return None, kind  # placeholder: caller resolves the tenant path
    return None


def _validate_body(label: str, kind: str, body: str) -> tuple[bool, str | None]:
    """Run a structural parse appropriate for *kind*.

    Returns ``(ok, error_msg_or_None)``. We deliberately do NOT
    enforce schema here — the gateway / forge / ldd modules each
    have their own load-time Pydantic validator and will reject a
    structurally-broken file at next read. Console's job is to
    catch the basic JSON/YAML parse failure so the operator does
    not save a file that breaks every reader downstream.
    """
    if not body.strip():
        # Allow empty body — interpret as "reset to defaults" for some
        # files. The downstream loaders treat absent file as default.
        return True, None
    if kind == "json":
        try:
            parsed = _json.loads(body)
            if label == "corvin-launcher.json":
                if "auto_update" in parsed and not isinstance(parsed["auto_update"], bool):
                    return False, "auto_update must be true or false"
            return True, None
        except _json.JSONDecodeError as e:
            return False, f"invalid JSON: {e.msg} at line {e.lineno} col {e.colno}"
    if kind == "yaml":
        if _yaml is None:
            # No YAML installed → don't block writes; the downstream
            # gateway loader will catch issues at next read.
            return True, "no PyYAML in venv — skipping structural check"
        try:
            _yaml.safe_load(body)
            return True, None
        except _yaml.YAMLError as e:
            return False, f"invalid YAML: {e}"
    return True, None


class AutoUpdateRequest(BaseModel):
    enabled: bool
    model_config = {"extra": "forbid"}


def _read_auto_update() -> bool:
    """Return the current auto_update flag from corvin-launcher config. Default: True."""
    try:
        data = _json.loads(_launcher_config_path().read_text(encoding="utf-8"))
        return bool(data.get("auto_update", True))
    except Exception:
        return True


def _write_auto_update(enabled: bool) -> None:
    """Persist auto_update in ~/.config/corvin-launcher/config.json."""
    path = _launcher_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        data = _json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    data["auto_update"] = enabled
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


@router.get("/settings/auto-update")
def get_auto_update(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the current auto-update-on-startup setting plus installed version."""
    enabled = _read_auto_update()
    return {
        "enabled": enabled,
        "path": str(_launcher_config_path()),
        "configured": _launcher_config_path().exists(),
        "version": _get_installed_version(),
    }


@router.put("/settings/auto-update")
def put_auto_update(
    body: AutoUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Enable or disable automatic PyPI update on startup."""
    try:
        _write_auto_update(body.enabled)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id="corvin-launcher.json",
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write_failed",
        ) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="settings.write",
        target_kind="settings_file",
        target_id="corvin-launcher.json",
    )
    return {"enabled": body.enabled, "ok": True}


# ── Delegation budget (structured endpoint, no YAML round-trip risk) ──────────

# Keys the UI is allowed to touch — a subset of _DELEGATION_BUDGET_DEFAULTS.
_BUDGET_KEYS = {
    "timeout_seconds":  {"type": int, "min": 30,    "max": 8640000, "default": 360000},
    "max_worker_turns": {"type": int, "min": 1,     "max": 50000,   "default": 10000},
    "max_loops":        {"type": int, "min": 1,     "max": 2000,    "default": 500},
    "max_wall_time":    {"type": int, "min": 60,    "max": 8640000, "default": 360000},
    "max_total_workers":{"type": int, "min": 1,     "max": 1600,    "default": 400},
    "max_depth":        {"type": int, "min": 1,     "max": 2000,    "default": 200},
}


def _budget_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "delegation_budget.json"


def _read_budget(tenant_id: str) -> dict:
    path = _budget_path(tenant_id)
    stored: dict = {}
    if path.exists():
        try:
            stored = _json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            stored = {}
    return {k: stored.get(k, meta["default"]) for k, meta in _BUDGET_KEYS.items()}


def _write_budget(tenant_id: str, values: dict) -> None:
    path = _budget_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(_json.dumps(values, indent=2), encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


class DelegationBudgetRequest(BaseModel):
    timeout_seconds:   int | None = None
    max_worker_turns:  int | None = None
    max_loops:         int | None = None
    max_wall_time:     int | None = None
    max_total_workers: int | None = None
    max_depth:         int | None = None
    model_config = {"extra": "forbid"}


@router.get("/settings/delegation-budget")
def get_delegation_budget(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    budget = _read_budget(rec.tenant_id)
    # Include meta for the UI (min/max/default per key)
    return {
        "values": budget,
        "meta": {k: {mk: mv for mk, mv in m.items() if mk != "type"}
                 for k, m in _BUDGET_KEYS.items()},
        "path": str(_budget_path(rec.tenant_id)),
    }


@router.put("/settings/delegation-budget")
def put_delegation_budget(
    body: DelegationBudgetRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    current = _read_budget(rec.tenant_id)
    updates = body.model_dump(exclude_none=True)
    errors: list[str] = []
    for key, raw_val in updates.items():
        meta = _BUDGET_KEYS.get(key)
        if meta is None:
            errors.append(f"unknown key: {key}")
            continue
        if not isinstance(raw_val, meta["type"]):
            errors.append(f"{key}: expected {meta['type'].__name__}")
            continue
        lo, hi = meta["min"], meta["max"]
        if not (lo <= raw_val <= hi):
            errors.append(f"{key}: must be between {lo} and {hi}")
    if errors:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id="delegation_budget.json",
            reason="validation-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="; ".join(errors),
        )
    new_values = {**current, **updates}
    try:
        _write_budget(rec.tenant_id, new_values)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id="delegation_budget.json",
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write_failed",
        ) from e
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="settings.write",
        target_kind="settings_file",
        target_id="delegation_budget.json",
    )
    return {"values": new_values, "ok": True}


@router.put("/settings/{label}")
def settings_write(
    label: str,
    body: SettingsWriteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create-or-replace one of the six known settings files.

    Compliance baseline:
      * Re-Auth required (body.re_auth_token verified against session
        fingerprint)
      * Structural validation (JSON/YAML parse before any disk write)
      * 64 KiB body cap (mirror of read-side _BODY_CAP_B)
      * File mode 0o600 enforced after write
      * Audit ``console.action_performed action=settings.write``
        OR ``console.action_failed`` with curated reason
      * Filename allow-list (six labels — anything else → 404)
    """
    # Resolve label → tenant-scoped path + kind.
    file_path: Path | None = None
    kind: str | None = None
    for known_label, path, k in _files_for_tenant(rec.tenant_id):
        if known_label == label:
            file_path = path
            kind = k
            break
    if file_path is None or kind is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"unknown settings file {label!r}",
        )

    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id=label,
            reason="reauth-failed",
        )
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    if len(body.body.encode("utf-8")) > _BODY_CAP_B:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id=label,
            reason="body-too-large",
        )
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"body exceeds {_BODY_CAP_B} bytes",
        )

    ok, err = _validate_body(label, kind, body.body)
    if not ok:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id=label,
            reason="validation-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=err or "validation failed",
        )

    try:
        file_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = file_path.with_suffix(file_path.suffix + ".tmp")
        tmp.write_text(body.body, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(file_path)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="settings.write",
            target_kind="settings_file",
            target_id=label,
            reason="io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write_failed",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="settings.write",
        target_kind="settings_file",
        target_id=label,
    )
    st = file_path.stat()
    return {
        "label":       label,
        "size_bytes":  st.st_size,
        "modified":    st.st_mtime,
        "mode":        f"0o{(st.st_mode & 0o777):o}",
        "ok":          True,
        "warning":     err,  # may carry "no PyYAML — skipped" etc.
    }
