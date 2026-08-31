"""Request context for structured logging: correlation id + tenant (ADR-0231).

Two deliberate deviations from ``docs/design/STRUCTURED_LOGGING_SYSTEM.md``:

1. **``contextvars``, not ``threading.local``.** The design sketch uses a
   thread-local. The gateway, the console and the adapter are all asyncio: many
   tasks share one thread, so a thread-local correlation id leaks between
   concurrent requests — every task would see whichever id was set last. A
   ``ContextVar`` is per-task AND per-thread, and it propagates into tasks created
   from the current one, which is exactly the semantics a correlation id needs.

2. **The package is not called ``logging``.** The sketch says
   ``core/logging/structured_logger.py``. A package named ``logging`` shadows the
   standard library the moment its parent lands on ``sys.path`` — the same class of
   failure that once broke ``corvin-webui.service`` via an ``operator`` package
   shadowing ``operator``. It lives in ``core/observability/corvin_logging/``.
"""
from __future__ import annotations

import contextlib
import uuid
from contextvars import ContextVar
from typing import Iterator, Optional

#: Correlation id of the request/turn currently being handled.
_correlation_id: ContextVar[Optional[str]] = ContextVar("corvin_correlation_id", default=None)
#: Tenant the current work belongs to.
_tenant_id: ContextVar[Optional[str]] = ContextVar("corvin_tenant_id", default=None)
#: Component/plugin currently executing, for per-component filtering.
_component: ContextVar[Optional[str]] = ContextVar("corvin_component", default=None)


def new_correlation_id() -> str:
    """A fresh correlation id.

    Random, not derived from anything about the user: an id built from a chat id or
    a user handle would turn every log line into a pseudonymous identifier and drag
    the whole log stream into GDPR scope.
    """
    return f"req-{uuid.uuid4().hex[:16]}"


def get_correlation_id() -> Optional[str]:
    return _correlation_id.get()


def get_tenant_id() -> Optional[str]:
    return _tenant_id.get()


def get_component() -> Optional[str]:
    return _component.get()


def set_correlation_id(correlation_id: str) -> None:
    """Set the id for the current task.  Prefer :func:`request_context`."""
    _correlation_id.set(correlation_id)


@contextlib.contextmanager
def request_context(
    correlation_id: Optional[str] = None,
    *,
    tenant_id: Optional[str] = None,
    component: Optional[str] = None,
) -> Iterator[str]:
    """Bind a correlation id (and optionally tenant/component) to this task.

    Restores the previous values on exit via the token API, so nesting works and a
    context cannot leak out of its block — the sketch's "restore only if truthy"
    approach left the inner value in place whenever the outer one was unset.
    """
    cid = correlation_id or new_correlation_id()
    tokens = [_correlation_id.set(cid)]
    if tenant_id is not None:
        tokens.append(_tenant_id.set(tenant_id))
    if component is not None:
        tokens.append(_component.set(component))
    try:
        yield cid
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


def current() -> dict:
    """The context fields to attach to a log record (absent keys omitted)."""
    out = {}
    if (cid := _correlation_id.get()) is not None:
        out["correlation_id"] = cid
    if (tid := _tenant_id.get()) is not None:
        out["tenant_id"] = tid
    if (comp := _component.get()) is not None:
        out["component"] = comp
    return out


__all__ = [
    "current",
    "get_component",
    "get_correlation_id",
    "get_tenant_id",
    "new_correlation_id",
    "request_context",
    "set_correlation_id",
]
