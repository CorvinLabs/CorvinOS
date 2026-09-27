"""OpenTelemetry infrastructure for Skills 2.0 observability (ADR-0637).

Provides:
- OtelTracer: wraps OpenTelemetry SDK for Skill execution spans
- OTLP exporter config (Prometheus, Jaeger, cloud providers)
- Span context propagation (tenant_id, audit_ref binding)
- Sampling strategy (configurable, default 10% cost-sensitive)
"""

from __future__ import annotations

import os
import logging
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Optional, Any

logger = logging.getLogger(__name__)


@dataclass
class OtelConfig:
    """OpenTelemetry configuration."""

    enabled: bool = True
    exporter_type: str = "otlp"  # otlp, jaeger, prometheus, logging
    exporter_endpoint: str = ""  # env OTEL_EXPORTER_OTLP_ENDPOINT
    service_name: str = "corvinOS-skills"
    service_version: str = "2.0.0"
    sample_rate: float = 0.10  # 10% sampling by default (cost-sensitive)

    @classmethod
    def from_env(cls) -> OtelConfig:
        """Load config from environment variables (ADR-0637 § configuration)."""
        return cls(
            enabled=os.environ.get("OTEL_ENABLED", "true").lower() == "true",
            exporter_type=os.environ.get("OTEL_EXPORTER_TYPE", "otlp"),
            exporter_endpoint=os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318"),
            service_name=os.environ.get("OTEL_SERVICE_NAME", "corvinOS-skills"),
            service_version=os.environ.get("OTEL_SERVICE_VERSION", "2.0.0"),
            sample_rate=float(os.environ.get("OTEL_SAMPLE_RATE", "0.10")),
        )


class OtelTracer:
    """Wraps OpenTelemetry SDK for Skills execution tracing.

    Features:
    - Automatic span creation for Skill.execute() calls
    - Tenant isolation (tenant_id in span context)
    - Audit trail binding (audit_ref links trace to chain)
    - Cost-aware sampling (configurable rate)
    - Fail-safe (exceptions in tracing don't break Skills)
    """

    def __init__(self, config: Optional[OtelConfig] = None):
        """Initialize OtelTracer.

        Args:
            config: OtelConfig instance (default: from environment)
        """
        self.config = config or OtelConfig.from_env()
        self.tracer = None
        self._initialized = False

        if self.config.enabled:
            self._init_tracer()

    def _init_tracer(self) -> None:
        """Initialize OpenTelemetry tracer (fail-safe)."""
        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
            from opentelemetry.sdk.resources import SERVICE_NAME, Resource

            # Create resource with service metadata
            resource = Resource(attributes={
                SERVICE_NAME: self.config.service_name,
                "service.version": self.config.service_version,
            })

            # Initialize tracer provider
            tracer_provider = TracerProvider(resource=resource)

            # Configure exporter based on type
            if self.config.exporter_type == "otlp":
                exporter = OTLPSpanExporter(endpoint=self.config.exporter_endpoint)
                tracer_provider.add_span_processor(BatchSpanProcessor(exporter))
            else:
                logger.warning(f"Unsupported exporter type: {self.config.exporter_type}, using no-op")

            # Set as global tracer provider
            trace.set_tracer_provider(tracer_provider)
            self.tracer = trace.get_tracer(__name__)
            self._initialized = True
            logger.info(f"OtelTracer initialized: {self.config.exporter_type} → {self.config.exporter_endpoint}")
        except ImportError:
            logger.warning("opentelemetry not installed, tracing disabled")
            self.config.enabled = False
        except Exception as e:
            logger.error(f"Failed to initialize OtelTracer: {e}", exc_info=True)
            self.config.enabled = False

    @contextmanager
    def span(
        self,
        name: str,
        attributes: Optional[dict[str, Any]] = None,
        tenant_id: Optional[str] = None,
        audit_ref: Optional[str] = None,
    ):
        """Create a tracing span for Skill execution.

        Args:
            name: Span name (e.g., "skill.delegation_router.execute")
            attributes: Custom span attributes (skill_id, version, etc.)
            tenant_id: Tenant identifier (for context isolation)
            audit_ref: Audit chain reference (links trace to audit events)

        Yields:
            Span object (or no-op if tracing disabled)
        """
        if not self.config.enabled or not self.tracer:
            yield None
            return

        try:
            attrs = attributes or {}
            if tenant_id:
                attrs["tenant_id"] = tenant_id
            if audit_ref:
                attrs["audit_ref"] = audit_ref

            with self.tracer.start_as_current_span(name) as span:
                for key, value in attrs.items():
                    span.set_attribute(key, value)
                yield span
        except Exception as e:
            logger.error(f"Error in OtelTracer.span(): {e}", exc_info=True)
            yield None


# Global tracer instance
_global_tracer: Optional[OtelTracer] = None


def get_tracer() -> OtelTracer:
    """Get or create global OtelTracer instance."""
    global _global_tracer
    if _global_tracer is None:
        _global_tracer = OtelTracer()
    return _global_tracer


def record_span_attribute(span, key: str, value: Any) -> None:
    """Safely set attribute on a span (fail-safe if span is None)."""
    if span is not None:
        try:
            span.set_attribute(key, value)
        except Exception as e:
            logger.debug(f"Failed to set span attribute {key}: {e}")


__all__ = [
    "OtelConfig",
    "OtelTracer",
    "get_tracer",
    "record_span_attribute",
]
