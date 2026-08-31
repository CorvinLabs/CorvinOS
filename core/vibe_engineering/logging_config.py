"""
Structured Logging Configuration for Vibe Engineering

Emits JSON logs with structured fields for easy querying and monitoring.
Compatible with common log aggregation systems (ELK, Splunk, Datadog, etc.).
"""

import json
import logging
import sys
from datetime import datetime
from typing import Any, Dict
from pythonjsonlogger import jsonlogger


class VIBEFormatter(jsonlogger.JsonFormatter):
    """
    Custom JSON formatter for Vibe Engineering.

    Emits structured fields:
    - timestamp (ISO 8601)
    - level (INFO, ERROR, WARNING)
    - module (which subsystem)
    - message
    - context (task_id, session_id, checkpoint_id, etc.)
    """

    def add_fields(self, log_record: Dict[str, Any], record, message_dict):
        """Add standard fields to log record."""
        super().add_fields(log_record, record, message_dict)

        # Standard fields
        log_record["timestamp"] = datetime.utcnow().isoformat()
        log_record["level"] = record.levelname
        log_record["module"] = record.name

        # Add any extra context fields (if present)
        if hasattr(record, 'task_id'):
            log_record["task_id"] = record.task_id
        if hasattr(record, 'session_id'):
            log_record["session_id"] = record.session_id
        if hasattr(record, 'checkpoint_id'):
            log_record["checkpoint_id"] = record.checkpoint_id
        if hasattr(record, 'trigger'):
            log_record["trigger"] = record.trigger
        if hasattr(record, 'iteration'):
            log_record["iteration"] = record.iteration
        if hasattr(record, 'metric'):
            log_record["metric"] = record.metric
        if hasattr(record, 'value'):
            log_record["value"] = record.value


def configure_logging(
    level: str = "INFO",
    json_output: bool = True,
    log_file: str = None
):
    """
    Configure structured logging for Vibe Engineering.

    Args:
        level: Logging level (DEBUG, INFO, WARNING, ERROR)
        json_output: If True, emit JSON; else human-readable
        log_file: Optional file path for logging (in addition to stdout)
    """
    root = logging.getLogger()
    root.setLevel(getattr(logging, level))

    # Remove existing handlers
    for handler in root.handlers[:]:
        root.removeHandler(handler)

    if json_output:
        # JSON formatter
        formatter = VIBEFormatter('%(message)s')
    else:
        # Human-readable formatter
        formatter = logging.Formatter(
            fmt='[%(asctime)s] %(levelname)-8s %(name)s: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

    # Console handler (stdout)
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    # File handler (if specified)
    if log_file:
        try:
            file_handler = logging.FileHandler(log_file)
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except Exception as e:
            root.warning(f"Could not open log file {log_file}: {e}")


class MetricsEmitter:
    """
    Emit structured metrics to logs for monitoring.

    Counters, histograms, gauges are emitted as JSON log lines,
    ready to be scraped by monitoring systems.
    """

    def __init__(self):
        self.logger = logging.getLogger("vibe_engineering.metrics")

    def counter(self, name: str, value: int, tags: Dict[str, str] = None):
        """Emit counter metric."""
        extra = {
            'metric': 'counter',
            'name': name,
            'value': value,
            'tags': tags or {}
        }
        self.logger.info(f"Metric: {name}={value}", extra=extra)

    def histogram(self, name: str, value: float, tags: Dict[str, str] = None):
        """Emit histogram metric."""
        extra = {
            'metric': 'histogram',
            'name': name,
            'value': value,
            'tags': tags or {}
        }
        self.logger.info(f"Metric: {name}={value:.2f}ms", extra=extra)

    def gauge(self, name: str, value: float, tags: Dict[str, str] = None):
        """Emit gauge metric."""
        extra = {
            'metric': 'gauge',
            'name': name,
            'value': value,
            'tags': tags or {}
        }
        self.logger.info(f"Metric: {name}={value:.1f}", extra=extra)


# Global metrics emitter
_metrics = None


def get_metrics() -> MetricsEmitter:
    """Get or create global metrics emitter."""
    global _metrics
    if _metrics is None:
        _metrics = MetricsEmitter()
    return _metrics


# Example usage in code:
# logger.info("Checkpoint saved", extra={'task_id': 'task_001', 'checkpoint_id': 'ckpt_123'})
# metrics = get_metrics()
# metrics.counter('vibe_checkpoint_created', 1, tags={'task_id': 'task_001'})
# metrics.histogram('vibe_checkpoint_save_ms', 45.3, tags={'compression_pct': '91'})
