"""Template: custom AuditBackend plugin (ADR-0233).

An audit backend is a SECONDARY SINK.  Core has already written the event to its
own hash-chained ``audit.jsonl`` before your ``fanout()`` is called — you are
forwarding a copy to Postgres / S3 / a SIEM, not owning the trail.

Copy this file, rename the class, fill in the TODOs, then install via:
    spec.plugins.installed:
      - id: "com.example.siem-audit"
        class_path: "my_package.my_module:MySiemAuditPlugin"
        config:
          endpoint: "https://siem.internal/ingest"   # credentials go in vault

Rules you MUST honour (they are what makes an installed backend safe):

* ``fanout()`` MUST NOT raise.  Swallow your own errors; the registry catches
  anything you miss, but a raising backend still costs the calling thread time.
* ``fanout()`` MUST NOT block.  Queue and return; a slow sink must not slow down
  every audited action in the platform.
* NEVER add PII, prompt text or user content to the record.  What you receive has
  passed the core metadata-only floor — keep it that way.
* NEVER try to reach the core chain: do not open ``audit.jsonl``, do not rewrite
  it, do not delete from it.  Retention on the core chain is L37's job.
"""
from __future__ import annotations

import queue
import threading

from corvin_plugins.protocol import HealthStatus, PluginContext


class MySiemAuditPlugin:
    """Replace with your actual class name."""

    plugin_id    = "com.example.siem-audit"   # globally unique reverse-domain
    plugin_type  = "audit_backend"
    version      = "1.0.0"
    display_name = "My SIEM Audit Backend"

    #: Bounded queue: when the sink is down, DROP the oldest copy rather than
    #: growing without limit.  The authoritative record is already on disk, so a
    #: dropped copy is a monitoring gap, not a compliance gap — whereas unbounded
    #: growth is an availability incident.
    MAX_QUEUED = 10_000

    def __init__(self) -> None:
        self._config: dict = {}
        self._queue: queue.Queue = queue.Queue(maxsize=self.MAX_QUEUED)
        self._worker: threading.Thread | None = None
        self._stop = threading.Event()
        self._dropped = 0

    # ── CorvinPlugin lifecycle ───────────────────────────────────────────────

    def on_load(self, ctx: PluginContext) -> None:
        self._config = ctx.config
        # TODO: build your client here (read credentials from the vault by NAME,
        # never from ctx.config values).
        self._stop.clear()
        self._worker = threading.Thread(
            target=self._drain, name="siem-audit-fanout", daemon=True
        )
        self._worker.start()
        if ctx.audit_registry is not None:
            ctx.audit_registry.set_active(self)

    def on_unload(self) -> None:
        self._stop.set()
        if self._worker is not None:
            self._worker.join(timeout=5.0)
        # TODO: flush whatever is still queued, then close the client.

    def health_check(self) -> HealthStatus:
        # TODO: check connectivity to your sink.  Report the drop count so a
        # silently failing sink is visible in health_check_all().
        return HealthStatus(
            ok=True,
            message="ok",
            details={"queued": self._queue.qsize(), "dropped": self._dropped},
        )

    # ── AuditBackend capability ──────────────────────────────────────────────

    def fanout(
        self,
        event_type: str,
        details: dict,
        *,
        severity: str = "INFO",
        tenant_id: str = "_default",
    ) -> None:
        """Accept a copy of an already-committed core audit event."""
        record = {
            "event_type": event_type,
            "severity": severity,
            "tenant_id": tenant_id,
            "details": details,
        }
        try:
            self._queue.put_nowait(record)
        except queue.Full:
            # Drop the oldest, keep the newest. Never block the caller.
            try:
                self._queue.get_nowait()
                self._queue.put_nowait(record)
            except (queue.Empty, queue.Full):
                pass
            self._dropped += 1

    def verify_chain(self) -> HealthStatus:
        """Report on YOUR OWN copy, if you keep one.

        This is never consulted to decide whether the core chain is intact —
        ``core/compliance/tripwire.py`` does that against the core writer.
        """
        # TODO: verify your sink's copy, or return ok with "not applicable".
        return HealthStatus(ok=True, message="backend keeps no verifiable copy")

    def enforce_retention(self, max_age_days: int, *, tenant_id: str = "_default") -> dict:
        """Apply retention to YOUR sink only.  Never touch the core chain."""
        # TODO: delete records older than max_age_days from your store.
        return {"deleted": 0}

    # ── internals ────────────────────────────────────────────────────────────

    def _drain(self) -> None:
        while not self._stop.is_set():
            try:
                record = self._queue.get(timeout=0.5)
            except queue.Empty:
                continue
            try:
                # TODO: ship `record` to your sink.
                del record
            except Exception:  # noqa: BLE001 - a sink error must stay inside the worker
                pass
