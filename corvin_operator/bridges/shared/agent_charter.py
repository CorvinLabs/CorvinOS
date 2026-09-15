"""Agent Lifecycle Governance — ADR-0131.

Charter read/write, state-machine, sign-off logic, and orphan detection.
All mutations are audit-logged via forge.security_events.

Invariants:
  - No promotion beyond session scope without a valid charter.
  - Charter files are never deleted; only disabled.
  - Audit events emit metadata only — no charter content.

MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Any

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[1]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))
if str(_THIS_DIR) not in sys.path:
    sys.path.insert(0, str(_THIS_DIR))

from paths import tenant_home as _tenant_home  # noqa: E402

try:
    from forge import security_events as _security_events  # noqa: E402
    _HAS_AUDIT = True
except ImportError:
    _HAS_AUDIT = False

# ── Constants ────────────────────────────────────────────────────────────────

AGENT_KINDS = frozenset({"forge_tool", "skill"})
AGENT_SCOPES = frozenset({"project", "user", "tenant_wide"})
SIGN_ROLES = ("it", "business", "compliance")
DATA_CLASSES = frozenset({"PUBLIC", "INTERNAL", "CONFIDENTIAL", "SECRET"})

# Required sign-off roles per target scope (in order)
SCOPE_SIGN_REQUIREMENTS: dict[str, tuple[str, ...]] = {
    "project":     ("it",),
    "user":        ("it", "business"),
    "tenant_wide": ("it", "business", "compliance"),
}

# Status values
STATUS_ACTIVE          = "active"
STATUS_REVIEW_PENDING  = "review_pending"   # review_date - 14d ≤ now < review_date
STATUS_REVIEW_OVERDUE  = "review_overdue"   # review_date ≤ now < review_date + 14d
STATUS_PENDING_SUNSET  = "pending_sunset"   # review_date + 14d ≤ now < sunset_date
STATUS_DISABLED        = "disabled"         # sunset_date ≤ now OR charter.disabled
STATUS_ORPHAN          = "orphan"           # owner user missing/role lapsed

# Sunset timeline (days)
REVIEW_WARNING_DAYS = 14  # warn this many days before review_date
GRACE_DAYS          = 14  # block new session-starts this many days after review_date

# Agent-ID sanitization: colon is the separator, no path characters
_AGENT_ID_RE = re.compile(r"^(forge_tool|skill):(project|user|tenant_wide):[A-Za-z0-9_\-]{1,64}$")
_SANITIZE_RE = re.compile(r"[^A-Za-z0-9_\-]")


# ── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class SignOff:
    role: str        # "it" | "business" | "compliance"
    signer: str      # username of signer
    signed_at: str   # ISO date YYYY-MM-DD


@dataclass
class AgentCharter:
    version: int
    agent_id: str
    name: str
    kind: str
    scope: str
    # purpose
    problem: str
    success_metric: str
    baseline: float
    target: float
    unit: str
    # ownership
    it_owner: str
    business_owner: str
    compliance_owner: str
    # lifecycle
    created_at: str
    review_date: str
    sunset_date: str
    # compliance
    data_class: str
    egress_zone: str
    engine_allowlist: list[str] = field(default_factory=list)
    # state (mutable)
    sign_offs: list[SignOff] = field(default_factory=list)
    disabled: bool = False


# ── Path helpers ─────────────────────────────────────────────────────────────

def _charters_dir(tenant_id: str | None) -> Path:
    return _tenant_home(tenant_id) / "agents" / "charters"


def _charter_path(tenant_id: str | None, agent_id: str) -> Path:
    safe = _SANITIZE_RE.sub("__", agent_id)
    return _charters_dir(tenant_id) / f"{safe}.json"


# ── Serialization ─────────────────────────────────────────────────────────────

def _sign_off_to_dict(s: SignOff) -> dict[str, str]:
    return {"role": s.role, "signer": s.signer, "signed_at": s.signed_at}


def _sign_off_from_dict(d: dict[str, Any]) -> SignOff:
    return SignOff(role=d["role"], signer=d["signer"], signed_at=d["signed_at"])


def _charter_to_dict(c: AgentCharter) -> dict[str, Any]:
    d = asdict(c)
    d["sign_offs"] = [_sign_off_to_dict(s) for s in c.sign_offs]
    return d


def _charter_from_dict(d: dict[str, Any]) -> AgentCharter:
    sign_offs = [_sign_off_from_dict(s) for s in d.get("sign_offs", [])]
    return AgentCharter(
        version=int(d.get("version", 1)),
        agent_id=d["agent_id"],
        name=d["name"],
        kind=d["kind"],
        scope=d["scope"],
        problem=d["problem"],
        success_metric=d["success_metric"],
        baseline=float(d["baseline"]),
        target=float(d["target"]),
        unit=d["unit"],
        it_owner=d["it_owner"],
        business_owner=d["business_owner"],
        compliance_owner=d["compliance_owner"],
        created_at=d["created_at"],
        review_date=d["review_date"],
        sunset_date=d["sunset_date"],
        data_class=d["data_class"],
        egress_zone=d["egress_zone"],
        engine_allowlist=list(d.get("engine_allowlist", [])),
        sign_offs=sign_offs,
        disabled=bool(d.get("disabled", False)),
    )


# ── Validation ────────────────────────────────────────────────────────────────

def validate_charter(data: dict[str, Any]) -> list[str]:
    """Return a list of error strings. Empty list = valid."""
    errors: list[str] = []

    required_str = [
        "agent_id", "name", "kind", "scope",
        "problem", "success_metric", "unit",
        "it_owner", "business_owner", "compliance_owner",
        "created_at", "review_date", "sunset_date",
        "data_class", "egress_zone",
    ]
    for field_name in required_str:
        if not data.get(field_name):
            errors.append(f"missing required field: {field_name}")

    if errors:
        return errors  # structural errors block further validation

    if data["kind"] not in AGENT_KINDS:
        errors.append(f"kind must be one of {sorted(AGENT_KINDS)}")
    if data["scope"] not in AGENT_SCOPES:
        errors.append(f"scope must be one of {sorted(AGENT_SCOPES)}")
    if data["data_class"] not in DATA_CLASSES:
        errors.append(f"data_class must be one of {sorted(DATA_CLASSES)}")

    for field_name in ("baseline", "target"):
        try:
            float(data[field_name])
        except (TypeError, ValueError, KeyError):
            errors.append(f"{field_name} must be a number")

    try:
        rd = date.fromisoformat(data["review_date"])
        sd = date.fromisoformat(data["sunset_date"])
        if sd <= rd + timedelta(days=14):
            errors.append("sunset_date must be > review_date + 14 days")
    except ValueError:
        errors.append("review_date and sunset_date must be ISO dates (YYYY-MM-DD)")

    if not _AGENT_ID_RE.match(data.get("agent_id", "")):
        errors.append("agent_id must match <kind>:<scope>:<name> pattern")

    return errors


# ── State machine ─────────────────────────────────────────────────────────────

def compute_status(charter: AgentCharter, now_date: date | None = None) -> str:
    """Return the current STATUS_* constant for a charter."""
    if now_date is None:
        now_date = date.today()

    if charter.disabled:
        return STATUS_DISABLED

    try:
        rd = date.fromisoformat(charter.review_date)
        sd = date.fromisoformat(charter.sunset_date)
    except ValueError:
        return STATUS_DISABLED  # malformed dates → disable

    if now_date >= sd:
        return STATUS_DISABLED

    if now_date >= rd + timedelta(days=GRACE_DAYS):
        return STATUS_PENDING_SUNSET

    if now_date >= rd:
        return STATUS_REVIEW_OVERDUE

    if now_date >= rd - timedelta(days=REVIEW_WARNING_DAYS):
        return STATUS_REVIEW_PENDING

    return STATUS_ACTIVE


def days_until(iso_date: str, now_date: date | None = None) -> int:
    if now_date is None:
        now_date = date.today()
    return (date.fromisoformat(iso_date) - now_date).days


# ── Sign-off helpers ──────────────────────────────────────────────────────────

def get_required_roles(scope: str) -> tuple[str, ...]:
    return SCOPE_SIGN_REQUIREMENTS.get(scope, ())


def has_role_signed(charter: AgentCharter, role: str) -> bool:
    return any(s.role == role for s in charter.sign_offs)


def current_signed_scope(charter: AgentCharter) -> str | None:
    """Return the highest scope fully signed off, or None."""
    roles_signed = {s.role for s in charter.sign_offs}
    for scope in ("tenant_wide", "user", "project"):
        required = set(SCOPE_SIGN_REQUIREMENTS[scope])
        if required.issubset(roles_signed):
            return scope
    return None


def can_sign_for_scope(charter: AgentCharter, role: str, target_scope: str) -> tuple[bool, str]:
    """Return (allowed, reason). Signing must proceed in scope order."""
    if role not in SCOPE_SIGN_REQUIREMENTS.get(target_scope, ()):
        return False, f"role '{role}' is not required for scope '{target_scope}'"
    if has_role_signed(charter, role):
        return False, f"role '{role}' has already signed"
    # Check prerequisite: cannot sign for user scope without IT signing first, etc.
    required = SCOPE_SIGN_REQUIREMENTS[target_scope]
    idx = required.index(role)
    for prereq in required[:idx]:
        if not has_role_signed(charter, prereq):
            return False, f"'{prereq}' must sign before '{role}'"
    return True, "ok"


# ── CRUD ──────────────────────────────────────────────────────────────────────

def load_charter(tenant_id: str | None, agent_id: str) -> AgentCharter | None:
    path = _charter_path(tenant_id, agent_id)
    if not path.exists():
        return None
    try:
        return _charter_from_dict(json.loads(path.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, KeyError, ValueError):
        return None


def save_charter(tenant_id: str | None, charter: AgentCharter,
                 *, exclusive: bool = False) -> None:
    """Atomic write to charters directory. Creates directory if needed.

    If exclusive=True, raises FileExistsError if a charter file already exists
    (atomic duplicate guard via os.link).
    """
    d = _charters_dir(tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    path = _charter_path(tenant_id, charter.agent_id)
    payload = json.dumps(_charter_to_dict(charter), indent=2, ensure_ascii=False)
    fd, tmp = tempfile.mkstemp(dir=d, suffix=".tmp")
    link_ok = False
    try:
        os.write(fd, payload.encode("utf-8"))
        os.close(fd)
        if exclusive:
            # os.link is atomic — fails with OSError/FileExistsError if target exists.
            try:
                os.link(tmp, path)
                link_ok = True
            except OSError:
                pass
            try:
                os.unlink(tmp)
            except OSError:
                pass
            if not link_ok:
                raise FileExistsError(f"charter already exists: {charter.agent_id!r}")
        else:
            os.replace(tmp, path)
    except Exception:
        if not link_ok:
            try:
                os.unlink(tmp)
            except OSError:
                pass
        raise


def list_charters(tenant_id: str | None) -> list[AgentCharter]:
    d = _charters_dir(tenant_id)
    if not d.exists():
        return []
    result: list[AgentCharter] = []
    for p in sorted(d.glob("*.json")):
        try:
            result.append(_charter_from_dict(json.loads(p.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, ValueError):
            pass
    return result


def delete_charter_file(tenant_id: str | None, agent_id: str) -> bool:
    """Only for tests / admin tooling. Do NOT call from normal code paths."""
    path = _charter_path(tenant_id, agent_id)
    if path.exists():
        path.unlink()
        return True
    return False


# ── Audit emission ─────────────────────────────────────────────────────────────

def _emit(tenant_id: str | None, event: str, severity: str = "INFO", **details: Any) -> None:
    if not _HAS_AUDIT:
        return
    try:
        th = _tenant_home(tenant_id)
        audit_path = th / "global" / "forge" / "audit.jsonl"
        _security_events.write_event(
            audit_path,        # positional: path
            event,             # positional: event_type
            severity=severity,
            details=dict(details),
        )
    except Exception:
        pass  # audit emit is best-effort; never blocks business logic


def emit_charter_created(tenant_id: str | None, charter: AgentCharter,
                          sid_fingerprint: str = "") -> None:
    _emit(tenant_id, "agent.charter_created",
          agent_id=charter.agent_id,
          kind=charter.kind,
          scope=charter.scope,
          tenant_id=tenant_id or "_default",
          sid_fingerprint=sid_fingerprint)


def emit_charter_renewed(tenant_id: str | None, charter: AgentCharter,
                          prior_version: int, sid_fingerprint: str = "") -> None:
    _emit(tenant_id, "agent.charter_renewed",
          agent_id=charter.agent_id,
          prior_version=prior_version,
          tenant_id=tenant_id or "_default",
          sid_fingerprint=sid_fingerprint)


def emit_sign_off(tenant_id: str | None, agent_id: str, signer_role: str,
                  scope_target: str, sid_fingerprint: str = "") -> None:
    _emit(tenant_id, "agent.sign_off",
          agent_id=agent_id,
          signer_role=signer_role,
          scope_target=scope_target,
          tenant_id=tenant_id or "_default",
          sid_fingerprint=sid_fingerprint)


def emit_sign_off_revoked(tenant_id: str | None, agent_id: str, signer_role: str,
                           prior_scope: str | None, sid_fingerprint: str = "") -> None:
    _emit(tenant_id, "agent.sign_off_revoked", severity="WARNING",
          agent_id=agent_id,
          signer_role=signer_role,
          prior_scope=prior_scope or "none",
          tenant_id=tenant_id or "_default",
          sid_fingerprint=sid_fingerprint)


def emit_orphan_detected(tenant_id: str | None, agent_id: str,
                          missing_role: str) -> None:
    _emit(tenant_id, "agent.orphan_detected", severity="WARNING",
          agent_id=agent_id,
          missing_role=missing_role,
          tenant_id=tenant_id or "_default")


def emit_review_pending(tenant_id: str | None, agent_id: str,
                         days_until_review: int) -> None:
    _emit(tenant_id, "agent.review_pending", severity="WARNING",
          agent_id=agent_id,
          days_until_review=days_until_review,
          tenant_id=tenant_id or "_default")


def emit_review_overdue(tenant_id: str | None, agent_id: str,
                         days_overdue: int) -> None:
    _emit(tenant_id, "agent.review_overdue", severity="WARNING",
          agent_id=agent_id,
          days_overdue=days_overdue,
          tenant_id=tenant_id or "_default")


def emit_pending_sunset(tenant_id: str | None, agent_id: str,
                         days_until_sunset: int) -> None:
    _emit(tenant_id, "agent.pending_sunset", severity="WARNING",
          agent_id=agent_id,
          days_until_sunset=days_until_sunset,
          tenant_id=tenant_id or "_default")


def emit_sunset(tenant_id: str | None, agent_id: str, kind: str,
                final_scope: str) -> None:
    _emit(tenant_id, "agent.sunset", severity="CRITICAL",
          agent_id=agent_id,
          kind=kind,
          final_scope=final_scope,
          tenant_id=tenant_id or "_default")


def emit_compliance_check_failed(tenant_id: str | None, agent_id: str,
                                  check_id: str) -> None:
    _emit(tenant_id, "agent.compliance_check_failed", severity="WARNING",
          agent_id=agent_id,
          check_id=check_id,
          tenant_id=tenant_id or "_default")


def emit_session_start_blocked(tenant_id: str | None, agent_id: str,
                                reason: str, sid_fingerprint: str = "") -> None:
    _emit(tenant_id, "agent.session_start_blocked", severity="WARNING",
          agent_id=agent_id,
          reason=reason,
          tenant_id=tenant_id or "_default",
          sid_fingerprint=sid_fingerprint)


def emit_single_owner_exception(tenant_id: str | None, agent_id: str) -> None:
    _emit(tenant_id, "agent.charter_single_owner_exception", severity="WARNING",
          agent_id=agent_id,
          tenant_id=tenant_id or "_default")


# ── Compliance pre-check ──────────────────────────────────────────────────────

def compliance_pre_check(charter: AgentCharter, tenant_id: str | None = None) -> list[str]:
    """Return a list of failed check IDs. Empty = all passed."""
    failures: list[str] = []

    # Check 1: data_class + egress_zone basic coherence
    data_class = charter.data_class
    egress_zone = charter.egress_zone
    if data_class == "SECRET" and egress_zone != "local":
        failures.append("data_class_egress_mismatch")
    if data_class == "CONFIDENTIAL" and egress_zone not in ("local",):
        failures.append("confidential_requires_local_egress")

    # Check 2: engine_allowlist must not be empty for governed scopes
    if charter.scope in ("project", "user", "tenant_wide") and not charter.engine_allowlist:
        failures.append("engine_allowlist_empty")

    return failures
