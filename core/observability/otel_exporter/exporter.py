"""OTELExporter: Core class for Phase 1 dual-write (JSON + OTEL).

ADR-0680: Migration Strategy (Hybrid Dual-Write)
ADR-0681: Metrics Schema

Implements real OTEL SDK with MeterProvider, OTLP exporter, and fallback to JSON
on export failure (zero telemetry loss, fail-closed semantics).
"""

import json
import os
import logging
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

try:
    from opentelemetry import metrics
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import MetricExportResult, PeriodicExportingMetricReader
    from opentelemetry.sdk.resources import Resource
    OTEL_AVAILABLE = True
except ImportError:
    OTEL_AVAILABLE = False

# Transports. OTLP/HTTP (protobuf) is the one the Corvin-Features intake speaks
# and the one an https:// endpoint gets; gRPC stays available for a collector
# on a plain host:port. Either may be absent.
try:
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
        OTLPMetricExporter as _HttpMetricExporter,
    )
except ImportError:
    _HttpMetricExporter = None  # type: ignore[assignment]
try:
    from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
        OTLPMetricExporter as _GrpcMetricExporter,
    )
except ImportError:
    _GrpcMetricExporter = None  # type: ignore[assignment]

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
        headers: Optional[Dict[str, str]] = None,
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
        self.otel_collector_url = otel_collector_url or "http://localhost:4318/v1/metrics"
        self.audit_logger = audit_logger or logging.getLogger("audit")
        self.headers = dict(headers or {})
        # Outcome of the most recent push, for callers that report it
        # (aco/otel_bridge.py → console telemetry panel).
        self.last_export_detail: Optional[str] = None
        self.transport: str = "none"
        self.sdk_version: Optional[str] = None

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

            # Transport by endpoint shape: a URL → OTLP/HTTP protobuf (the
            # Corvin-Features intake); a bare host:port → gRPC collector.
            url = self.otel_collector_url
            if url.startswith(("http://", "https://")):
                if _HttpMetricExporter is None:
                    raise OTELExportError("opentelemetry-exporter-otlp-proto-http is not installed")
                base_cls, self.transport = _HttpMetricExporter, "otlp/http"
                kwargs: Dict[str, Any] = {"endpoint": url, "timeout": 10, "headers": self.headers or None}
            else:
                if _GrpcMetricExporter is None:
                    raise OTELExportError("opentelemetry-exporter-otlp-proto-grpc is not installed")
                base_cls, self.transport = _GrpcMetricExporter, "otlp/grpc"
                kwargs = {"endpoint": url, "timeout": 10,
                          "insecure": os.environ.get("OTEL_EXPORTER_OTLP_INSECURE", "true").lower() in ("1", "true", "yes"),
                          "headers": tuple(self.headers.items()) or None}

            owner = self

            class _RecordingExporter(base_cls):  # type: ignore[misc,valid-type]
                """The SDK swallows the export result inside the reader; this
                subclass keeps it so ``export_heartbeat`` can report truthfully."""

                def export(self, metrics_data, timeout_millis=10_000, **kw):  # noqa: D401
                    try:
                        result = super().export(metrics_data, timeout_millis=timeout_millis, **kw)
                    except Exception as exc:  # noqa: BLE001
                        owner._last_result = (False, f"{type(exc).__name__}: {str(exc)[:80]}")
                        raise
                    ok = result == MetricExportResult.SUCCESS
                    owner._last_result = (ok, "exported" if ok else "exporter reported failure")
                    return result

            exporter = _RecordingExporter(**kwargs)

            # One reader; pushes happen on force_flush() after each heartbeat,
            # never on a timer of their own (a long interval keeps the SDK's
            # background thread idle between heartbeats).
            reader = PeriodicExportingMetricReader(
                exporter,
                export_interval_millis=24 * 3600 * 1000,
                export_timeout_millis=15_000,
            )
            self._reader = reader
            try:
                from opentelemetry.sdk.version import __version__ as _sdkv  # noqa: PLC0415

                self.sdk_version = _sdkv
            except Exception:  # noqa: BLE001
                self.sdk_version = None

            # Create and set MeterProvider
            meter_provider = MeterProvider(resource=resource, metric_readers=[reader])
            # A private provider: the SDK accepts set_meter_provider() once per
            # process, and a second exporter (endpoint change, tests) must not
            # be silently ignored.
            self._meter = meter_provider.get_meter(__name__, version="1.0.0")
            self._meter_provider = meter_provider
            self._last_result: Tuple[bool, str] = (False, "not exported yet")
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
                # Set the gauges (the SDK's synchronous Gauge API is set(), not
        # record() — the original code never ran against a real SDK).
                self._meter.create_gauge(
                    name="corvin.instance.online",
                    unit="1",
                    description="Is this instance currently alive (0=no, 1=yes)?",
                ).set(
                    1 if signal.is_alive else 0,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.uptime",
                    unit="s",
                    description="Uptime since boot in seconds",
                ).set(
                    signal.uptime_seconds,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.plugin_count",
                    unit="1",
                    description="Number of loaded plugins",
                ).set(
                    signal.plugin_count,
                    attributes=base_attributes,
                )

                self._meter.create_gauge(
                    name="corvin.instance.memory_usage",
                    unit="By",  # OpenTelemetry unit for bytes
                    description="Process memory usage in bytes",
                ).set(
                    signal.memory_usage_bytes,
                    attributes=base_attributes,
                )

                # Push NOW — a recorded gauge is not an exported one until the
                # reader flushed it and the exporter answered.
                self._last_result = (False, "flush did not run")
                flushed = self._meter_provider.force_flush(timeout_millis=15_000)
                ok, detail = self._last_result
                self.last_export_detail = detail
                if not flushed or not ok:
                    raise OTELExportError(detail if not ok else "force_flush timed out")
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
