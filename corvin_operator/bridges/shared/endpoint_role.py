"""Organisation label and GDPR role of a paired A2A endpoint (ADR-2294).

The endpoint record (``remote_endpoints/<endpoint_id>.json``) carries two operator-stated fields:

* ``organisation`` - display string, 1 to 80 characters, no markup;
* ``gdpr_role`` - one of ``controller``, ``processor``, ``joint`` or ``unset``; absent means ``unset``.

They are written ONLY by :func:`set_endpoint_role`, which the console route
(``PUT /v1/console/remote-trigger/endpoints/{id}/gdpr-role``) and the CLI (``corvin-a2a set-role``) call.
Every other writer of endpoint records (pairing, re-pairing, the receiver's reconnect handler, the relay)
never names these keys and never merges peer-supplied dicts, so a peer cannot set or change its own label
or role (``test_endpoint_gdpr_role.py`` pins that structurally).

The role is the operator's statement, never derived: nothing here reads the peer's catalogue, URL,
label or name to guess one, and the default is ``unset`` - never ``processor``. No legal analysis is
generated; the audit chain records who stated which role and when (``federation.endpoint_role_set``:
endpoint id, role, actor - never the organisation text or a justification).

The outbound rule (:func:`outbound_refusal`): anything classified above ``PUBLIC`` toward an endpoint
whose role is ``unset`` is refused with the closed reason ``endpoint_role_unset``, even when the
workflow ``peer_policy`` released the grade. Public data is unaffected. It runs in
``RemoteTriggerSender.send`` (the call every outbound A2A path passes) and in the workflow
``peer_agent`` gate (``peer_gate.evaluate_peer_outbound``), and before the audit-first record of
``core.federation.delegation.delegate``.
"""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any, Callable

GDPR_ROLES: tuple[str, ...] = ("controller", "processor", "joint", "unset")
ROLE_UNSET = "unset"
REASON_ROLE_UNSET = "endpoint_role_unset"
ORGANISATION_MAX_CHARS = 80
AUDIT_EVENT = "federation.endpoint_role_set"
#: How the Art. 30 record prints an unset role - a statement that the decision is pending, not a guess.
UNSET_RECORD_TEXT = "unset - operator decision pending"

# Characters that carry markup in the surfaces that show the label (HTML, Markdown, the PDF record).
_MARKUP_CHARS = frozenset("<>`[]{}|\\")
# actor: the surface that made the statement plus an opaque id (console session fingerprint).
_ACTOR_RE = re.compile(r"^(console|cli)(:[A-Za-z0-9_\-]{1,64})?$")

_REFUSAL_TEXT = (
    "Refused: this connection has no GDPR role yet, so data classified above public is not sent to it. "
    "State the partner's organisation and GDPR role (controller, processor or joint) in the console "
    "under Agent Hub > Peers > outbound endpoint > GDPR role, or with "
    "'corvin-a2a set-role <endpoint> --organisation <name> --role <role>'. Public data still goes."
)


class EndpointRoleError(ValueError):
    """A value or request the operator writer refuses (invalid organisation, role, actor or id)."""


class EndpointNotFound(EndpointRoleError):
    """No endpoint record with this id."""


# ── values ────────────────────────────────────────────────────────────────

def normalise_role(value: Any) -> str:
    """The role as stored, or :class:`EndpointRoleError`. Exact lower-case values only."""
    if not isinstance(value, str) or value not in GDPR_ROLES:
        raise EndpointRoleError("gdpr_role must be one of: controller, processor, joint, unset")
    return value


def normalise_organisation(value: Any) -> str:
    """NFC, trimmed, 1 to 80 characters, no control/format characters, no markup characters."""
    if not isinstance(value, str):
        raise EndpointRoleError("organisation must be text")
    text = unicodedata.normalize("NFC", value).strip()
    if not 1 <= len(text) <= ORGANISATION_MAX_CHARS:
        raise EndpointRoleError(f"organisation must be 1 to {ORGANISATION_MAX_CHARS} characters")
    for ch in text:
        if unicodedata.category(ch).startswith("C"):
            raise EndpointRoleError("organisation must not contain control or formatting characters")
        if ch in _MARKUP_CHARS:
            raise EndpointRoleError("organisation must not contain markup characters (< > ` [ ] { } | \\)")
    return text


def role_of(cfg: Any) -> str:
    """The stated role of an endpoint record; ``unset`` for anything else. Never inferred."""
    value = cfg.get("gdpr_role") if isinstance(cfg, dict) else None
    return value if isinstance(value, str) and value in GDPR_ROLES else ROLE_UNSET


def organisation_of(cfg: Any) -> str:
    """The stated organisation, or ``""`` when absent or not a valid stored value."""
    value = cfg.get("organisation") if isinstance(cfg, dict) else None
    try:
        return normalise_organisation(value)
    except EndpointRoleError:
        return ""


def refusal_text() -> str:
    """The fixed operator-facing text of an ``endpoint_role_unset`` refusal."""
    return _REFUSAL_TEXT


# ── the outbound rule ─────────────────────────────────────────────────────

def outbound_refusal(cfg: Any, instruction: str) -> tuple[str | None, str]:
    """``(reason, grade_name)``: ``reason`` is ``endpoint_role_unset`` or ``None`` (this rule allows).

    Classification is the same heuristic the workflow gate uses (``data_classification.classify_task``).
    A classifier failure refuses where the rule can apply (role ``unset``) - fail-closed - and changes
    nothing for an endpoint with a stated role.
    """
    role = role_of(cfg)
    try:
        import data_classification as _dc  # type: ignore[import-not-found]  # noqa: PLC0415

        grade = _dc.classify_task(instruction or "", persona="assistant")
        grade_name = grade.name
        above_public = grade > _dc.DataClassification.PUBLIC
    except Exception:  # noqa: BLE001 - cannot prove the payload is public
        return (REASON_ROLE_UNSET if role == ROLE_UNSET else None), ""
    if above_public and role == ROLE_UNSET:
        return REASON_ROLE_UNSET, grade_name
    return None, grade_name


# ── reading every endpoint (Art. 30 record) ───────────────────────────────

def default_endpoints_dir() -> Path:
    env = os.environ.get("REMOTE_ENDPOINTS_DIR")
    if env:
        return Path(env)
    import remote_trigger_sender as _rts  # type: ignore[import-not-found]  # noqa: PLC0415

    return Path(_rts._REMOTE_ENDPOINTS_DEFAULT)


def read_endpoint_roles(endpoints_dir: Path | str | None = None) -> dict[str, tuple[str, str]]:
    """``{endpoint_id: (organisation, role)}`` for every endpoint file, enabled or not. Read-only;
    an unreadable file is listed with no organisation and role ``unset``."""
    d = Path(endpoints_dir) if endpoints_dir is not None else default_endpoints_dir()
    out: dict[str, tuple[str, str]] = {}
    if not d.is_dir():
        return out
    for path in sorted(d.glob("*.json")):
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            cfg = {}
        out[path.stem] = (organisation_of(cfg), role_of(cfg))
    return out


# ── the operator writer ───────────────────────────────────────────────────

def _valid_endpoint_id(endpoint_id: Any) -> bool:
    return (isinstance(endpoint_id, str) and bool(endpoint_id) and "/" not in endpoint_id
            and "\\" not in endpoint_id and ":" not in endpoint_id and not endpoint_id.startswith(".")
            and len(endpoint_id) <= 128)


def _default_emit() -> Callable[..., Any]:
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from core.federation import audit as _fa  # noqa: PLC0415

    return _fa.emit


def _atomic_write(path: Path, cfg: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(cfg, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        if not sys.platform.startswith("win"):
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def set_endpoint_role(
    endpoint_id: str, *, organisation: Any, gdpr_role: Any, actor: str, tenant_id: str,
    endpoints_dir: Path | str | None = None, emit: Callable[..., Any] | None = None,
) -> dict[str, str]:
    """Record the operator's statement of an endpoint's organisation and GDPR role.

    Audit-first: ``federation.endpoint_role_set`` (endpoint id, role, actor, tenant) must commit before
    the record changes; if it does not, nothing is written and :class:`EndpointRoleError` is raised.
    The read-modify-write runs under the cross-process A2A config lock and keeps every other key.
    """
    org = normalise_organisation(organisation)
    role = normalise_role(gdpr_role)
    if not isinstance(actor, str) or not _ACTOR_RE.match(actor):
        raise EndpointRoleError("actor must be 'console:<id>' or 'cli'")
    if not _valid_endpoint_id(endpoint_id):
        raise EndpointRoleError("invalid endpoint_id")
    d = Path(endpoints_dir) if endpoints_dir is not None else default_endpoints_dir()
    path = d / f"{endpoint_id}.json"
    writer = emit if emit is not None else _default_emit()

    import a2a_friendship as _ft  # type: ignore[import-not-found]  # noqa: PLC0415

    with _ft.config_file_lock(d):
        if not path.is_file():
            raise EndpointNotFound(f"unknown endpoint: {endpoint_id}")
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise EndpointRoleError("endpoint record unreadable") from exc
        if not isinstance(cfg, dict) or cfg.get("endpoint_id") != endpoint_id:
            raise EndpointRoleError("endpoint record does not match its id")
        try:
            writer(AUDIT_EVENT, tenant_id=tenant_id, endpoint_id=endpoint_id, gdpr_role=role, actor=actor)
        except Exception as exc:  # noqa: BLE001 - not recorded => not changed
            raise EndpointRoleError("the audit record could not be written; nothing was changed") from exc
        cfg["organisation"] = org
        cfg["gdpr_role"] = role
        _atomic_write(path, cfg)
    return {"endpoint_id": endpoint_id, "organisation": org, "gdpr_role": role}


__all__ = [
    "AUDIT_EVENT", "EndpointNotFound", "EndpointRoleError", "GDPR_ROLES", "ORGANISATION_MAX_CHARS",
    "REASON_ROLE_UNSET", "ROLE_UNSET", "UNSET_RECORD_TEXT", "normalise_organisation", "normalise_role",
    "organisation_of", "outbound_refusal", "read_endpoint_roles", "refusal_text", "role_of",
    "set_endpoint_role",
]
