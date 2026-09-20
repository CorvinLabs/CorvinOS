"""Console Dispatcher — Central Request Orchestrator (M1 ADR-0954)

Main orchestration logic for routing console requests to renderers with:
- Token validation (fail-closed auth)
- Timeout enforcement
- Audit trail emission
- Fallback chain support (M2–M6)
"""

import asyncio
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, Optional
import hashlib

from core.dispatch.token import DispatcherAuthError, TokenPayload, TokenStore
from core.dispatch.audit_event import DispatcherAuditEvent, AuditEventType
from core.console.models import ConsoleRequest, ConsoleResponse


class DispatcherError(Exception):
    """Base exception for dispatcher errors."""
    pass


class DispatcherTimeout(DispatcherError):
    """Renderer execution timed out."""
    pass


class DispatcherNotFound(DispatcherError):
    """Renderer not found in registry."""
    pass


class ConsoleDispatcher:
    """Central orchestrator for console rendering requests.

    Pipeline:
    1. Validate token (fail-closed on auth error)
    2. Look up renderer in registry
    3. Execute with timeout + error handling
    4. Emit audit event
    5. Return response or raise error
    """

    def __init__(
        self,
        token_store: TokenStore,
        audit_emitter: Callable[[DispatcherAuditEvent], Awaitable[None]],
        timeout_seconds: int = 30,
    ):
        """Initialize dispatcher.

        Args:
            token_store: TokenStore implementation (InMemoryTokenStore or Redis)
            audit_emitter: Async function to emit audit events
            timeout_seconds: Request timeout (default 30s)
        """
        self.token_store = token_store
        self.audit_emitter = audit_emitter
        self.timeout_seconds = timeout_seconds
        self.renderers: Dict[str, Any] = {}  # {renderer_name: RendererBase}

    def register_renderer(self, renderer_name: str, renderer: Any) -> None:
        """Register a renderer in the dispatcher.

        Args:
            renderer_name: Renderer identifier ("slide", "svg", "chart", etc.)
            renderer: Renderer instance (must implement RendererBase protocol)
        """
        self.renderers[renderer_name] = renderer

    async def dispatch(self, request: ConsoleRequest) -> ConsoleResponse:
        """Main entry point: orchestrate request → render → response.

        Steps:
        1. Emit CONSOLE_REQUEST_RECEIVED audit event
        2. Validate token via token_store.validate()
        3. Lookup renderer
        4. Execute renderer with timeout
        5. Emit success/failure audit event
        6. Return response

        Raises:
            DispatcherAuthError: Invalid/expired token
            DispatcherNotFound: Renderer not registered
            DispatcherTimeout: Renderer execution timeout
        """
        start_time = datetime.utcnow()

        try:
            # Step 1: Log request received
            await self._emit_audit(
                request=request,
                event_type=AuditEventType.CONSOLE_REQUEST_RECEIVED,
                status="received",
            )

            # Step 2: Validate token (fail-closed)
            token_payload = await self.token_store.validate(request.token)
            await self._emit_audit(
                request=request,
                event_type=AuditEventType.CONSOLE_TOKEN_VALIDATED,
                status="validated",
                token_payload=token_payload,
            )

            # Step 3: Lookup renderer
            renderer = self.renderers.get(request.renderer)
            if not renderer:
                await self._emit_audit(
                    request=request,
                    event_type=AuditEventType.DISPATCH_NOT_FOUND,
                    status="failed",
                    error=f"Renderer {request.renderer} not found",
                )
                raise DispatcherNotFound(f"Renderer {request.renderer} not found")

            # Step 4: Execute with timeout
            try:
                output = await asyncio.wait_for(
                    renderer.execute(request),
                    timeout=self.timeout_seconds,
                )
            except asyncio.TimeoutError:
                await self._emit_audit(
                    request=request,
                    event_type=AuditEventType.DISPATCH_TIMEOUT,
                    status="timeout",
                    latency_ms=int((datetime.utcnow() - start_time).total_seconds() * 1000),
                )
                raise DispatcherTimeout(f"Renderer {request.renderer} timed out after {self.timeout_seconds}s")

            # Step 5: Emit success audit event
            latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            await self._emit_audit(
                request=request,
                event_type=AuditEventType.DISPATCH_ROUTED,
                status="success",
                latency_ms=latency_ms,
                output=output,
            )

            # Step 6: Return response
            return ConsoleResponse(
                request_id=request.request_id,
                status="success",
                output=output,
                latency_ms=latency_ms,
                audit_event_id="audit_event_id_placeholder",  # M1: filled by audit_emitter
            )

        except DispatcherAuthError as e:
            await self._emit_audit(
                request=request,
                event_type=AuditEventType.CONSOLE_TOKEN_INVALID,
                status="denied",
                error=str(e),
            )
            raise

        except Exception as e:
            latency_ms = int((datetime.utcnow() - start_time).total_seconds() * 1000)
            await self._emit_audit(
                request=request,
                event_type=AuditEventType.DISPATCH_CONFIG_ERROR,
                status="failed",
                error=str(e),
                latency_ms=latency_ms,
            )
            raise

    async def _emit_audit(
        self,
        request: ConsoleRequest,
        event_type: AuditEventType,
        status: str,
        error: Optional[str] = None,
        latency_ms: int = 0,
        output: Optional[Dict[str, Any]] = None,
        token_payload: Optional[TokenPayload] = None,
    ) -> None:
        """Emit audit event for dispatcher decision.

        Creates DispatcherAuditEvent with:
        - Event type + status
        - Input/output hashing (SHA256)
        - Tenant isolation (request.tenant_id)
        - Latency measurement

        Audit events are immutable + hash-chained (ADR-0232).
        """
        input_hash = hashlib.sha256(str(request.payload).encode()).hexdigest()
        output_hash = hashlib.sha256(str(output).encode()).hexdigest() if output else None

        event = DispatcherAuditEvent(
            event_type=event_type,
            dispatcher_id="console_dispatcher",
            dispatcher_version="1.0.0",
            tenant_id=request.tenant_id,
            input_hash=input_hash,
            output_hash=output_hash,
            latency_ms=latency_ms,
            status=status,
            error_type=type(Exception).__name__ if error else None,
        )

        # Emit audit event (async, non-blocking)
        try:
            await self.audit_emitter(event)
        except Exception as audit_error:
            # Audit failure should not block dispatch (but should be logged)
            # In production, this would be logged to stderr or emergency audit fallback
            pass
