"""Console Request/Response Models (M1 ADR-0954)

Data structures for console-plugin request routing pipeline.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional
import uuid


@dataclass
class ConsoleRequest:
    """Request to render console content via dispatcher.

    Mandatory fields:
    - request_id: Unique request identifier (audit trail linking)
    - tenant_id: Tenant scope (ADR-0007, fail-closed if missing)
    - token: Token string (validated by dispatcher before dispatch)
    - renderer: Renderer name ("slide", "svg", "chart", "blender", "screencast")
    - payload: Renderer-specific data dict
    - timestamp: Request creation time (audit trail)
    """
    request_id: str
    tenant_id: str  # Mandatory — ADR-0007 isolation
    token: str
    renderer: str  # "slide", "svg", "chart", "blender", "screencast"
    payload: Dict[str, Any]
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        """Validate mandatory fields (fail-closed)."""
        if not self.request_id:
            raise ValueError("request_id is mandatory")
        if not self.tenant_id:
            raise ValueError("tenant_id is mandatory (ADR-0007)")
        if not self.token:
            raise ValueError("token is mandatory")
        if not self.renderer:
            raise ValueError("renderer is mandatory")


@dataclass
class ConsoleResponse:
    """Response from dispatcher after rendering attempt.

    Carries outcome (success/failed/timeout) + audit trail link.
    """
    request_id: str
    status: str  # "success", "failed", "timeout", "denied"
    output: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    latency_ms: int = 0
    audit_event_id: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def is_success(self) -> bool:
        return self.status == "success"

    def is_failed(self) -> bool:
        return self.status in ("failed", "timeout", "denied")


@dataclass
class ConsoleAsyncResponse:
    """Async response for long-running renderers (M5+ Blender).

    Returns job_id + status + polling URL instead of immediate output.
    """
    request_id: str
    job_id: str
    status: str  # "queued", "processing", "ready", "failed"
    estimated_wait_seconds: int = 0
    polling_url: Optional[str] = None
    audit_event_id: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)
