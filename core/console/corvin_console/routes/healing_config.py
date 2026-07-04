"""Healing configuration — structured toggles for the self-healing subsystem.

Surfaces three tenant-policy flags in ``tenant.corvin.yaml`` as a small,
validated JSON API so the console Settings page can flip them without editing
raw YAML:

  * ``telemetry.healing_traces``  — upload anonymised self-healing events
    (ADR-0180; the operator-level gate — the per-user ConsentAct is separate).
  * ``aco.l5_enabled``            — ACO Layer-5 actuating self-repair on/off
    (ADR-0178, Tier LOCAL). Default ON.
  * ``aco.l5_risky``             — allow the *risky* repair tier (patches to
    Python source). Default OFF.

Reader≠writer discipline: this router reads and writes the SAME file the raw
Settings viewer (``routes/settings.py``) surfaces — ``tenant_global_dir(tid) /
tenant.corvin.yaml`` — so a toggle here is immediately visible in the raw YAML
card, and the ACO runtime (``aco/repair_actions.py``) + telemetry consent gate
(``aco/htrace_consent.py``) read the same keys back.

``tenant.corvin.yaml`` is a k8s-style manifest (apiVersion/kind/metadata/spec);
the runtime settings live under the ``spec:`` wrapper. Reads and writes therefore
resolve through ``spec`` (falling back to the top-level document for a legacy flat
file, mirroring the ``data.get("spec", data)`` shape used by the consent gate and
the ACO repair loop).

Writes are a MERGE, never a full replace: only the three keys above are
patched under ``spec``; every other key in the document is preserved.
"""
from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

try:
    import yaml as _yaml          # opt-in: pyyaml ships in the console venv
except Exception:                 # pragma: no cover
    _yaml = None  # type: ignore[assignment]

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


def _config_path(tenant_id: str) -> Path:
    """tenant.corvin.yaml for this tenant — the same file the Settings viewer edits."""
    return _forge_paths.tenant_global_dir(tenant_id) / "tenant.corvin.yaml"


def _read_config(tenant_id: str) -> dict[str, Any]:
    """Return the parsed YAML document (empty dict if absent / unreadable)."""
    path = _config_path(tenant_id)
    if _yaml is None or not path.exists():
        return {}
    try:
        data = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _read_flags(tenant_id: str) -> dict[str, bool]:
    """Resolve the three flags with their defaults (deny-by-default for risky)."""
    cfg = _read_config(tenant_id)
    # tenant.corvin.yaml is a k8s-style manifest — settings live under spec:.
    # Fall back to the top-level document when no spec wrapper is present.
    spec = cfg.get("spec") if isinstance(cfg.get("spec"), dict) else cfg
    telemetry = spec.get("telemetry") if isinstance(spec.get("telemetry"), dict) else {}
    aco = spec.get("aco") if isinstance(spec.get("aco"), dict) else {}
    return {
        "telemetry_enabled": bool(telemetry.get("healing_traces", True)),
        "healing_enabled":   bool(aco.get("l5_enabled", True)),
        "risky_enabled":     bool(aco.get("l5_risky", False)),
    }


def _write_flags(tenant_id: str, patch: dict[str, bool]) -> None:
    """Merge the changed flags into tenant.corvin.yaml, preserving every other key.

    Settings live under the ``spec:`` wrapper of the k8s-style manifest. When a
    spec wrapper is present the flags are merged under ``spec.telemetry`` /
    ``spec.aco`` (the location the consent gate + ACO loop read back); a legacy
    flat document with no spec is patched at the top level so the same-shaped
    ``data.get("spec", data)`` readers still resolve them. Every other key —
    including the whole manifest header — is preserved. The write is atomic
    (tmp file + os.replace).
    """
    if _yaml is None:
        raise RuntimeError("pyyaml unavailable")
    path = _config_path(tenant_id)
    cfg = _read_config(tenant_id)

    # Mirror the reader: write into spec: when the manifest has one, else top-level.
    # `target` is a live reference into `cfg`, so mutating it mutates the document
    # that is dumped below — the full manifest structure is preserved.
    target = cfg["spec"] if isinstance(cfg.get("spec"), dict) else cfg

    if "telemetry_enabled" in patch:
        if not isinstance(target.get("telemetry"), dict):
            target["telemetry"] = {}
        target["telemetry"]["healing_traces"] = patch["telemetry_enabled"]
    if "healing_enabled" in patch or "risky_enabled" in patch:
        if not isinstance(target.get("aco"), dict):
            target["aco"] = {}
        if "healing_enabled" in patch:
            target["aco"]["l5_enabled"] = patch["healing_enabled"]
        if "risky_enabled" in patch:
            target["aco"]["l5_risky"] = patch["risky_enabled"]

    path.parent.mkdir(parents=True, exist_ok=True)
    body = _yaml.dump(cfg, default_flow_style=False, sort_keys=False)
    tmp = path.with_suffix(".yaml.tmp")
    tmp.write_text(body, encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)


class HealingConfigRequest(BaseModel):
    telemetry_enabled: bool | None = None
    healing_enabled:   bool | None = None
    risky_enabled:     bool | None = None
    model_config = {"extra": "forbid"}


@router.get("/healing-config")
def get_healing_config(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the current self-healing flags for this tenant."""
    return _read_flags(rec.tenant_id)


@router.patch("/healing-config")
def patch_healing_config(
    body: HealingConfigRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Update one or more self-healing flags. Only the supplied keys are changed."""
    patch = body.model_dump(exclude_none=True)
    if patch:
        try:
            _write_flags(rec.tenant_id, patch)
        except Exception as e:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="settings.write",
                target_kind="settings_file",
                target_id="tenant.corvin.yaml",
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
            target_id="tenant.corvin.yaml",
        )
    return _read_flags(rec.tenant_id)
