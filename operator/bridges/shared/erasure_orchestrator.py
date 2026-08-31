"""erasure_orchestrator.py — Layer 36: GDPR Art. 17 Erasure Orchestrator.

ADR-0045 (companion to ADR-0041 / ADR-0042 / ADR-0043 / ADR-0044). Cross-layer
right-to-deletion. Coordinates per-layer purge handlers so a single
``corvin-erasure <subject_id>`` call cleans every store that holds
data about the subject.

Design tension (resolved here):

  GDPR Art. 17 grants a "right to erasure" but Art. 17(3)(b) carves
  out "compliance with a legal obligation" — audit logs typically
  fall under this. The L16 hash chain is structurally tamper-evident;
  rewriting sealed segments to remove a ``user_id`` string would
  break the chain or require re-signing every subsequent segment.

  **Corvin resolution:** user identifiers in the audit chain are
  **pseudonymous** by design (opaque strings; the audit allow-list
  forbids real PII in details). The structural Art. 17 mechanism is
  to delete the *identity mapping* (the link from pseudonym to
  real-world identity) and to delete the *content stores* (recall
  text, artifacts, skill-forge user data). The audit chain itself
  is preserved — once the mapping is gone, the chain's
  ``user_id="user_42"`` is no longer traceable to a person.

  This matches the EDPB guidance on pseudonymisation as a sufficient
  Art. 17 measure when full deletion conflicts with Art. 30 / 32
  obligations.

Architecture:

  * :class:`ErasureRequest` — request id, subject id, requester
    (operator / DPO who initiated), timestamp, scope.
  * :class:`ErasureHandler` Protocol — one per layer that holds
    subject data. Each handler returns an :class:`ErasureLayerResult`.
  * :class:`ErasureOrchestrator` — registers handlers, walks them
    in declared order, persists the plan + per-layer results to
    ``<tenant>/global/erasure/<request_id>.json``.
  * Audit events on L16 hash chain (allow-list enforced):
    - ``erasure.requested`` (WARNING) — emitted FIRST, before any
      handler runs
    - ``erasure.applied`` (INFO) — per layer, on each successful purge
    - ``erasure.skipped`` (INFO) — per layer, when handler finds nothing
    - ``erasure.failed`` (CRITICAL) — per layer, on handler exception
    - ``erasure.completed`` (WARNING) — emitted LAST, with overall
      status

  Layers expected to register handlers:
    - L7  skill-forge — user-scope skill purge
    - L24 data-snapshot — purge snapshots that referenced the subject
    - L28 recall — DELETE FROM turns WHERE user_id = ?
    - L33 artifacts — unpinned purge; pinned require operator ACK
    - L16/L34/L35/L37 audit chain — **identity-mapping delete only**
      (chain content preserved per the resolution above)

  L36 ships built-in stub handlers that audit-only no-op; real
  implementations land in the respective layers as follow-ups.

CI lint: module MUST NOT ``import anthropic``. Audit details
allow-list enforced at emission time. Cross-tenant erasure refused
(``ErasureScopeError``).
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import re
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Protocol

# ── ADR-0141 Tier 3 — self-register this security capability at import time ──
try:  # pragma: no cover - exercised at adapter boot / self-test
    from security_capabilities import (  # noqa: E402
        register_capability as _reg_cap,
        module_self_hash as _self_hash,
    )

    _reg_cap("erasure_orchestrator", version="1.0", file_hash=_self_hash(__file__))
except Exception:  # pragma: no cover - fail-closed: absent capability blocks spawn
    pass

# ----- types ---------------------------------------------------------

class LayerStatus(str, Enum):
    """Per-layer erasure outcome."""
    APPLIED = "applied"   # handler made changes
    SKIPPED = "skipped"   # handler found nothing for this subject
    FAILED = "failed"     # handler raised


class OverallStatus(str, Enum):
    """Aggregate erasure outcome."""
    COMPLETED = "completed"   # every handler returned APPLIED or SKIPPED
    PARTIAL = "partial"       # at least one handler FAILED, others succeeded
    FAILED = "failed"         # every handler FAILED (or no handlers registered)


class ReasonCode(str, Enum):
    """Controlled vocabulary for the audit-bound per-layer reason.

    GDPR Art. 5 / CLAUDE.md L36 "metadata-only is load-bearing": the
    free-form :attr:`ErasureLayerResult.reason` may legitimately carry
    absolute filesystem paths and raw exception text (handlers build it
    for the operator-facing trail file, which is 0600 and permitted to
    hold free-form notes). That text MUST NOT reach the L16 hash chain.

    The orchestrator therefore audit-emits ONLY this controlled CODE,
    never the free-form ``reason``. The code is a closed enum — there is
    no path, no exception text, no caller-supplied string that can ride
    in on it. Handlers SHOULD set :attr:`ErasureLayerResult.code`; when a
    handler leaves it blank the orchestrator derives a safe code from the
    layer status (see :func:`_derive_reason_code`).
    """
    DELETED = "deleted"                 # APPLIED — content was removed
    STORE_ABSENT = "store_absent"       # SKIPPED — store/file/dir not present
    STORE_EMPTY = "store_empty"         # SKIPPED — store present but nothing matched
    NOT_APPLICABLE = "not_applicable"   # SKIPPED — documented stub / no concrete handler
    STORE_ERROR = "store_error"         # FAILED — handler raised / infra error
    HANDLER_CONTRACT_ERROR = "handler_contract_error"  # FAILED — bad return / mis-attribution


# The closed set of legal audit-bound reason codes (string values).
_REASON_CODES: frozenset[str] = frozenset(rc.value for rc in ReasonCode)


def _derive_reason_code(status: "LayerStatus", code: str | None) -> str:
    """Map a per-layer result to a safe controlled-vocabulary code.

    If the handler supplied an explicit code that is in the closed
    vocabulary, it is used verbatim. Otherwise a conservative default is
    derived purely from the layer status — NEVER from the free-form
    ``reason`` — so no path/exception text can leak through this path.
    """
    if code and code in _REASON_CODES:
        return code
    if status == LayerStatus.APPLIED:
        return ReasonCode.DELETED.value
    if status == LayerStatus.SKIPPED:
        # Without an explicit handler signal we cannot distinguish
        # absent / empty / n-a; "store_empty" is the safe, neutral default.
        return ReasonCode.STORE_EMPTY.value
    # FAILED (or any unknown status) — never APPLIED/SKIPPED.
    return ReasonCode.STORE_ERROR.value


# Subject-id shape — operator-chosen pseudonymous identifier.
# Allowed: alphanumeric + dash/underscore + dot + colon, 1–128 chars.
# Forbidden: path separators, control chars, whitespace, @-sign,
# spaces, slashes. The colon is admitted so "channel:chat_key" style
# identifiers work directly — the email-shape check still rejects
# PII because "@" is not in the allowed set.
_SUBJECT_ID_RE = re.compile(r"^[A-Za-z0-9.:_\-]{1,128}$")


def validate_subject_id(subject_id: str) -> str:
    """Sieve the subject id. Returns the canonical form (the input
    unchanged on validation pass); raises :class:`ValueError` on
    shape failure.

    Conservative shape — operators sometimes pass raw email or
    other PII as subject_id; this regex forces them to use a
    pseudonym (or hash-of-email) first.

    Uses :func:`re.fullmatch` (NOT :func:`re.match`) so the whole
    string must match end-to-end. ``re.match`` with a trailing ``$``
    would accept a trailing newline (``"abc\\n"`` matches because ``$``
    also matches just before a final ``\\n``). subject_id keys the
    pseudonymisation, so a newline-variant could mint a distinct,
    un-erasable identity — it must be rejected.
    """
    if not isinstance(subject_id, str):
        raise ValueError(f"subject_id must be str, got {type(subject_id).__name__}")
    if not _SUBJECT_ID_RE.fullmatch(subject_id):
        raise ValueError(
            f"subject_id {subject_id!r} fails shape check "
            f"{_SUBJECT_ID_RE.pattern} — use a pseudonymous identifier"
        )
    return subject_id


# ----- request / result dataclasses ---------------------------------

@dataclass(frozen=True)
class ErasureRequest:
    """Input to :meth:`ErasureOrchestrator.execute`.

    ``scope`` is a free-form operator hint ("all" / "session-only" /
    custom) that handlers can read; the orchestrator does not enforce
    semantics on it — it's passed through.
    """
    subject_id: str
    requester: str
    request_id: str = field(default_factory=lambda: f"er-{uuid.uuid4().hex[:12]}")
    ts: float = field(default_factory=time.time)
    scope: str = "all"
    notes: str = ""
    tenant_id: str | None = None
    """Optional tenant scope. When set, :meth:`ErasureOrchestrator.execute`
    verifies it matches the orchestrator's own ``tenant_id`` and raises
    :class:`ErasureScopeError` on mismatch. Leave ``None`` for legacy
    callers that pre-date the structural tenant check.
    """

    def __post_init__(self) -> None:
        validate_subject_id(self.subject_id)
        if not isinstance(self.requester, str) or not self.requester.strip():
            raise ValueError("requester must be a non-empty string")
        if not isinstance(self.scope, str) or not self.scope:
            raise ValueError("scope must be a non-empty string")


@dataclass(frozen=True)
class ErasureLayerResult:
    """One handler's outcome.

    ``reason`` is FREE-FORM and operator-facing — it MAY contain absolute
    filesystem paths and raw exception text. It is written ONLY to the
    0600 trail file (which L36 permits to hold free-form notes); it is
    NEVER emitted to the L16 audit chain.

    ``code`` is the audit-bound machine-readable :class:`ReasonCode`
    value (controlled vocabulary). The orchestrator emits this code — and
    only this code — to the hash chain. Handlers SHOULD set it; when left
    blank the orchestrator derives a safe code from ``status``.
    """
    layer_id: str
    status: LayerStatus
    count: int = 0
    reason: str = ""
    code: str = ""
    duration_ms: int = 0


@dataclass
class ErasureResult:
    """Aggregate result returned by :meth:`ErasureOrchestrator.execute`."""
    request: ErasureRequest
    started_at: float
    completed_at: float = 0.0
    per_layer: list[ErasureLayerResult] = field(default_factory=list)
    overall_status: OverallStatus = OverallStatus.COMPLETED

    @property
    def applied_count(self) -> int:
        return sum(1 for r in self.per_layer if r.status == LayerStatus.APPLIED)

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.per_layer if r.status == LayerStatus.FAILED)

    def to_dict(self) -> dict[str, Any]:
        d = {
            "request": asdict(self.request),
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "overall_status": self.overall_status.value,
            "per_layer": [
                {
                    "layer_id": r.layer_id,
                    "status": r.status.value,
                    "count": r.count,
                    # Trail file (0600) keeps BOTH the free-form reason
                    # (paths/exceptions for the operator/DPO) AND the
                    # audit-bound controlled code, so the operator can
                    # correlate a chain entry to its full descriptor.
                    "reason": r.reason,
                    "code": _derive_reason_code(r.status, r.code),
                    "duration_ms": r.duration_ms,
                }
                for r in self.per_layer
            ],
        }
        return d


# ----- handler protocol ---------------------------------------------

class ErasureHandler(Protocol):
    """One per layer. ``purge`` returns a :class:`ErasureLayerResult`
    summarising what the handler did. Handlers MUST NOT raise on the
    common cases (subject not found, layer disabled); only on truly
    exceptional failures (DB unreachable, permission denied)."""

    layer_id: str
    """Stable identifier used in audit events and the per-request file."""

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult: ...


# ----- audit allow-list ---------------------------------------------

_AUDIT_ALLOWED: frozenset[str] = frozenset({
    "request_id",
    "subject_id",
    "requester",
    "scope",
    "layer_id",
    "status",
    "count",
    # Controlled-vocabulary code replaces the old free-form `reason`.
    # `reason` is DELIBERATELY no longer in the allow-list: it can carry
    # absolute paths + raw exception text and must stay out of the L16
    # hash chain (GDPR Art. 5 / CLAUDE.md L36 metadata-only).
    "code",
    "duration_ms",
    "overall_status",
    "applied_count",
    "failed_count",
    # Bare exception CLASS name (e.g. "OSError") for the trail_failed
    # event — never the exception message/path. Validated to a strict
    # identifier shape below.
    "error_type",
})

# Detail keys whose VALUE must be a member of the closed reason-code
# vocabulary. Defence-in-depth: even if a caller smuggled free text into
# `code`, the value gate below rejects it.
_CODED_VALUE_KEYS: frozenset[str] = frozenset({"code"})

# Detail keys that hold a Python class name (identifier shape). Exempt
# from the exception-hint scan because a class name like "OSError"
# legitimately contains "Error"; instead they must match a strict
# identifier pattern (no paths, no whitespace, no message text).
_IDENTIFIER_VALUE_KEYS: frozenset[str] = frozenset({"error_type"})
_IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_.]{0,127}")

# Caller-supplied identity / hint keys that have ALWAYS been free-form by
# design (the operator/DPO chooses them). They are exempt from the
# path/exception heuristic to avoid false-positives (e.g. a requester
# "team/ops" or scope "session-only"). They are still length-bounded and
# subject_id additionally passes validate_subject_id() at request build.
# The path/exception scrubber therefore targets the leak vector the issue
# names — handler-derived descriptors — on any OTHER (unexpected) key.
_FREEFORM_IDENTITY_KEYS: frozenset[str] = frozenset({
    "request_id", "subject_id", "requester", "scope",
})

# Defence-in-depth value scrubber. The structural guarantee is that the
# orchestrator only ever emits `code` (a closed enum) and never `reason`;
# this scrubber is a fail-closed backstop that rejects any audit detail
# value carrying a filesystem path separator or exception-shaped text,
# regardless of which key it rides on. A leak into the tamper-evident
# chain is irreversible, so the gate fails CLOSED (raises) rather than
# best-effort dropping.
_PATH_HINT_RE = re.compile(r"(?:/|\\)")  # any path separator
_EXC_HINT_RE = re.compile(
    r"(?:Error|Exception|Traceback|errno|No such file|Permission denied)",
    re.IGNORECASE,
)


def _assert_safe_audit_value(key: str, value: Any) -> None:
    """Fail-closed scrubber for a single audit detail value.

    Non-string scalars (int/float/bool/None) are always safe. For string
    values:

      * keys in :data:`_CODED_VALUE_KEYS` must be a member of the closed
        :class:`ReasonCode` vocabulary (no free text at all);
      * any other string must not contain a path separator or
        exception-shaped substring — those signal a leaked path/exception
        and must never enter the hash chain.
    """
    if not isinstance(value, str):
        return
    if key in _CODED_VALUE_KEYS:
        if value not in _REASON_CODES:
            raise ValueError(
                f"erasure audit detail '{key}'={value!r} is not in the "
                f"controlled reason-code vocabulary {sorted(_REASON_CODES)}"
            )
        return
    if key in _IDENTIFIER_VALUE_KEYS:
        if not _IDENTIFIER_RE.fullmatch(value):
            raise ValueError(
                f"erasure audit detail '{key}'={value!r} is not a bare "
                f"identifier (expected an exception class name)"
            )
        return
    if key in _FREEFORM_IDENTITY_KEYS:
        return
    if _PATH_HINT_RE.search(value) or _EXC_HINT_RE.search(value):
        raise ValueError(
            f"erasure audit detail '{key}' carries a filesystem path or "
            f"exception-shaped text — refused (GDPR Art. 5 / L36 "
            f"metadata-only). Free-form descriptors belong in the 0600 "
            f"trail file only."
        )


def _validate_audit_details(details: dict[str, Any]) -> None:
    for k, v in details.items():
        if k not in _AUDIT_ALLOWED:
            raise ValueError(
                f"erasure audit detail '{k}' not in allow-list "
                f"{sorted(_AUDIT_ALLOWED)}"
            )
        _assert_safe_audit_value(k, v)


AuditWriter = Callable[[str, str, dict[str, Any]], None]


# ----- orchestrator -------------------------------------------------

class ErasureScopeError(Exception):
    """Raised on cross-tenant erasure attempts. Tenants are siloed
    by ADR-0007; erasure NEVER crosses the boundary."""


@dataclass
class ErasureOrchestrator:
    """Coordinates per-layer purge handlers.

    Typical wiring (in adapter / CLI):

      .. code-block:: python

         orchestrator = ErasureOrchestrator(
             tenant_id=tenant_id,
             trail_dir=tenant_home / "global" / "erasure",
             audit_writer=make_forge_audit_writer(audit_path),
         )
         orchestrator.register_handler(L28RecallHandler(...))
         orchestrator.register_handler(L33ArtifactsHandler(...))
         orchestrator.register_handler(L7SkillForgeHandler(...))
         ...
         result = orchestrator.execute(ErasureRequest(
             subject_id="user_42",
             requester="dpo@example.com",
             scope="all",
         ))
    """
    tenant_id: str
    trail_dir: Path
    audit_writer: AuditWriter | None = None
    _handlers: list[ErasureHandler] = field(default_factory=list)

    # ----- handler registration -------------------------------------

    def register_handler(self, handler: ErasureHandler) -> None:
        """Add a handler to the chain. Order matters — handlers run
        in registration order. Duplicate ``layer_id`` raises
        :class:`ValueError`."""
        existing = {h.layer_id for h in self._handlers}
        if handler.layer_id in existing:
            raise ValueError(
                f"handler for layer_id={handler.layer_id!r} already registered"
            )
        self._handlers.append(handler)

    def list_layers(self) -> list[str]:
        return [h.layer_id for h in self._handlers]

    # ----- execution ------------------------------------------------

    def execute(self, request: ErasureRequest) -> ErasureResult:
        """Run every registered handler for the request. Persist the
        result to ``<trail_dir>/<request_id>.json``. Audit before /
        per-layer / after.

        Never raises on a single handler's failure — the failure is
        captured in :class:`ErasureLayerResult` and the overall
        result becomes ``PARTIAL`` or ``FAILED``. Raises only on
        infrastructure problems (e.g. ``trail_dir`` unwritable).

        Raises :class:`ValueError` immediately if ``audit_writer`` is
        ``None`` — the audit-first invariant requires the
        ``erasure.requested`` event to be durably written before any
        handler fires. Callers that intentionally want no-op audit must
        pass an explicit no-op writer (e.g. ``lambda *a, **kw: None``).
        """
        if self.audit_writer is None:
            raise ValueError(
                "ErasureOrchestrator.execute() requires a non-None audit_writer. "
                "The audit-first invariant (erasure.requested MUST be written "
                "before any handler fires) cannot be satisfied without one. "
                "Pass an explicit no-op lambda if audit emission is intentionally "
                "disabled for this context."
            )

        # Cross-tenant isolation guard (ADR-0007 / CLAUDE.md L36).
        # Checked BEFORE any handler runs or any audit event is emitted
        # so a misconfigured caller cannot smuggle a foreign-tenant
        # request through a single orchestrator instance.
        req_tenant = getattr(request, "tenant_id", None)
        if req_tenant is not None and req_tenant != self.tenant_id:
            raise ErasureScopeError(
                f"Request tenant_id={req_tenant!r} does not match "
                f"orchestrator tenant_id={self.tenant_id!r}. "
                "Cross-tenant erasure is forbidden (ADR-0007)."
            )

        started_at = time.time()

        # Audit emission: BEFORE any handler runs.
        self._emit("erasure.requested", "WARNING", {
            "request_id": request.request_id,
            "subject_id": request.subject_id,
            "requester": request.requester,
            "scope": request.scope,
        })

        result = ErasureResult(
            request=request,
            started_at=started_at,
        )

        for handler in self._handlers:
            t0 = time.time()
            try:
                lr = handler.purge(request.subject_id, request.request_id)
                # Validate handler return — must be ErasureLayerResult
                if not isinstance(lr, ErasureLayerResult):
                    lr = ErasureLayerResult(
                        layer_id=handler.layer_id,
                        status=LayerStatus.FAILED,
                        reason=f"handler returned {type(lr).__name__}, expected ErasureLayerResult",
                        code=ReasonCode.HANDLER_CONTRACT_ERROR.value,
                        duration_ms=int((time.time() - t0) * 1000),
                    )
                # Ensure layer_id matches (handler convention)
                if lr.layer_id != handler.layer_id:
                    lr = ErasureLayerResult(
                        layer_id=handler.layer_id,
                        status=lr.status,
                        count=lr.count,
                        reason=f"handler claimed layer_id={lr.layer_id!r}; corrected to {handler.layer_id!r}. " + (lr.reason or ""),
                        code=lr.code or ReasonCode.HANDLER_CONTRACT_ERROR.value,
                        duration_ms=lr.duration_ms or int((time.time() - t0) * 1000),
                    )
            except Exception as e:  # noqa: BLE001
                # Raw exception text is captured for the 0600 trail file ONLY;
                # the audit chain receives the controlled code STORE_ERROR.
                tb_short = traceback.format_exception_only(type(e), e)[-1].strip()[:500]
                lr = ErasureLayerResult(
                    layer_id=handler.layer_id,
                    status=LayerStatus.FAILED,
                    reason=tb_short,
                    code=ReasonCode.STORE_ERROR.value,
                    duration_ms=int((time.time() - t0) * 1000),
                )

            result.per_layer.append(lr)

            event_type = {
                LayerStatus.APPLIED: "erasure.applied",
                LayerStatus.SKIPPED: "erasure.skipped",
                LayerStatus.FAILED: "erasure.failed",
            }[lr.status]
            severity = "CRITICAL" if lr.status == LayerStatus.FAILED else "INFO"
            # Audit the controlled CODE, never the free-form `reason`.
            # The full descriptive `reason` (paths/exceptions) lives only
            # in the 0600 trail file persisted below.
            self._emit(event_type, severity, {
                "request_id": request.request_id,
                "subject_id": request.subject_id,
                "layer_id": lr.layer_id,
                "status": lr.status.value,
                "count": lr.count,
                "code": _derive_reason_code(lr.status, lr.code),
                "duration_ms": lr.duration_ms,
            })

        result.completed_at = time.time()
        result.overall_status = _aggregate_status(result.per_layer)

        # Persist trail file; failure is surfaced via erasure.trail_failed but
        # must NOT prevent erasure.completed from reaching the audit chain.
        trail_error: str | None = None
        try:
            self._persist_trail(result)
        except Exception as exc:  # noqa: BLE001
            trail_error = type(exc).__name__

        # Audit emission: AFTER all handlers ran, regardless of trail status.
        self._emit("erasure.completed", "WARNING", {
            "request_id": request.request_id,
            "subject_id": request.subject_id,
            "overall_status": result.overall_status.value,
            "applied_count": result.applied_count,
            "failed_count": result.failed_count,
        })

        if trail_error is not None:
            self._emit("erasure.trail_failed", "ERROR", {
                "request_id": request.request_id,
                "subject_id": request.subject_id,
                "error_type": trail_error,
            })

        return result

    # ----- trail persistence ----------------------------------------

    def _persist_trail(self, result: ErasureResult) -> None:
        self.trail_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = self.trail_dir / f"{result.request.request_id}.json"
        tmp = path.with_suffix(path.suffix + ".tmp")
        lock = path.with_suffix(path.suffix + ".lock")
        fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            with open(tmp, "w", opener=lambda p, f: os.open(p, f, 0o600)) as fh:
                fh.write(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n")
            os.replace(tmp, path)
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
            try:
                lock.unlink(missing_ok=True)
            except OSError:
                pass

    # ----- audit emission helper ------------------------------------

    def _emit(self, event_type: str, severity: str, details: dict[str, Any]) -> None:
        if self.audit_writer is None:
            return
        _validate_audit_details(details)
        try:
            self.audit_writer(event_type, severity, details)
        except Exception:  # noqa: BLE001
            pass


# ----- aggregate-status logic ---------------------------------------

def _aggregate_status(per_layer: list[ErasureLayerResult]) -> OverallStatus:
    if not per_layer:
        return OverallStatus.FAILED
    has_failure = any(r.status == LayerStatus.FAILED for r in per_layer)
    has_success = any(r.status in (LayerStatus.APPLIED, LayerStatus.SKIPPED) for r in per_layer)
    if has_failure and not has_success:
        return OverallStatus.FAILED
    if has_failure:
        return OverallStatus.PARTIAL
    return OverallStatus.COMPLETED


# ----- built-in stub handlers ---------------------------------------

@dataclass
class StubHandler:
    """No-op handler that returns SKIPPED. Useful as a placeholder
    while real per-layer handlers are being implemented in the
    respective layers, and as a base class for layers that want a
    declarative "no data here" answer."""
    layer_id: str
    reason: str = "stub handler — real implementation pending"

    def purge(self, subject_id: str, request_id: str) -> ErasureLayerResult:
        return ErasureLayerResult(
            layer_id=self.layer_id,
            status=LayerStatus.SKIPPED,
            reason=self.reason,
            code=ReasonCode.NOT_APPLICABLE.value,
        )


def builtin_stub_chain() -> list[ErasureHandler]:
    """Return the default handler chain populated with stubs for
    every layer that *should* register a real handler. Used by the
    ``corvin-erasure`` CLI when no real handlers have been registered
    yet — gives the operator a visible "TODO" trail for each layer
    instead of silently doing nothing.
    """
    # ADR-0077 C-3: import the real A2A handler instead of a stub.
    try:
        from erasure_a2a import A2AErasureHandler  # type: ignore[import-not-found]
        a2a_handler: ErasureHandler = A2AErasureHandler()
    except ImportError:
        a2a_handler = StubHandler("L38-a2a", "a2a erasure handler not importable")

    # ADR-0153 M4: import the real CorvinID handler instead of a stub.
    try:
        from erasure_handler_corvinid import CorvinIDErasureHandler  # type: ignore[import-not-found]
        corvinid_handler: ErasureHandler = CorvinIDErasureHandler()
    except ImportError:
        corvinid_handler = StubHandler("L153-corvinid", "corvinid erasure handler not importable")

    # ADR-0163: ULO erasure stub — real handler is in real_handler_chain().
    # Keep as stub here so the stub-chain covers the layer_id for doctor output.
    ulo_handler: ErasureHandler = StubHandler(
        "L163-ulo", "ulo handler registered via real_handler_chain()"
    )

    return [
        StubHandler("L28-recall", "recall.db handler not registered"),
        StubHandler("L33-artifacts", "artifacts handler not registered"),
        StubHandler("L7-skill-forge", "skill-forge handler not registered"),
        StubHandler("L24-data-snapshot", "data-snapshot handler not registered"),
        StubHandler(
            "L16-identity-mapping",
            "identity-mapping handler not registered — "
            "audit chain pseudonyms cannot be unwound without this",
        ),
        a2a_handler,
        corvinid_handler,
        ulo_handler,
    ]


# ----- forge-backed audit writer (production wiring) ----------------

def make_forge_audit_writer(audit_path: Path) -> AuditWriter:
    """Build the production audit writer. Same pattern as L34 / L35 /
    L37. Returns a no-op when forge isn't importable."""
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:  # noqa: BLE001
        def _noop(event_type: str, severity: str, details: dict[str, Any]) -> None:
            return
        return _noop

    def _writer(event_type: str, severity: str, details: dict[str, Any]) -> None:
        try:
            write_event(
                audit_path, event_type,
                severity=severity, details=details,
            )
        except Exception:  # noqa: BLE001
            pass

    return _writer
