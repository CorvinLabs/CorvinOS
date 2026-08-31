"""Run persistence + lifecycle model for the Corvin Gateway.

ADR-0007 Phase 2.2 — Pydantic-validated AWP-shaped Run + per-tenant
on-disk registry. The HTTP layer in ``app.py`` is a thin wrapper
around this module; the engine-dispatch worker added in Phase 2.3
will mutate the same on-disk state via :meth:`RunRegistry.set_status`.

On-disk layout
--------------

``<tenant_home>/global/gateway/runs/<run_id>.json`` (mode ``0o600``).
Atomic-replace pattern identical to ``auth.py`` /  ``consent.py`` /
``roles.py``.

Status state-machine
--------------------

::

    accepted ──▶ running ──▶ completed
                       └──▶ failed
                       └──▶ budget_exceeded

The terminal states are mutually exclusive. ``app.py`` returns 202 +
``run_id`` immediately after writing ``accepted``; the Phase 2.3
worker is the only code path that flips into ``running`` and
onwards.

What this module does NOT do
----------------------------

* It does not own the Gateway's HTTP surface — see ``app.py``.
* It does not dispatch the run to any engine — that's Phase 2.3.
* It does not fire webhooks — that's Phase 2.4.
* It does not stream events — that's Phase 2.5.
* It does not create the tenant directory — Phase 1.4 migration
  helper remains the sole owner of tenant-tree creation. Writing
  into a non-existent tenant raises :class:`RunStoreMalformed`.
"""
from __future__ import annotations

import json
import os
import secrets
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# Same dance the auth module does so we can import from forge.
_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402
from forge import security_events as _security_events  # noqa: E402


# ── Event registration ───────────────────────────────────────────────


_GATEWAY_RUN_EVENTS = {
    "gateway.run_created":          "INFO",
    "gateway.run_not_found":        "WARNING",
    "gateway.cross_tenant_denied":  "WARNING",
    "gateway.run_status_changed":   "INFO",
}
for _evt, _sev in _GATEWAY_RUN_EVENTS.items():
    _security_events.EVENT_SEVERITY.setdefault(_evt, _sev)


# ── Exceptions ───────────────────────────────────────────────────────


class RunStoreMalformed(Exception):
    """Tenant dir missing, mode > 0o600, malformed JSON, etc."""


class RunNotFound(Exception):
    """No on-disk record for the requested run_id."""


# ── Status state-machine ─────────────────────────────────────────────


RunStatus = Literal["accepted", "running", "completed", "failed", "budget_exceeded"]
TERMINAL_STATES = frozenset({"completed", "failed", "budget_exceeded"})
ACTIVE_STATES = frozenset({"accepted", "running"})
ALL_STATES = TERMINAL_STATES | ACTIVE_STATES


# ── Run request schema (AWP-shaped) ──────────────────────────────────


class WebhookSpec(BaseModel):
    """Webhook callback configuration. Phase 2.4 dispatch consumes this."""
    model_config = ConfigDict(extra="forbid")

    url:        str = Field(..., description="HTTPS callback URL.")
    secret_ref: str = Field(..., description="Operator-side name for the HMAC secret.")

    @field_validator("url")
    @classmethod
    def _url_must_be_https(cls, v: str) -> str:
        # PENTEST-6(a): reject plaintext ``http://`` (and every non-https
        # scheme) at validation time. The docs have always said "HTTPS
        # callback URL", but the old check accepted ``http://`` too — which
        # let a caller aim the gateway's outbound POST at an internal plaintext
        # service. HTTPS is required so the callback is confidentiality- and
        # integrity-protected in transit; the resolve-and-reject SSRF guard in
        # ``webhooks.py`` is the second gate applied just before the POST.
        if not isinstance(v, str) or not v.lower().startswith("https://"):
            raise ValueError("webhook.url must be an https:// URL")
        return v


class BudgetOverride(BaseModel):
    """Per-run budget override; clamped to tenant defaults at dispatch."""
    model_config = ConfigDict(extra="forbid")

    max_wall_clock_s: int | None = Field(default=None, ge=1, le=3600)
    max_tokens:       int | None = Field(default=None, ge=1, le=2_000_000)


class RunSpec(BaseModel):
    """The ``spec`` body of an AWP Run."""
    model_config = ConfigDict(extra="forbid")

    persona:          str = Field(..., min_length=1, max_length=64)
    input:            str = Field(..., min_length=1, max_length=64_000)
    webhook:          WebhookSpec | None = None
    budget_override:  BudgetOverride | None = None


class RunRequest(BaseModel):
    """The top-level POST body — AWP envelope."""
    model_config = ConfigDict(extra="forbid")

    apiVersion: Literal["corvin/v1"]
    kind:       Literal["Run"]
    spec:       RunSpec


# ── Stored run record ────────────────────────────────────────────────


@dataclass(frozen=True)
class RunRecord:
    """On-disk projection of an accepted/active/terminal run."""
    run_id:       str
    tenant_id:    str
    status:       RunStatus
    created_at:   float
    updated_at:   float
    request:      dict[str, Any]
    result:       dict[str, Any] | None
    error:        str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":     self.run_id,
            "tenant_id":  self.tenant_id,
            "status":     self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "request":    self.request,
            "result":     self.result,
            "error":      self.error,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRecord":
        return cls(
            run_id=data["run_id"],
            tenant_id=data["tenant_id"],
            status=data["status"],
            created_at=float(data["created_at"]),
            updated_at=float(data["updated_at"]),
            request=data.get("request") or {},
            result=data.get("result"),
            error=data.get("error"),
        )


# ── Path helpers ─────────────────────────────────────────────────────


_REQUIRED_MODE = 0o600


def _runs_dir(tenant_id: str) -> Path:
    """``<tenant_home>/global/gateway/runs/`` — per-tenant run store."""
    return _forge_paths.tenant_global_dir(tenant_id) / "gateway" / "runs"


def _run_path(tenant_id: str, run_id: str) -> Path:
    return _runs_dir(tenant_id) / f"{run_id}.json"


def _audit_path(tenant_id: str) -> Path:
    return _forge_paths.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"


# ── Run-ID generation ────────────────────────────────────────────────


_RUN_ID_PREFIX = "run_"


def generate_run_id() -> str:
    """``run_<22 url-safe-base64-chars>`` — 128 bits of entropy.

    The shape is deliberately distinct from token shape (``atlr_…``)
    so a leaked run-id is never confused with a credential.
    """
    suffix = secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:22]
    # token_urlsafe sometimes returns short strings after stripping
    # padding — top up with a uuid4 hex slice if needed.
    if len(suffix) < 22:
        suffix = (suffix + uuid.uuid4().hex)[:22]
    return f"{_RUN_ID_PREFIX}{suffix}"


# ── Audit helper ─────────────────────────────────────────────────────


def _audit(
    event_type: str,
    *,
    tenant_id: str,
    details: dict[str, Any] | None = None,
    severity: str | None = None,
) -> None:
    try:
        _security_events.write_event(
            _audit_path(tenant_id),
            event_type,
            severity=severity,
            details=dict(details or {}),
            hash_chain=True,
        )
    except Exception:
        pass


# ── On-disk IO ───────────────────────────────────────────────────────


def _atomic_write(path: Path, payload: dict[str, Any]) -> Path:
    """Write *payload* as JSON to *path* with mode 0600, atomically."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    body = (json.dumps(payload, indent=2, sort_keys=True) + "\n").encode("utf-8")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, _REQUIRED_MODE)
    try:
        os.write(fd, body)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, path)
    os.chmod(path, _REQUIRED_MODE)
    return path


def _read_record(tenant_id: str, run_id: str) -> RunRecord:
    p = _run_path(tenant_id, run_id)
    if not p.exists():
        raise RunNotFound(f"no run {run_id!r} for tenant {tenant_id!r}")
    try:
        st = p.stat()
    except OSError as e:
        raise RunStoreMalformed(f"stat failed for {p}: {e}") from e
    mode = st.st_mode & 0o777
    if mode != _REQUIRED_MODE:
        raise RunStoreMalformed(f"{p}: mode 0o{mode:o}, want 0o{_REQUIRED_MODE:o}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise RunStoreMalformed(f"{p}: bad JSON: {e}") from e
    if not isinstance(data, dict):
        raise RunStoreMalformed(f"{p}: top-level must be object")
    return RunRecord.from_dict(data)


# ── Public registry API ──────────────────────────────────────────────


class RunRegistry:
    """Per-tenant run lifecycle.

    Stateless — every call touches the filesystem. The HTTP handler
    constructs one of these per request; tests construct them freely.
    A future in-process cache (Phase 7 — when rate-limiting + gRPC
    land) will sit in front of this without changing the contract.
    """

    def create(self, tenant_id: str, request: RunRequest) -> RunRecord:
        """Persist a fresh ``accepted`` record and return it."""
        validate_tenant_id(tenant_id)
        runs_dir = _runs_dir(tenant_id)
        if not runs_dir.parent.parent.parent.exists():
            # That's <corvin_home>/tenants/<tid>/ — Phase 1.4's job.
            raise RunStoreMalformed(
                f"tenant directory does not exist: "
                f"{runs_dir.parent.parent.parent}"
            )
        runs_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        run_id = generate_run_id()
        # Idempotency floor: extremely unlikely collision still
        # short-circuits with a fresh id rather than overwriting.
        while _run_path(tenant_id, run_id).exists():
            run_id = generate_run_id()
        record = RunRecord(
            run_id=run_id,
            tenant_id=tenant_id,
            status="accepted",
            created_at=now,
            updated_at=now,
            request=request.model_dump(),
            result=None,
            error=None,
        )
        _atomic_write(_run_path(tenant_id, run_id), record.to_dict())
        _audit(
            "gateway.run_created",
            tenant_id=tenant_id,
            details={
                "run_id":  run_id,
                "persona": request.spec.persona,
            },
        )
        return record

    def get(self, tenant_id: str, run_id: str) -> RunRecord:
        """Read a record. Raises :class:`RunNotFound` on miss."""
        validate_tenant_id(tenant_id)
        try:
            return _read_record(tenant_id, run_id)
        except RunNotFound:
            _audit(
                "gateway.run_not_found",
                tenant_id=tenant_id,
                details={"run_id": run_id},
                severity="WARNING",
            )
            raise

    def set_status(
        self,
        tenant_id: str,
        run_id: str,
        status: RunStatus,
        *,
        result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> RunRecord:
        """Transition a run to *status* and persist.

        Phase 2.2 doesn't drive transitions yet — Phase 2.3's engine
        worker is the first caller. The function is here so test
        fixtures can simulate completed runs end-to-end.
        """
        validate_tenant_id(tenant_id)
        if status not in ALL_STATES:
            raise ValueError(f"invalid status: {status!r}")
        current = _read_record(tenant_id, run_id)
        if current.status in TERMINAL_STATES and current.status != status:
            # Refuse silent re-writes; an idempotent setter would mask
            # double-dispatch bugs in the worker.
            raise ValueError(
                f"run {run_id} is already in terminal state "
                f"{current.status!r}; refusing transition to {status!r}"
            )
        updated = RunRecord(
            run_id=current.run_id,
            tenant_id=current.tenant_id,
            status=status,
            created_at=current.created_at,
            updated_at=time.time(),
            request=current.request,
            result=result if result is not None else current.result,
            error=error if error is not None else current.error,
        )
        _atomic_write(_run_path(tenant_id, run_id), updated.to_dict())
        _audit(
            "gateway.run_status_changed",
            tenant_id=tenant_id,
            details={
                "run_id":     run_id,
                "from":       current.status,
                "to":         status,
            },
        )
        return updated
