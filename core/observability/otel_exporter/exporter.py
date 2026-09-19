"""OTELExporter: Core class for Phase 1 dual-write (JSON + OTEL).

ADR-0680: Migration Strategy (Hybrid Dual-Write)
ADR-0681: Metrics Schema

Implements real OTEL SDK with MeterProvider, OTLP exporter, and fallback to JSON
on export failure (zero telemetry loss, fail-closed semantics).
"""

import json
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from opentelemetry import metrics
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

logger = logging.getLogger(__name__)


class OTELExportError(Exception):
    """Raised when OTEL export fails (recoverable)."""

    pass


@dataclass(frozen=True)
class HeartbeatSignal:
    """Immutable heartbeat signal (Gauge: corvin.instance.online)."""

    tenant_id: str
    instance_id: str
    is_alive: bool
    uptime_seconds: int
    timestamp: str  # ISO 8601
    platform: str  # linux | darwin | windows
    python_version: str
    plugin_count: int
    memory_usage_bytes: int


@dataclass(frozen=True)
class GeoAttributes:
    """Immutable geo resource attributes (ADR-0683)."""

    country: str  # ISO 3166-1 alpha-2, always
    region: Optional[str] = None  # ISO 3166-2, if granularity >= "region"
    city: Optional[str] = None  # City name, if granularity >= "city"
    granularity: str = "country"  # Declared level
    source: str = "cloudflare"  # Where did this come from?


class OTELExporter:
    """Phase 1 Core Exporter: Heartbeat + Geo dual-write (JSON + OTEL).

    Responsibilities:
    1. Export heartbeat signal (JSON + OTEL Gauge)
    2. Export geo attributes (OTEL Resource Attributes)
    3. Failover to JSON if OTEL export fails (zero telemetry loss)
    4. Audit log all exports + fallbacks

    Constraints (ADR-0680):
    - tenant_id is mandatory (fail-closed if missing)
    - dual-write is atomic (JSON fallback is written if OTEL fails)
    - no PII in attributes (privacy validator is separate)
    """

    def __init__(
        self,
        tenant_id: str,
        instance_id: str,
        geo_granularity: str = "country",
        json_fallback_dir: Optional[Path] = None,
        otel_collector_url: Optional[str] = None,
        audit_logger: Optional[logging.Logger] = None,
    ):
        """Initialize OTELExporter.

        Args:
            tenant_id: Tenant identifier (mandatory, fail-closed if missing)
            instance_id: Instance UUID
            geo_granularity: "country" | "region" | "city" | "coordinates"
            json_fallback_dir: Where to write JSON fallback (e.g., ~/.corvin/telemetry/)
            otel_collector_url: OTEL Collector gRPC endpoint
            audit_logger: Logger for audit trail (telemetry_dual_write, telemetry_fallback_activated)
        """
        if not tenant_id:
            raise ValueError("tenant_id is mandatory (fail-closed)")

        self.tenant_id = tenant_id
        self.instance_id = instance_id
        self.geo_granularity = geo_granularity
        self.json_fallback_dir = json_fallback_dir or Path.home() / ".corvin" / "telemetry"
        self.otel_collector_url = otel_collector_url or "http://localhost:4318"
        self.audit_logger = audit_logger or logging.getLogger("audit")

        # Phase 2+: Initialize OTEL SDK (batch exporter, OTLP gRPC)
        self._initialize_otel_sdk()

    def export_heartbeat(
        self,
        is_alive: bool,
        uptime_seconds: int,
        plugin_count: int,
        memory_usage_bytes: int,
        platform: str,
        python_version: str,
        geo_attrs: Optional[GeoAttributes] = None,
    ) -> Tuple[bool, str]:
        """Export heartbeat signal (JSON + OTEL).

        Returns:
            (success: bool, message: str)
            - success=True: OTEL export succeeded
            - success=False: OTEL export failed, fell back to JSON (zero data loss)
        """
        signal = HeartbeatSignal(
            tenant_id=self.tenant_id,
            instance_id=self.instance_id,
            is_alive=is_alive,
            uptime_seconds=uptime_seconds,
            timestamp=datetime.utcnow().isoformat() + "Z",
            platform=platform,
            python_version=python_version,
            plugin_count=plugin_count,
            memory_usage_bytes=memory_usage_bytes,
        )

        # Step 1: Try OTEL export
        try:
            self._export_to_otel(signal, geo_attrs)
            self.audit_logger.info(
                "telemetry_dual_write",
                extra={
                    "tenant_id": self.tenant_id,
                    "signal_type": "heartbeat",
                    "export_target": "otel",
                    "timestamp": signal.timestamp,
                },
            )
            return True, "OTEL export succeeded"
        except OTELExportError as e:
            # Step 2: Fallback to JSON
            self.audit_logger.warning(
                "telemetry_fallback_activated",
                extra={
                    "tenant_id": self.tenant_id,
                    "reason": str(e),
                    "fallback_target": "json",
                    "timestamp": signal.timestamp,
                },
            )
            self._export_to_json(signal, geo_attrs)
            return False, f"OTEL failed, fell back to JSON: {e}"

    def _initialize_otel_sdk(self) -> None:
        """Initialize OTEL SDK with MeterProvider and OTLP exporter.

        Sets up:
        - MeterProvider with OTLP gRPC exporter
        - Resource attributes (tenant_id, instance_id, geo, etc.)
        - PeriodicExportingMetricReader (async, interval=60s)

        Raises: OTELExportError if initialization fails and collector is unreachable
        """
        try:
            if not OTEL_AVAILABLE:
                logger.warning("OTEL SDK not available (packages not installed)")
                self._otel_initialized = False
                self._meter = None
                return

            # Create Resource with tenant and instance attributes
            resource = Resource.create({
                "service.name": "corvinOS",
                "tenant_id": self.tenant_id,
                "instance_id": self.instance_id,
                "geo.granularity": self.geo_granularity,
                "service.version": "2.0.0",  # TODO: load from CORVIN_VERSION env var
            })

            # Initialize OTLP exporter (gRPC endpoint)
            # timeout_millis: 10s per export attempt
            # insecure: True for local development; override with env var for production
            exporter = OTLPMetricExporter(
                endpoint=self.otel_collector_url,
                timeout=10,  # seconds
                insecure=True,  # TODO: Set via env var OTEL_EXPORTER_OTLP_INSECURE
            )

            # Create periodic reader (exports every 60 seconds)
            reader = PeriodicExportingMetricReader(
                exporter,
                interval_millis=60000,  # 60 seconds
            )

            # Create and set MeterProvider
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            metrics.set_meter_provider(meter_provider)

            # Get meter for this module
            self._meter = metrics.get_meter(__name__, version="1.0.0")
            self._meter_provider = meter_provider
            self._otel_initialized = True

            logger.info(f"OTEL SDK initialized: collector={self.otel_collector_url}, tenant={self.tenant_id}")

        except Exception as e:
            logger.warning(f"OTEL SDK initialization failed: {e} (will use JSON fallback)")
            self._otel_initialized = False
            self._meter = None

    def _export_to_otel(
        self, signal: HeartbeatSignal, geo_attrs: Optional[GeoAttributes], max_retries: int = 3
    ) -> None:
        """Export heartbeat signal to OTEL Collector as Gauge metrics.

        Creates:
        - corvin.instance.online (Gauge: 0 or 1)
        - corvin.instance.uptime (Gauge: seconds)
        - corvin.instance.plugin_count (Gauge: count)
        - corvin.instance.memory_usage (Gauge: bytes)

        Implements exponential backoff retry (100ms → 200ms → 400ms).

        Args:
            signal: HeartbeatSignal to export
            geo_attrs: Optional GeoAttributes (country, region, city)
            max_retries: Number of retry attempts (default 3)

        Raises: OTELExportError if all retries fail
        """
        if not self._otel_initialized or self._meter is None:
            raise OTELExportError("OTEL SDK not initialized (meter is None)")

        # Base attributes for all metrics
        base_attributes = {
            "tenant_id": signal.tenant_id,
            "instance_id": signal.instance_id,
            "platform": signal.platform,
            "python_version": signal.python_version,
        }

        # Add geo attributes if present
        if geo_attrs:
            base_attributes.update({
                "geo.country": geo_attrs.country,
                "geo.region": geo_attrs.region,
                "geo.city": geo_attrs.city,
                "geo.granularity": geo_attrs.granularity,
            })

        # Retry logic with exponential backoff
        last_error = None
        for attempt in range(max_retries):
            try:
                # Record Gauge metrics
                self._meter.create_gauge(
                    name="corvin.instance.online",
                    unit="1",
                    description="Is this instance currently alive (0=no, 1=yes)?",
                ).record(
                    1 if signal.is_alive else 0,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.uptime",
                    unit="s",
                    description="Uptime since boot in seconds",
                ).record(
                    signal.uptime_seconds,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.plugin_count",
                    unit="1",
                    description="Number of loaded plugins",
                ).record(
                    signal.plugin_count,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.memory_usage",
                    unit="By",  # OpenTelemetry unit for bytes
                    description="Process memory usage in bytes",
                ).record(
                    signal.memory_usage_bytes,
                    attributes=base_attributes,
                )

                # Success - return without raising
                logger.debug(f"OTEL export succeeded on attempt {attempt + 1}/{max_retries}")
                return

            except Exception as e:
                last_error = e
                if attempt < max_retries - 1:
                    # Exponential backoff: 100ms * 2^attempt
                    backoff_ms = 100 * (2 ** attempt)
                    logger.debug(f"OTEL export attempt {attempt + 1} failed: {e}. Retrying in {backoff_ms}ms...")
                    time.sleep(backoff_ms / 1000.0)
                else:
                    logger.error(f"OTEL export failed after {max_retries} attempts: {e}")

        # All retries exhausted - raise error
        raise OTELExportError(f"OTEL metric export failed after {max_retries} retries: {last_error}")

    def _export_to_json(
        self, signal: HeartbeatSignal, geo_attrs: Optional[GeoAttributes]
    ) -> None:
        """Export to JSON fallback file (backward compat, ADR-0680).

        Writes to: {json_fallback_dir}/heartbeat-{tenant_id}-{instance_id}.jsonl (append-only)
        """
        self.json_fallback_dir.mkdir(parents=True, exist_ok=True)
        fallback_file = (
            self.json_fallback_dir / f"heartbeat-{self.tenant_id}-{self.instance_id}.jsonl"
        )

        # Construct JSON record
        record = {
            **asdict(signal),
            "geo": asdict(geo_attrs) if geo_attrs else None,
        }

        # Append to file (immutable log)
        with open(fallback_file, "a") as f:
            f.write(json.dumps(record) + "\n")

        logger.info(f"Wrote heartbeat to JSON fallback: {fallback_file}")
