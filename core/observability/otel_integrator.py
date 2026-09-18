"""OTEL Telemetry Integrator: Phase 2 Complete Integration.

Wires OTEL metrics into operator context_engineering and learning loop.
Implements multi-tenant telemetry with dual-write fallback (ADR-0680/0681/0682).

Key responsibilities:
1. Initialize OTEL SDK with batch exporter
2. Emit telemetry signals (metrics, traces, logs)
3. Integrate with context_engineering for metric adaptation
4. Wire into learning loop (ADR-0314) for signal feedback
5. Fail-closed JSON fallback when OTEL unavailable
"""

import asyncio
import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# Optional OTEL imports (graceful fallback if not installed)
try:
    from opentelemetry import metrics, trace
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter,
    )
    from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)


class OTELIntegrationError(Exception):
    """OTEL integration failed (may have fallback)."""

    pass


@dataclass(frozen=True)
class TelemetrySignal:
    """Immutable telemetry signal for multi-tenant emission."""

    tenant_id: str
    signal_type: str  # "metric" | "trace" | "log"
    metric_name: str
    value: float
    attributes: Dict[str, Any]
    timestamp: str  # ISO 8601
    context: Optional[Dict[str, Any]] = None  # Learning context


class OTELIntegrator:
    """Phase 2: Complete OTEL integration with dual-write + learning loop.

    Handles:
    - OTEL SDK initialization (gRPC exporter, batch processor)
    - Metrics emission (heartbeat, skill execution, learning feedback)
    - Trace wiring (request→decision→feedback)
    - JSON fallback (OTEL unavailable)
    - Learning loop integration (ADR-0314)
    - Audit logging (ADR-0232)
    """

    def __init__(
        self,
        tenant_id: str,
        instance_id: str,
        otel_endpoint: str = "http://localhost:4317",
        json_fallback_dir: Optional[Path] = None,
        audit_logger: Optional[logging.Logger] = None,
        enable_learning_feedback: bool = True,
    ):
        """Initialize OTEL Integrator.

        Args:
            tenant_id: Tenant identifier (mandatory)
            instance_id: Instance UUID
            otel_endpoint: OTEL Collector endpoint (gRPC)
            json_fallback_dir: Fallback directory for OTEL failures
            audit_logger: Audit trail logger
            enable_learning_feedback: Wire into ADR-0314 learning loop
        """
        if not tenant_id:
            raise ValueError("tenant_id is mandatory (fail-closed)")

        self.tenant_id = tenant_id
        self.instance_id = instance_id
        self.otel_endpoint = otel_endpoint
        self.json_fallback_dir = json_fallback_dir or (
            Path.home() / ".corvin" / "telemetry"
        )
        self.audit_logger = audit_logger or logging.getLogger("audit")
        self.enable_learning_feedback = enable_learning_feedback

        # Create fallback directory
        self.json_fallback_dir.mkdir(parents=True, exist_ok=True)

        # OTEL SDK components (lazy-initialized on first use)
        self._meter = None
        self._tracer = None
        self._initialized = False
        self._fallback_active = False

        # Learning loop integration
        self._learning_context: Dict[str, Any] = {}
        self._signal_queue: asyncio.Queue = asyncio.Queue()

    def _initialize_otel(self) -> bool:
        """Initialize OTEL SDK (gRPC exporter, batch processor).

        Returns:
            True if initialization succeeded, False if fallback activated.
        """
        if self._initialized:
            return not self._fallback_active

        # Check if OTEL is available
        if not OTEL_AVAILABLE:
            logger.warning(
                "opentelemetry package not installed. Using JSON fallback."
            )
            self._initialized = True
            self._fallback_active = True
            self.audit_logger.warning(
                f"telemetry_fallback_activated: tenant={self.tenant_id}, "
                f"reason=opentelemetry_not_installed"
            )
            return False

        try:
            # Initialize trace exporter (gRPC)
            span_exporter = OTLPSpanExporter(endpoint=self.otel_endpoint)
            trace_provider = TracerProvider(
                resource=Resource.create(
                    {
                        "service.name": "corvinOS",
                        "service.version": "3.0.0",
                        "tenant_id": self.tenant_id,
                        "instance_id": self.instance_id,
                    }
                )
            )
            trace_provider.add_span_processor(BatchSpanProcessor(span_exporter))
            trace.set_tracer_provider(trace_provider)
            self._tracer = trace.get_tracer(__name__)

            # Initialize metrics exporter (gRPC, 60s interval)
            metric_exporter = OTLPMetricExporter(endpoint=self.otel_endpoint)
            reader = PeriodicExportingMetricReader(
                metric_exporter, interval_millis=60000
            )
            meter_provider = MeterProvider(resource=Resource.create(
                {
                    "service.name": "corvinOS",
                    "tenant_id": self.tenant_id,
                    "instance_id": self.instance_id,
                }
            ), metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)
            self._meter = metrics.get_meter(__name__)

            self._initialized = True
            self._fallback_active = False

            self.audit_logger.info(
                f"otel_integrator_initialized: tenant={self.tenant_id}, "
                f"endpoint={self.otel_endpoint}"
            )
            return True

        except Exception as e:
            logger.warning(
                f"OTEL initialization failed: {e}. Activating JSON fallback."
            )
            self._initialized = True
            self._fallback_active = True
            self.audit_logger.warning(
                f"telemetry_fallback_activated: tenant={self.tenant_id}, "
                f"reason={str(e)}"
            )
            return False

    def emit_signal(
        self,
        signal_type: str,
        metric_name: str,
        value: float,
        attributes: Dict[str, Any],
        context: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, str]:
        """Emit telemetry signal (metric/trace/log) with dual-write.

        Args:
            signal_type: "metric" | "trace" | "log"
            metric_name: e.g., "skill.execution.time_ms"
            value: Numeric value
            attributes: Signal attributes (tenant_id, user_id, etc.)
            context: Optional learning context

        Returns:
            (success, message)
        """
        try:
            signal = TelemetrySignal(
                tenant_id=self.tenant_id,
                signal_type=signal_type,
                metric_name=metric_name,
                value=value,
                attributes=attributes,
                timestamp=datetime.utcnow().isoformat() + "Z",
                context=context,
            )

            # Try OTEL export
            if not self._initialized:
                self._initialize_otel()

            if not self._fallback_active:
                try:
                    return self._emit_via_otel(signal)
                except Exception as e:
                    logger.warning(
                        f"OTEL export failed, falling back to JSON: {e}"
                    )
                    self._fallback_active = True
                    self.audit_logger.warning(
                        f"telemetry_otel_export_failed: tenant={self.tenant_id}, "
                        f"metric={metric_name}"
                    )

            # Fallback to JSON
            return self._emit_via_json_fallback(signal)

        except Exception as e:
            self.audit_logger.error(
                f"telemetry_emit_signal_error: tenant={self.tenant_id}, "
                f"error={str(e)}"
            )
            return False, f"Signal emission failed: {str(e)}"

    def _emit_via_otel(self, signal: TelemetrySignal) -> Tuple[bool, str]:
        """Emit signal via OTEL SDK.

        Args:
            signal: TelemetrySignal instance

        Returns:
            (success, message)
        """
        if not OTEL_AVAILABLE or not self._meter or not self._tracer:
            raise OTELIntegrationError("OTEL SDK not initialized")

        if signal.signal_type == "metric":
            # Create gauge or counter
            gauge = self._meter.create_gauge(
                name=signal.metric_name,
                description=f"Metric: {signal.metric_name}",
                unit="1",
            )
            gauge.record(signal.value, attributes=signal.attributes)

        elif signal.signal_type == "trace":
            # Create span with attributes
            with self._tracer.start_as_current_span(
                signal.metric_name
            ) as span:
                for key, val in signal.attributes.items():
                    span.set_attribute(key, val)
                span.set_attribute("value", signal.value)

        elif signal.signal_type == "log":
            # Log via standard logger with attributes
            logger.info(
                f"telemetry_log: {signal.metric_name}={signal.value}",
                extra=signal.attributes,
            )

        self.audit_logger.info(
            f"telemetry_otel_emit: tenant={self.tenant_id}, "
            f"metric={signal.metric_name}, type={signal.signal_type}"
        )
        return True, f"Signal emitted via OTEL: {signal.metric_name}"

    def _emit_via_json_fallback(
        self, signal: TelemetrySignal
    ) -> Tuple[bool, str]:
        """Emit signal to JSON fallback file (append-only).

        Args:
            signal: TelemetrySignal instance

        Returns:
            (success, message)
        """
        try:
            fallback_file = (
                self.json_fallback_dir
                / f"{signal.signal_type}-{self.tenant_id}-{self.instance_id}.jsonl"
            )

            record = asdict(signal)
            with open(fallback_file, "a") as f:
                f.write(json.dumps(record) + "\n")

            self.audit_logger.info(
                f"telemetry_json_fallback: tenant={self.tenant_id}, "
                f"metric={signal.metric_name}, file={fallback_file}"
            )
            return True, f"Signal emitted via JSON fallback: {fallback_file}"

        except Exception as e:
            self.audit_logger.error(
                f"telemetry_json_fallback_error: tenant={self.tenant_id}, "
                f"error={str(e)}"
            )
            return False, f"JSON fallback failed: {str(e)}"

    def register_learning_feedback(
        self, signal_id: str, feedback: Dict[str, Any]
    ) -> Tuple[bool, str]:
        """Register learning feedback for a telemetry signal.

        Integrates with ADR-0314 (Learning Infrastructure).
        Feedback updates skill config, optimizer, and next telemetry emit.

        Args:
            signal_id: Reference to prior telemetry signal
            feedback: Feedback dict (outcome, confidence, preference)

        Returns:
            (success, message)
        """
        try:
            if not self.enable_learning_feedback:
                return True, "Learning feedback disabled"

            # Queue for async learning processor
            asyncio.create_task(
                self._signal_queue.put(
                    {
                        "type": "learning_feedback",
                        "signal_id": signal_id,
                        "feedback": feedback,
                        "timestamp": datetime.utcnow().isoformat() + "Z",
                    }
                )
            )

            self.audit_logger.info(
                f"telemetry_learning_feedback: tenant={self.tenant_id}, "
                f"signal={signal_id}, feedback_type={feedback.get('type')}"
            )
            return True, "Learning feedback registered"

        except Exception as e:
            self.audit_logger.error(
                f"telemetry_learning_feedback_error: {str(e)}"
            )
            return False, f"Learning feedback failed: {str(e)}"

    async def process_pending_signals(self) -> int:
        """Process pending signals from the learning queue.

        Called periodically by event loop.

        Returns:
            Number of signals processed
        """
        count = 0
        while not self._signal_queue.empty():
            try:
                item = self._signal_queue.get_nowait()
                if item["type"] == "learning_feedback":
                    # Update learning context
                    self._learning_context[item["signal_id"]] = item["feedback"]
                    count += 1
            except asyncio.QueueEmpty:
                break
        return count

    def get_metrics_summary(self) -> Dict[str, Any]:
        """Get current telemetry metrics summary.

        Returns:
            Dict with emitted signals, fallbacks, errors, etc.
        """
        fallback_file = (
            self.json_fallback_dir
            / f"metric-{self.tenant_id}-{self.instance_id}.jsonl"
        )
        fallback_count = 0
        if fallback_file.exists():
            with open(fallback_file) as f:
                fallback_count = sum(1 for _ in f)

        return {
            "tenant_id": self.tenant_id,
            "instance_id": self.instance_id,
            "otel_enabled": not self._fallback_active,
            "fallback_active": self._fallback_active,
            "json_fallback_count": fallback_count,
            "learning_signals_queued": self._signal_queue.qsize(),
            "learning_context_entries": len(self._learning_context),
        }
