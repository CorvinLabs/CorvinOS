"""
Distributed Tracing for Plugins — Phase 1

OpenTelemetry integration. Track plugin execution latency, flamegraphs.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any, Optional
import time
import json
from datetime import datetime
import threading


@dataclass
class Span:
    """Single trace span."""
    name: str
    plugin_id: str
    start_ms: float
    end_ms: Optional[float] = None
    duration_ms: float = 0.0
    status: str = "ok"  # ok, error, timeout
    attributes: Dict[str, Any] = field(default_factory=dict)
    children: List["Span"] = field(default_factory=list)

    def finish(self, status: str = "ok"):
        """Mark span as finished."""
        self.end_ms = time.time() * 1000
        self.duration_ms = self.end_ms - self.start_ms
        self.status = status

    def set_attribute(self, key: str, value: Any):
        """Set span attribute."""
        self.attributes[key] = value

    def to_dict(self) -> Dict[str, Any]:
        """Serialize span to dict."""
        return {
            "name": self.name,
            "plugin_id": self.plugin_id,
            "duration_ms": self.duration_ms,
            "status": self.status,
            "attributes": self.attributes,
            "children": [c.to_dict() for c in self.children],
        }


class Tracer:
    """Trace plugin execution."""

    def __init__(self):
        self.root_spans: List[Span] = []
        self.current_span: Optional[Span] = None
        self.metrics = {
            "total_spans": 0,
            "total_duration_ms": 0.0,
            "errors": 0,
            "timeouts": 0,
        }
        self._lock = threading.Lock()

    def start_span(self, name: str, plugin_id: str) -> Span:
        """Start a new span."""
        span = Span(
            name=name,
            plugin_id=plugin_id,
            start_ms=time.time() * 1000,
        )

        with self._lock:
            if self.current_span:
                self.current_span.children.append(span)
            else:
                self.root_spans.append(span)

        return span

    def finish_span(self, span: Span, status: str = "ok"):
        """Finish a span."""
        span.finish(status)
        with self._lock:
            self.metrics["total_spans"] += 1
            self.metrics["total_duration_ms"] += span.duration_ms

            if status == "error":
                self.metrics["errors"] += 1
            elif status == "timeout":
                self.metrics["timeouts"] += 1

    def get_flamegraph(self) -> List[Dict[str, Any]]:
        """Get flamegraph data (sorted by duration)."""
        all_spans = []

        def collect(span: Span, depth: int = 0):
            all_spans.append({
                "name": span.name,
                "plugin_id": span.plugin_id,
                "duration_ms": span.duration_ms,
                "depth": depth,
                "status": span.status,
            })
            for child in span.children:
                collect(child, depth + 1)

        with self._lock:
            for root in self.root_spans:
                collect(root)

        # Sort by duration (longest first)
        return sorted(all_spans, key=lambda s: s["duration_ms"], reverse=True)

    def get_metrics(self) -> Dict[str, Any]:
        """Get tracer metrics."""
        with self._lock:
            avg_duration = (
                self.metrics["total_duration_ms"] / self.metrics["total_spans"]
                if self.metrics["total_spans"] > 0
                else 0.0
            )
            return {
                **self.metrics,
                "avg_duration_ms": avg_duration,
                "error_rate": (
                    self.metrics["errors"] / self.metrics["total_spans"]
                    if self.metrics["total_spans"] > 0
                    else 0.0
                ),
            }

    def export_json(self) -> str:
        """Export traces as JSON."""
        export = {
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": self.get_metrics(),
            "flamegraph": self.get_flamegraph(),
            "traces": [s.to_dict() for s in self.root_spans],
        }
        return json.dumps(export, indent=2)

    def clear(self):
        """Clear all spans."""
        with self._lock:
            self.root_spans.clear()
            self.current_span = None


# Global tracer instance
_tracer = Tracer()


def get_tracer() -> Tracer:
    """Get global tracer."""
    return _tracer


class TracedExecution:
    """Context manager for tracing plugin execution."""

    def __init__(self, name: str, plugin_id: str):
        self.name = name
        self.plugin_id = plugin_id
        self.span: Optional[Span] = None

    def __enter__(self) -> Span:
        tracer = get_tracer()
        self.span = tracer.start_span(self.name, self.plugin_id)
        with tracer._lock:
            tracer.current_span = self.span
        return self.span

    def __exit__(self, exc_type, exc_val, exc_tb):
        tracer = get_tracer()
        if exc_type:
            status = "error" if exc_type != TimeoutError else "timeout"
        else:
            status = "ok"

        if self.span:
            tracer.finish_span(self.span, status)

        with tracer._lock:
            tracer.current_span = None


def trace_plugin_execution(plugin_id: str, operation: str):
    """Decorator to trace plugin execution."""
    def decorator(func):
        async def async_wrapper(*args, **kwargs):
            with TracedExecution(f"{plugin_id}:{operation}", plugin_id) as span:
                span.set_attribute("operation", operation)
                return await func(*args, **kwargs)

        def sync_wrapper(*args, **kwargs):
            with TracedExecution(f"{plugin_id}:{operation}", plugin_id) as span:
                span.set_attribute("operation", operation)
                return func(*args, **kwargs)

        # Return async or sync
        if hasattr(func, "__code__") and "await" in str(func.__code__.co_code):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator
