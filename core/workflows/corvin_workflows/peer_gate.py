"""Outbound gate and audit helpers of the ``peer_agent`` node (ADR-2276).

Everything the node sends to a paired peer installation leaves this organisation, so the check
runs on OUR side, before anything is sent, on the exact payload:

1. L44 acceptable use (``spawn_gates.check_l44`` - fail-closed by contract);
2. the data grade of the payload (``data_classification.classify_task``): ``PUBLIC`` may go;
   anything above is refused unless the tenant policy releases that one endpoint
   (``spec.workflows.peer_policy.endpoints.<endpoint_id>.max_classification``: ``internal`` or
   ``confidential``; ``SECRET`` never). The L34 engine matrix is deliberately NOT used: a peer is
   not an engine of this installation and ``check_l34`` is fail-open without a tenant policy
   (ADR-2276 Appendix A);
3. the endpoint is paired (registry entry, enabled) and carries an instance pin.

Every failure of the gate itself refuses (fail-closed). The returned reason codes are a closed
set; neither refusal wording nor payload text ever reaches an audit record.
"""
from __future__ import annotations

import logging
import os
import sys
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
REASONS = frozenset({
    REASON_GATE_UNAVAILABLE, REASON_L44, REASON_ABOVE_PUBLIC, REASON_NOT_PAIRED, REASON_NOT_PINNED,
    REASON_AUDIT,
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


def evaluate_peer_outbound(
    instruction: str, tenant_id: str, endpoint_id: str, *, sender: Any = None,
) -> PeerGateDecision:
    """Run the outbound gate. Never raises; any internal failure is a refusal."""
    grade_name = ""
    try:
        _shared_on_path()
        import spawn_gates as _sg  # type: ignore[import-not-found]  # noqa: PLC0415
        import data_classification as _dc  # type: ignore[import-not-found]  # noqa: PLC0415

        hr = _sg.check_l44(
            instruction, tenant_id, persona="assistant", channel=GATE_CHANNEL,
            chat_key=GATE_CHANNEL, engine_id=GATE_ENGINE_ID,
        )
        if hr is not None:
            return PeerGateDecision(False, REASON_L44, grade_name)

        grade = _dc.classify_task(instruction, persona="assistant")
        grade_name = grade.name
        released = None
        if grade > _dc.DataClassification.PUBLIC:
            released = _released_grade(tenant_id, endpoint_id)

        reg = getattr(sender, "_registry", None)
        if reg is None:
            import remote_trigger_sender as _rts  # type: ignore[import-not-found]  # noqa: PLC0415

            reg = _rts.RemoteEndpointRegistry()
        try:
            cfg = reg.load(endpoint_id)
        except Exception:  # noqa: BLE001 - unknown / disabled / unreadable endpoint
            return PeerGateDecision(False, REASON_NOT_PAIRED, grade_name)
        if not str(cfg.get("instance_id") or "").strip():
            return PeerGateDecision(False, REASON_NOT_PINNED, grade_name)

        if grade > _dc.DataClassification.PUBLIC and (released is None or grade > released):
            return PeerGateDecision(False, REASON_ABOVE_PUBLIC, grade_name)
        return PeerGateDecision(True, "", grade_name)
    except Exception as exc:  # noqa: BLE001 - any gate error refuses, never allows
        _log.error("[workflow-peer] outbound gate error (%s) - refused (fail-closed)", type(exc).__name__)
        return PeerGateDecision(False, REASON_GATE_UNAVAILABLE, grade_name)


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
