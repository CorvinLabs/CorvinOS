"""CorvinLogger — one structured JSON record per event (ADR-0231 Stage 1).

The schema is flat and filterable, matching ADR-0231 § Structured Logging Schema:

    timestamp, level, component, plugin_id, tenant_id, correlation_id,
    operation, duration_ms, error_code, recovered, message, context

Deliberate deviations from ``docs/design/STRUCTURED_LOGGING_SYSTEM.md``:

* **No ``print()``.** The sketch emits with ``print(json.dumps(event))`` *and* to a
  logger. In this repo the runtime captures stdout/stderr into
  ``<corvin_home>/logs/corvin.log``, so a print is both invisible where it was
  expected and duplicated where it wasn't. Records go through the ``logging``
  module only; a JSON formatter is what makes them machine-readable, and the
  operator's existing handlers keep working.
* **Scrubbing redacts instead of raising** — see ``scrubber``.
* **``error_code`` is a code, never a message.** ``str(exc)`` routinely carries a
  path, a hostname or a record fragment; the exception CLASS is the signal.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from . import context as _ctx
from .scrubber import scrub

#: Per-field character cap. A record is one line in a log stream, and aggregators
#: (Loki, ELK, journald) drop lines past their own limit — so an oversized field
#: does not merely bloat the log, it makes the whole event disappear. Truncating
#: keeps the operational signal. The rule remains "log metadata, not payloads";
#: this is the backstop for when someone forgets.
MAX_FIELD_CHARS = 2048
#: Cap on the serialised record. Beyond this the context is dropped entirely and
#: replaced with a marker: the message and the schema fields matter more than a
#: context nobody can read.
MAX_RECORD_CHARS = 16384

#: Fields that always appear (in this order) so a reader can grep positionally.
_ORDER = (
    "timestamp",
    "level",
    "component",
    "plugin_id",
    "tenant_id",
    "correlation_id",
    "operation",
    "duration_ms",
    "error_code",
    "recovered",
    "message",
    "pii_redacted",
    "context",
)


class JsonFormatter(logging.Formatter):
    """Render a record as one JSON line, preferring our structured payload."""

    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "corvin_event", None)
        if payload is None:
            # A plain logging call from anywhere else: still emit valid JSON so a
            # single stream stays parseable end to end.
            payload = {
                "timestamp": _now(),
                "level": record.levelname,
                "component": record.name,
                "message": record.getMessage(),
            }
        return json.dumps(payload, default=str, ensure_ascii=False)


#: Depth ceiling for the size walk, mirroring the scrubber's.
_MAX_TRUNCATE_DEPTH = 6


def _truncate(event: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """Cap field and record size.  Returns ``(event, was_truncated)``."""
    truncated = False

    def cap(value: Any, depth: int = 0) -> Any:
        nonlocal truncated
        # Depth ceiling, like the scrubber has. Without it a self-referencing
        # context (context["self"] = context) recursed until RecursionError, which
        # the outer handler swallowed — so the record was silently LOST instead of
        # merely being oversized. A guard that drops the event is worse than the
        # problem it guards against.
        if depth > _MAX_TRUNCATE_DEPTH:
            truncated = True
            return "…[too deep]"
        if isinstance(value, str) and len(value) > MAX_FIELD_CHARS:
            truncated = True
            return value[:MAX_FIELD_CHARS] + f"…[+{len(value) - MAX_FIELD_CHARS} chars]"
        if isinstance(value, dict):
            return {k: cap(v, depth + 1) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cap(v, depth + 1) for v in value]
        return value

    event = {k: cap(v) for k, v in event.items()}

    # Still too big (many capped fields, or a huge nested structure): the context is
    # the expendable part — the schema fields and the message are what a reader needs.
    if len(json.dumps(event, default=str)) > MAX_RECORD_CHARS and "context" in event:
        event["context"] = {"dropped": "record exceeded MAX_RECORD_CHARS"}
        truncated = True
    return event, truncated


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace(
        "+00:00", "Z"
    )


class CorvinLogger:
    """Structured logger for one component (and optionally one plugin)."""

    def __init__(self, component: str, plugin_id: Optional[str] = None):
        self.component = component
        self.plugin_id = plugin_id
        self._logger = logging.getLogger(f"corvin.{component}")

    # ── level helpers ────────────────────────────────────────────────────────

    def debug(self, message: str, **kw: Any) -> Dict[str, Any]:
        return self._emit(logging.DEBUG, message, **kw)

    def info(self, message: str, **kw: Any) -> Dict[str, Any]:
        return self._emit(logging.INFO, message, **kw)

    def warn(self, message: str, **kw: Any) -> Dict[str, Any]:
        return self._emit(logging.WARNING, message, **kw)

    def error(self, message: str, **kw: Any) -> Dict[str, Any]:
        return self._emit(logging.ERROR, message, **kw)

    def critical(self, message: str, **kw: Any) -> Dict[str, Any]:
        return self._emit(logging.CRITICAL, message, **kw)

    # ── the one emit path ────────────────────────────────────────────────────

    def _emit(
        self,
        level: int,
        message: str,
        *,
        operation: str = "",
        duration_ms: Optional[float] = None,
        error: Optional[BaseException] = None,
        error_code: str = "",
        recovered: Optional[bool] = None,
        context: Optional[dict] = None,
    ) -> Dict[str, Any]:
        """Build, scrub and emit one record.  Returns the record (for tests).

        Never raises: a logging call must not be able to fail the work it describes.
        """
        try:
            event: Dict[str, Any] = {
                "timestamp": _now(),
                "level": logging.getLevelName(level),
                "component": self.component,
                "message": message,
            }
            if self.plugin_id:
                event["plugin_id"] = self.plugin_id
            if operation:
                event["operation"] = operation
            if duration_ms is not None:
                event["duration_ms"] = round(float(duration_ms), 3)
            # An exception contributes its CLASS, never its message.
            code = error_code or (type(error).__name__ if error is not None else "")
            if code:
                event["error_code"] = code
            if recovered is not None:
                event["recovered"] = bool(recovered)
            if context:
                event["context"] = context

            # Context fields (correlation_id / tenant_id / component) come last so
            # an explicit component on the logger wins over an ambient one.
            ambient = _ctx.current()
            ambient.pop("component", None)
            event.update(ambient)

            # Truncate FIRST, scrub SECOND. The scrubber is regex-based, and a
            # multi-hundred-kilobyte value made it the slowest thing in the process
            # (measured: seconds per call, unbounded with size). Capping first means
            # the patterns only ever see a few kilobytes.
            event, truncated = _truncate(event)
            scrubbed, redacted = scrub(event)
            if redacted:
                scrubbed["pii_redacted"] = True
            if truncated:
                scrubbed["truncated"] = True

            ordered = {k: scrubbed[k] for k in _ORDER if k in scrubbed}
            ordered.update({k: v for k, v in scrubbed.items() if k not in ordered})

            self._logger.log(level, message, extra={"corvin_event": ordered})
            return ordered
        except Exception:  # noqa: BLE001 - logging must never break the caller
            try:
                self._logger.error("structured log emit failed for %r", message[:80])
            except Exception:  # noqa: BLE001
                pass
            return {}

    # ── timing helper ────────────────────────────────────────────────────────

    def timed(self, operation: str, **kw: Any) -> "_Timer":
        """Context manager that logs the operation's duration on exit.

        Records ``recovered=False`` and the exception CLASS when the block raises,
        then re-raises — the log is a side effect, never a swallow.
        """
        return _Timer(self, operation, kw)


class _Timer:
    def __init__(self, logger: CorvinLogger, operation: str, kw: dict):
        self._logger = logger
        self._operation = operation
        self._kw = kw
        self._start = 0.0

    def __enter__(self) -> "_Timer":
        self._start = time.monotonic()
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        elapsed_ms = (time.monotonic() - self._start) * 1000.0
        if exc is None:
            self._logger.info(
                f"{self._operation} ok", operation=self._operation,
                duration_ms=elapsed_ms, **self._kw
            )
        else:
            self._logger.error(
                f"{self._operation} failed", operation=self._operation,
                duration_ms=elapsed_ms, error=exc, recovered=False, **self._kw
            )
        return False  # never suppress


def get_logger(component: str, plugin_id: Optional[str] = None) -> CorvinLogger:
    return CorvinLogger(component, plugin_id)


def install_json_handler(level: int = logging.INFO) -> logging.Handler:
    """Attach the JSON formatter to the ``corvin`` logger tree.

    Idempotent, and it does NOT touch the root logger: the operator's existing
    handlers (including the file handler that captures stdout) stay in charge of
    everything else.
    """
    root = logging.getLogger("corvin")
    for existing in root.handlers:
        if getattr(existing, "_corvin_json", False):
            return existing
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    handler._corvin_json = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(level)
    return handler


__all__ = [
    "MAX_FIELD_CHARS",
    "MAX_RECORD_CHARS",
    "CorvinLogger",
    "JsonFormatter",
    "get_logger",
    "install_json_handler",
]
