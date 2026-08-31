"""Layer 39 — Incident Tracker (ADR-0057).

Tracks structural control failures that may constitute "serious incidents"
under EU AI Act Art. 73.  Provides:

  - IncidentRecord dataclass (storage schema)
  - IncidentAutoDetector  (hook for CRITICAL audit events)
  - open_incident() / update_incident() / close_incident()
  - list_incidents() / load_incident() / export_incidents()
  - notify_draft()  (Art. 73 §2 notification template generator)

Storage: <tenant>/global/incidents/<incident_id>.json  (mode 0600, atomic).

The ``description`` field is stored ONLY in the incident record and NEVER
enters the L16 audit chain — same principle as L36 ErasureRequest.notes.

Severity definitions (Art. 73 context):
  "serious"       — structural control failure; 15-day Art. 73 clock starts
  "warning"       — degraded state; no notification obligation
  "informational" — logged for awareness; no action required

Must NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import os
import re
import sys
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── Category definitions (ADR-0057 § Component 2) ──────────────────────────

CATEGORIES: frozenset[str] = frozenset({
    "chain_integrity",          # audit.chain_gap_detected CRITICAL
    "consent_bypass",           # message processed without consent.granted
    "engine_policy_violation",  # data_flow.blocked / egress.blocked gate failed open
    "pii_in_audit_chain",       # regex scan detects unredacted PII in audit segment
    "secret_exposure",          # path_gate.denied on vault path + write succeeded
    "disclosure_failure",       # session reached message_received without disclosure.shown
})

SEVERITIES: frozenset[str] = frozenset({"serious", "warning", "informational"})
STATUSES: frozenset[str] = frozenset({"open", "contained", "notified", "closed"})

# CRITICAL audit events that auto-trigger incident detection
TRIGGER_EVENTS: dict[str, str] = {
    "audit.chain_gap_detected":  "chain_integrity",
    "data_flow.blocked":         "engine_policy_violation",
    "egress.blocked":            "engine_policy_violation",
    "path_gate.denied":          "secret_exposure",
}

# Regex for unredacted PII patterns (conservative — triggers review, not block)
_PII_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b"),  # email
    re.compile(r"\b\+?[\d\s\-()]{10,20}\b"),  # phone
]


# ── IncidentRecord ──────────────────────────────────────────────────────────

@dataclass
class IncidentRecord:
    incident_id: str
    detected_at: str
    category: str
    severity: str
    trigger_event: str
    trigger_chain_hash: str   # first 16 hex chars; never full hash
    description: str          # operator-authored; NEVER in audit chain
    status: str
    notified_at: str | None = None
    closed_at: str | None = None
    tenant_id: str = "_default"

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown category: {self.category!r}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"unknown severity: {self.severity!r}")
        if self.status not in STATUSES:
            raise ValueError(f"unknown status: {self.status!r}")
        h = self.trigger_chain_hash or ""
        if not re.match(r"^[0-9a-f]{1,16}$", h):
            raise ValueError(
                f"trigger_chain_hash must be 1-16 lowercase hex chars, got: {h!r}"
            )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "IncidentRecord":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


# ── Path resolution ─────────────────────────────────────────────────────────

def _corvin_home() -> Path:
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    return Path(os.path.expanduser(env)) if env else Path.home() / ".corvin"


def _incidents_dir(tenant_id: str) -> Path:
    return _corvin_home() / "tenants" / tenant_id / "global" / "incidents"


# ── Audit emit helper ───────────────────────────────────────────────────────

def _emit(event_type: str, details: dict[str, Any], *, severity: str = "INFO") -> None:
    """Best-effort audit emit.  Never raises.  Only allow-listed keys."""
    try:
        repo = Path(__file__).resolve().parents[3]
        forge_path = repo / "operator" / "forge"
        if forge_path.is_dir() and str(forge_path) not in sys.path:
            sys.path.insert(0, str(forge_path))
        from forge import security_events as _se  # type: ignore

        audit_dir = _corvin_home() / "global" / "forge"
        audit_dir.mkdir(parents=True, exist_ok=True)
        audit_file = Path(
            os.environ.get("VOICE_AUDIT_PATH") or str(audit_dir / "audit.jsonl")
        )
        _se.write_event(audit_file, event_type, severity=severity,
                        tool="", run_id="", details=details, hash_chain=True)
    except Exception:
        pass


# ── Storage layer ───────────────────────────────────────────────────────────

def _write_incident(record: IncidentRecord) -> None:
    """Atomic write, mode 0600."""
    d = _incidents_dir(record.tenant_id)
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{record.incident_id}.json"
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(record.to_dict(), indent=2, ensure_ascii=False))
    tmp.chmod(0o600)
    tmp.rename(p)
    p.chmod(0o600)


def _read_incident(tenant_id: str, incident_id: str) -> IncidentRecord | None:
    p = _incidents_dir(tenant_id) / f"{incident_id}.json"
    if not p.exists():
        return None
    try:
        return IncidentRecord.from_dict(json.loads(p.read_text("utf-8")))
    except Exception:
        return None


# ── Public API ──────────────────────────────────────────────────────────────

def open_incident(
    *,
    category: str,
    trigger_event: str,
    trigger_chain_hash: str,
    description: str,
    severity: str = "serious",
    tenant_id: str = "_default",
) -> IncidentRecord:
    """Open a new incident record and emit incident.opened to the audit chain.

    Audit allow-list: incident_id, category, severity, trigger_event,
    trigger_chain_hash.  Never: description, tenant PII.
    """
    now = datetime.now(timezone.utc).isoformat()
    record = IncidentRecord(
        incident_id=str(uuid.uuid4()),
        detected_at=now,
        category=category,
        severity=severity,
        trigger_event=trigger_event,
        trigger_chain_hash=trigger_chain_hash[:16],
        description=description,
        status="open",
        tenant_id=tenant_id,
    )
    _write_incident(record)
    _emit("incident.opened", {
        "incident_id": record.incident_id,
        "category": record.category,
        "severity": record.severity,
        "trigger_event": record.trigger_event,
        "trigger_chain_hash": record.trigger_chain_hash,
    }, severity="CRITICAL" if record.severity == "serious" else "WARNING")
    return record


def update_incident(
    incident_id: str,
    new_status: str,
    *,
    tenant_id: str = "_default",
    notified_at: str | None = None,
) -> IncidentRecord:
    """Transition an incident to a new status."""
    record = _read_incident(tenant_id, incident_id)
    if record is None:
        raise FileNotFoundError(f"incident not found: {incident_id}")
    if new_status not in STATUSES:
        raise ValueError(f"unknown status: {new_status!r}")
    old_status = record.status
    record = IncidentRecord(**{
        **record.to_dict(),
        "status": new_status,
        "notified_at": notified_at or record.notified_at,
    })
    _write_incident(record)
    _emit("incident.status_changed", {
        "incident_id": record.incident_id,
        "old_status": old_status,
        "new_status": new_status,
    })
    return record


def close_incident(incident_id: str, *, tenant_id: str = "_default") -> IncidentRecord:
    """Mark incident closed, recording duration in hours."""
    record = _read_incident(tenant_id, incident_id)
    if record is None:
        raise FileNotFoundError(f"incident not found: {incident_id}")
    now = datetime.now(timezone.utc)
    detected = datetime.fromisoformat(record.detected_at)
    duration_hours = round((now - detected).total_seconds() / 3600, 1)
    record = IncidentRecord(**{
        **record.to_dict(),
        "status": "closed",
        "closed_at": now.isoformat(),
    })
    _write_incident(record)
    _emit("incident.closed", {
        "incident_id": record.incident_id,
        "duration_hours": duration_hours,
    })
    return record


def list_incidents(
    *,
    tenant_id: str = "_default",
    status: str | None = None,
) -> list[IncidentRecord]:
    """Return incidents, newest first, optionally filtered by status."""
    d = _incidents_dir(tenant_id)
    if not d.exists():
        return []
    records: list[IncidentRecord] = []
    for p in sorted(d.glob("*.json"), reverse=True):
        if p.name.startswith("."):
            continue
        try:
            r = IncidentRecord.from_dict(json.loads(p.read_text("utf-8")))
            if status is None or r.status == status:
                records.append(r)
        except Exception:
            continue
    return records


def load_incident(incident_id: str, *, tenant_id: str = "_default") -> IncidentRecord | None:
    return _read_incident(tenant_id, incident_id)


def export_incidents(*, tenant_id: str = "_default") -> list[dict[str, Any]]:
    """Export all incidents as dicts (includes description) for audit packages."""
    return [r.to_dict() for r in list_incidents(tenant_id=tenant_id)]


def scan_audit_chain(
    *,
    since_days: int = 30,
    tenant_id: str = "_default",
) -> list[dict[str, Any]]:
    """Batch scan: detect consent-bypass and disclosure failures in audit chain.

    Returns a list of potential incident descriptors (not yet opened).
    The caller is responsible for reviewing and calling open_incident() when
    the findings represent real failures.
    """
    try:
        from audit import audit_path  # type: ignore
    except ImportError:
        return []

    findings: list[dict[str, Any]] = []
    path = audit_path()
    if not path.exists():
        return []

    cutoff = datetime.now(timezone.utc).timestamp() - (since_days * 86400)
    seen_disclosed: set[str] = set()
    seen_consented: set[str] = set()

    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    entry = json.loads(line)
                except Exception:
                    continue
                ts = entry.get("timestamp") or entry.get("ts") or 0
                try:
                    ts_float = float(ts)
                except (TypeError, ValueError):
                    ts_float = 0.0
                if ts_float < cutoff:
                    continue
                evt = entry.get("event", "")
                uid = entry.get("user") or entry.get("uid") or ""
                if evt == "disclosure.shown":
                    seen_disclosed.add(uid)
                elif evt == "consent.granted":
                    seen_consented.add(uid)
                elif evt == "bridge.message_received":
                    if uid and uid not in seen_disclosed:
                        findings.append({
                            "potential_category": "disclosure_failure",
                            "uid_hash": uid[:8] + "…",
                            "event": evt,
                        })
                    if uid and uid not in seen_consented:
                        findings.append({
                            "potential_category": "consent_bypass",
                            "uid_hash": uid[:8] + "…",
                            "event": evt,
                        })
    except Exception:
        pass
    return findings


def notify_draft(
    incident_id: str,
    *,
    tenant_id: str = "_default",
    authority: str = "BSI",
    operator_name: str = "[OPERATOR: FILL IN]",
) -> str:
    """Generate an EU AI Act Art. 73 §2 notification draft in Markdown."""
    record = _read_incident(tenant_id, incident_id)
    if record is None:
        raise FileNotFoundError(f"incident not found: {incident_id}")

    return (
        f"# Serious Incident Notification — Art. 73 EU AI Act\n\n"
        f"**To:** {authority}  \n"
        f"**Operator:** {operator_name}  \n"
        f"**Incident ID:** `{record.incident_id}`  \n"
        f"**Detected at:** {record.detected_at}  \n"
        f"**Category:** {record.category}  \n"
        f"**Severity:** {record.severity}  \n"
        f"\n---\n\n"
        f"## 1. Description of the Incident\n\n"
        f"{record.description}\n\n"
        f"## 2. Affected System\n\n"
        f"Corvin — AI orchestration platform  \n"
        f"Deployment: [OPERATOR: describe deployment context]\n\n"
        f"## 3. Impact Assessment\n\n"
        f"[OPERATOR: describe impact on users, data subjects, fundamental rights]\n\n"
        f"## 4. Root Cause\n\n"
        f"[OPERATOR: describe root cause once determined]\n\n"
        f"## 5. Containment Steps Taken\n\n"
        f"[OPERATOR: list containment actions with timestamps]\n\n"
        f"## 6. Remediation and Preventive Controls\n\n"
        f"[OPERATOR: describe remediation steps and timeline]\n\n"
        f"## 7. Supporting Evidence\n\n"
        f"- Audit chain trigger: `{record.trigger_chain_hash}` ({record.trigger_event})\n"
        f"- Incident trail: `<tenant>/global/incidents/{record.incident_id}.json`\n\n"
        f"---\n\n"
        f"*Generated by `corvin-incident notify-draft`. "
        f"Complete all [OPERATOR: …] fields before submission.*\n"
    )


# ── Auto-Detection Hook ─────────────────────────────────────────────────────

class IncidentAutoDetector:
    """Called on every CRITICAL audit emit (PostAudit hook).

    When the emitted event type matches a TRIGGER_EVENTS entry, an incident
    is opened automatically.  The operator is responsible for review,
    containment, and authority notification — auto-detection only ensures no
    CRITICAL structural event goes untracked.

    Consent-bypass and disclosure-failure require batch scan (audit chain
    inspection) rather than per-event detection; use scan_audit_chain() on
    a scheduled basis (e.g. daily via systemd timer).
    """

    def on_audit_event(
        self,
        event: dict[str, Any],
        *,
        tenant_id: str = "_default",
    ) -> IncidentRecord | None:
        """Open incident if event matches a trigger category.  Returns record or None."""
        event_type = str(event.get("event", ""))
        category = TRIGGER_EVENTS.get(event_type)
        if category is None:
            return None
        if str(event.get("severity", "")).upper() != "CRITICAL":
            return None
        raw_hash = str(event.get("hash") or event.get("prev_hash") or "")
        # Sanitise: keep only lowercase hex chars, pad/truncate to 16
        chain_hash = re.sub(r"[^0-9a-f]", "0", raw_hash.lower())[:16].ljust(16, "0")
        try:
            return open_incident(
                category=category,
                trigger_event=event_type,
                trigger_chain_hash=chain_hash,
                description=(
                    f"Auto-detected from {event_type} CRITICAL event. "
                    f"Operator review required."
                ),
                severity="serious",
                tenant_id=tenant_id,
            )
        except Exception:
            return None
