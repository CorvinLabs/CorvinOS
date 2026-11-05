"""Outbound gate and audit helpers of the ``peer_agent`` node (ADR-2276).

Everything the node sends to a paired peer installation leaves this organisation, so the check
runs on OUR side, before anything is sent, on the exact payload - normalised first (NFKC, format
and control characters removed), and that normalised text is the one that is sent (P-01):

1. L44 acceptable use (``spawn_gates.check_l44`` - fail-closed by contract);
2. the data grade of the payload (``data_classification.classify_task``; a ``[class:...]`` marker
   may only raise it, never lower it - P-02): ``PUBLIC`` may go;
   anything above is refused unless the tenant policy releases that one endpoint
   (``spec.workflows.peer_policy.endpoints.<endpoint_id>.max_classification``: ``internal`` or
   ``confidential``; ``SECRET`` never) AND the operator stated the endpoint's GDPR role
   (ADR-2294: role ``unset`` refuses a released grade with ``endpoint_role_unset``). The L34 engine matrix is deliberately NOT used: a peer is
   not an engine of this installation and ``check_l34`` is fail-open without a tenant policy
   (ADR-2276 Appendix A);
3. the endpoint is paired (registry entry, enabled) and carries an instance pin.

Every failure of the gate itself refuses (fail-closed). The returned reason codes are a closed
set; neither refusal wording nor payload text ever reaches an audit record.
"""
from __future__ import annotations

import logging
import os
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .node_types import PeerDelegationError, _federation_module

_log = logging.getLogger(__name__)

REASON_GATE_UNAVAILABLE = "gate_unavailable"
REASON_L44 = "l44_refused"
REASON_ABOVE_PUBLIC = "classified_above_public"
REASON_NOT_PAIRED = "endpoint_not_paired"
REASON_NOT_PINNED = "endpoint_not_pinned"
REASON_AUDIT = "audit_unavailable"
# ADR-2294: the grade is released by policy, but the operator has not stated the endpoint's GDPR role.
REASON_ROLE_UNSET = "endpoint_role_unset"
REASONS = frozenset({
    REASON_GATE_UNAVAILABLE, REASON_L44, REASON_ABOVE_PUBLIC, REASON_NOT_PAIRED, REASON_NOT_PINNED,
    REASON_AUDIT, REASON_ROLE_UNSET,
})

GATE_CHANNEL = "workflow-peer"
GATE_ENGINE_ID = "peer_agent"
# Grades a tenant may release toward a peer. SECRET (credential patterns) never.
_RELEASABLE = ("internal", "confidential")


@dataclass(frozen=True)
class PeerGateDecision:
    allowed: bool
    reason_code: str  # "" when allowed
    data_class: str  # grade name of the payload ("" when it could not be determined)
    payload: str = ""  # the EXACT text that was judged - and the only text that may be sent (P-01)


_MARKER_RE = re.compile(r"^\s*\[class:(public|internal|confidential|secret)\]\s*", re.IGNORECASE)


def normalize_outbound(text: str) -> str:
    """The text as the peer will read it: NFKC (fullwidth/compatibility forms collapse to ASCII)
    with format, control, surrogate, private-use and unassigned characters removed (zero-width,
    soft hyphen, bidi), except tab, newline and carriage return. A secret only disguised by such
    characters is then judged in its collapsed form - and this collapsed text is what is sent, so
    the gate's verdict and the bytes that leave are the same thing (P-01)."""
    out = unicodedata.normalize("NFKC", text)
    return "".join(
        ch for ch in out
        if ch in "\t\n\r" or unicodedata.category(ch) not in ("Cf", "Cc", "Cs", "Co", "Cn")
    )


def _shared_on_path() -> None:
    d = os.path.dirname(os.path.abspath(__file__))
    while d != os.path.dirname(d):
        cand = os.path.join(d, "corvin_operator", "bridges", "shared")
        if os.path.isdir(cand):
            if cand not in sys.path:
                sys.path.insert(0, cand)
            return
        d = os.path.dirname(d)


def _tenant_policy_file(tenant_id: str) -> Path:
    _federation_module("audit")  # puts the repository root on sys.path when needed
    from core.paths import tenant_home  # noqa: PLC0415
    from core.tenants.validation import validate_tenant_id  # noqa: PLC0415

    return tenant_home(validate_tenant_id(tenant_id)) / "global" / "tenant.corvin.yaml"


def _released_grade(tenant_id: str, endpoint_id: str) -> Any:
    """Highest grade the tenant releases toward ``endpoint_id`` as a ``DataClassification``, or
    ``None`` (nothing released). Raises on an unreadable policy file - the caller refuses then."""
    path = _tenant_policy_file(tenant_id)
    if not path.is_file():
        return None
    import yaml  # noqa: PLC0415 - absent PyYAML + present policy file => refuse (caller catches)

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise ValueError("tenant policy is not a mapping")
    spec = raw.get("spec")
    wf = spec.get("workflows") if isinstance(spec, dict) else None
    pol = wf.get("peer_policy") if isinstance(wf, dict) else None
    eps = pol.get("endpoints") if isinstance(pol, dict) else None
    entry = eps.get(endpoint_id) if isinstance(eps, dict) else None
    value = entry.get("max_classification") if isinstance(entry, dict) else None
    if not isinstance(value, str) or value.strip().lower() not in _RELEASABLE:
        return None  # absent, malformed or "secret": nothing released
    from data_classification import DataClassification  # type: ignore[import-not-found]  # noqa: PLC0415

    return DataClassification[value.strip().upper()]


def _endpoint_registry(sender: Any = None) -> Any:
    reg = getattr(sender, "_registry", None)
    if reg is None:
        _shared_on_path()
        import remote_trigger_sender as _rts  # type: ignore[import-not-found]  # noqa: PLC0415

        reg = _rts.RemoteEndpointRegistry()
    return reg


def endpoint_is_paired(endpoint_id: str, sender: Any = None) -> bool:
    """True when ``endpoint_id`` is a paired, enabled endpoint of this installation. Reads only;
    used by the synthetic readiness check, which never sends. Never raises."""
    try:
        _endpoint_registry(sender).load(endpoint_id)
        return True
    except Exception:  # noqa: BLE001
        return False


def evaluate_peer_outbound(
    instruction: str, tenant_id: str, endpoint_id: str, *, sender: Any = None,
) -> PeerGateDecision:
    """Run the outbound gate. Never raises; any internal failure is a refusal."""
    grade_name = ""
    try:
        _shared_on_path()
        import spawn_gates as _sg  # type: ignore[import-not-found]  # noqa: PLC0415
        import data_classification as _dc  # type: ignore[import-not-found]  # noqa: PLC0415

        payload = normalize_outbound(instruction)
        hr = _sg.check_l44(
            payload, tenant_id, persona="assistant", channel=GATE_CHANNEL,
            chat_key=GATE_CHANNEL, engine_id=GATE_ENGINE_ID,
        )
        if hr is not None:
            return PeerGateDecision(False, REASON_L44, grade_name)

        # P-02: a `[class:...]` marker may only RAISE the grade. The content is judged without it
        # (secret patterns, personal data) and the stricter of the two counts; otherwise a leading
        # `[class:public]` - writable by any upstream node - downgraded personal data to PUBLIC.
        marker = _MARKER_RE.match(payload)
        content = payload[marker.end():] if marker else payload
        grade = _dc.classify_task(content, persona="assistant")
        if marker:
            grade = max(grade, _dc.DataClassification[marker.group(1).upper()])
        grade_name = grade.name
        released = None
        if grade > _dc.DataClassification.PUBLIC:
            released = _released_grade(tenant_id, endpoint_id)

        try:
            cfg = _endpoint_registry(sender).load(endpoint_id)
        except Exception:  # noqa: BLE001 - unknown / disabled / unreadable endpoint
            return PeerGateDecision(False, REASON_NOT_PAIRED, grade_name)
        if not str(cfg.get("instance_id") or "").strip():
            return PeerGateDecision(False, REASON_NOT_PINNED, grade_name)

        if grade > _dc.DataClassification.PUBLIC and (released is None or grade > released):
            return PeerGateDecision(False, REASON_ABOVE_PUBLIC, grade_name)
        # ADR-2294: a released grade still needs the operator's statement of the endpoint's GDPR role.
        import endpoint_role as _er  # type: ignore[import-not-found]  # noqa: PLC0415

        if grade > _dc.DataClassification.PUBLIC and _er.role_of(cfg) == _er.ROLE_UNSET:
            return PeerGateDecision(False, REASON_ROLE_UNSET, grade_name)
        return PeerGateDecision(True, "", grade_name, payload)
    except Exception as exc:  # noqa: BLE001 - any gate error refuses, never allows
        _log.error("[workflow-peer] outbound gate error (%s) - refused (fail-closed)", type(exc).__name__)
        return PeerGateDecision(False, REASON_GATE_UNAVAILABLE, grade_name)


_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,128}$")


def _claim_path(tenant_id: str, task_id: str, after: dict | None) -> Path:
    if not _TASK_ID_RE.match(task_id or ""):
        raise ValueError("invalid task id")
    name = task_id
    if after is not None:
        # A retry after a `rejected` row claims THAT generation: every runner that read the same
        # rejected row computes the same name, so exactly one of them wins it (an unlink of the
        # old claim followed by a create let two racing retriers both win).
        import hashlib  # noqa: PLC0415
        import json  # noqa: PLC0415

        gen = hashlib.sha256(json.dumps(after, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:16]
        name = f"{task_id}.{gen}"
    return _tenant_policy_file(tenant_id).parent / "federation" / "peer_claims" / name


def claim_task(tenant_id: str, task_id: str, *, after: dict | None = None) -> bool:
    """Exclusive per-task send claim (exactly-once, P-05). True when the caller now owns the send.

    The ledger row of a task only appears inside ``delegate()``; between a runner's ledger read
    and that row a second runner of the same (run, node) would also see "nothing sent". The claim
    file, created with ``O_EXCL`` right before the send, closes that window. ``after`` is the
    prior ``rejected`` ledger row a retry follows (the peer refused before running anything).
    Raises on an unusable claim directory (the caller refuses)."""
    path = _claim_path(tenant_id, task_id, after)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return False
    os.close(fd)
    return True


def release_task_claim(tenant_id: str, task_id: str, *, after: dict | None = None) -> None:
    """Give a claim back when the send provably did not happen (the audit-first record could not
    be written). Never called once ``delegate()`` was entered."""
    _claim_path(tenant_id, task_id, after).unlink(missing_ok=True)


def audit_peer_event(event: str, tenant_id: str, *, strict: bool = False, **fields: Any) -> bool:
    """Write one ``workflow.peer_*`` record to the tenant chain. Scalars only.

    ``strict`` (the record that must precede a send): a failure raises ``PeerDelegationError`` so
    nothing leaves. Otherwise a failure is logged and reported as ``False`` - the outcome stands.
    """
    try:
        fa = _federation_module("audit")
        from core.paths import tenant_audit_chain  # noqa: PLC0415

        payload = {k: v for k, v in fields.items() if v is not None}
        payload["tenant_id"] = tenant_id

        path = tenant_audit_chain(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = fa._core_write_event()(path, event, details=payload)
        if not (isinstance(record, dict) and record.get("hash")):
            raise RuntimeError("no chained record")
        return True
    except Exception as exc:  # noqa: BLE001
        _log.error("[workflow-peer] audit write %s failed (%s)", event, type(exc).__name__)
        if strict:
            raise PeerDelegationError(REASON_AUDIT) from exc
        return False
