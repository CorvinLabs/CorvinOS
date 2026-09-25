"""
Discovery Relay Service - Core FastAPI Application.

Enables instance registration, discovery, and heartbeat management.
All operations are tenant-scoped and audit-logged.
"""

from fastapi import FastAPI, HTTPException, Depends, Request, Header
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from typing import List, Optional, Dict
from datetime import datetime, timezone
import logging
import asyncio
import os
import base64

# Audit event emission (optional for tests, required for production)
try:
    from corvin_operator.forge.forge.security_events import write_event
except ImportError:
    write_event = None  # Mock if not available
from .catalog import CatalogBackend, InMemoryCatalogBackend, DiscoveredInstance
from .security import (
    validate_hmac, compute_hmac, decrypt_payload, encrypt_payload,
    AuthenticationError, scrub_for_audit
)

logger = logging.getLogger(__name__)


# Pydantic schemas
class RegisterRequest(BaseModel):
    """Instance registration request."""
    instance_id: str = Field(..., description="Unique instance identifier")
    endpoint: str = Field(..., description="Instance endpoint URL")
    tier_enc: str = Field(..., description="AES-256-GCM encrypted tier+kid")
    tier_enc_nonce: str = Field(..., description="Base64-encoded nonce for tier_enc")
    kid: str = Field(..., description="Key ID from org_jwt claims")
    latency_ms: int = Field(default=0, description="Measured latency to relay in milliseconds")


class HeartbeatRequest(BaseModel):
    """Instance heartbeat request."""
    instance_id: str = Field(..., description="Instance identifier")
    latency_ms: int = Field(default=0, description="Current latency measurement")


class CatalogEntry(BaseModel):
    """An instance in the catalog response."""
    instance_id: str
    endpoint: str
    latency_ms: int
    last_heartbeat: str  # ISO 8601
    state: str


class CatalogResponse(BaseModel):
    """Catalog query response."""
    org_id: str
    instances: List[CatalogEntry]
    total: int


class HealthResponse(BaseModel):
    """Health check response."""
    status: str
    active_instances: int
    retired_instances: int
    orgs_count: int


class DiscoveryRelay:
    """Discovery relay service controller."""

    def __init__(self, catalog: CatalogBackend, tenant_id: str = "_default"):
        self.catalog = catalog
        self.tenant_id = tenant_id
        self._housekeeping_task: Optional[asyncio.Task] = None
        # Org keys (org_id -> 32-byte key). In production, load from Vault.
        self._org_keys: Dict[str, bytes] = {}

    def set_org_key(self, org_id: str, key: bytes) -> None:
        """Register an organization's signing/encryption key."""
        if len(key) != 32:
            raise ValueError(f"org_key must be 32 bytes, got {len(key)}")
        self._org_keys[org_id] = key

    def get_org_key(self, org_id: str) -> Optional[bytes]:
        """Get an organization's key."""
        return self._org_keys.get(org_id)

    async def start_housekeeping(self, interval_sec: int = 30, ttl_sec: int = 300):
        """Start the housekeeping loop (mark stale, delete old retired)."""
        async def housekeeping_loop():
            while True:
                try:
                    await asyncio.sleep(interval_sec)
                    # Retire stale instances (no heartbeat >ttl_sec)
                    retired_count = await self.catalog.retire_stale(ttl_sec)
                    # Delete old retired instances (>1h)
                    deleted_count = await self.catalog.delete_old_retired(age_seconds=3600)
                    if retired_count > 0 or deleted_count > 0:
                        logger.info(
                            f"Housekeeping: retired={retired_count}, deleted={deleted_count}",
                            extra={"tenant_id": self.tenant_id}
                        )
                        # Emit audit event (if available)
                        if write_event:
                            try:
                                from pathlib import Path
                                audit_path = Path.home() / ".corvin" / "tenants" / self.tenant_id / "global" / "audit.jsonl"
                                write_event(
                                    audit_path,
                                    event_type="discovery.housekeeping_complete",
                                    details={
                                        "retired_count": retired_count,
                                        "deleted_count": deleted_count,
                                    }
                                )
                            except Exception as e:
                                logger.warning(f"Failed to emit audit event: {e}")
                except Exception as e:
                    logger.error(f"Housekeeping error: {e}")

        self._housekeeping_task = asyncio.create_task(housekeeping_loop())
        logger.info("Housekeeping loop started")

    async def stop_housekeeping(self):
        """Stop the housekeeping loop."""
        if self._housekeeping_task:
            self._housekeeping_task.cancel()
            try:
                await self._housekeeping_task
            except asyncio.CancelledError:
                pass
        logger.info("Housekeeping loop stopped")

    async def handle_register(self, org_id: str, req: RegisterRequest, auth_signature: str) -> Dict:
        """
        Handle instance registration.

        Steps:
        1. Validate HMAC signature (Authorization: Bearer header)
        2. Decrypt tier_enc to verify kid matches
        3. Register instance in catalog
        4. Emit audit event
        """
        # Get org key
        org_key = self.get_org_key(org_id)
        if not org_key:
            logger.warning(f"Unknown org_id: {org_id}")
            raise HTTPException(status_code=403, detail="Unknown organization")

        # Validate HMAC signature
        # Signature is computed over the JSON payload (excluding auth header itself)
        payload_for_sig = b'{"instance_id":"' + req.instance_id.encode() + b'"}'
        if not validate_hmac(payload_for_sig, auth_signature, org_key):
            logger.warning(f"Invalid HMAC signature for org_id={org_id}")
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Decrypt tier_enc to verify kid
        decrypted = decrypt_payload(req.tier_enc, req.tier_enc_nonce, org_key)
        if not decrypted:
            logger.warning(f"Decryption failed for org_id={org_id}, instance_id={req.instance_id}")
            raise HTTPException(status_code=400, detail="Decryption failed")

        # Verify kid in decrypted payload matches the one provided
        if decrypted.get("kid") != req.kid:
            logger.warning(f"Kid mismatch for org_id={org_id}, instance_id={req.instance_id}")
            raise HTTPException(status_code=400, detail="Kid mismatch")

        # Create instance record
        instance = DiscoveredInstance(
            org_id=org_id,
            instance_id=req.instance_id,
            endpoint=req.endpoint,
            tier_enc=req.tier_enc,
            kid=req.kid,
            latency_ms=req.latency_ms,
            last_heartbeat=datetime.now(timezone.utc),
            state="ACTIVE",
        )

        # Register in catalog
        await self.catalog.register(org_id, req.instance_id, instance)

        # Emit audit event (if available)
        if write_event:
            try:
                from pathlib import Path
                audit_path = Path.home() / ".corvin" / "tenants" / self.tenant_id / "global" / "audit.jsonl"
                write_event(
                    audit_path,
                    event_type="discovery.instance_registered",
                    details=scrub_for_audit({
                        "org_id": org_id,
                        "instance_id": req.instance_id,
                        "endpoint": req.endpoint,
                        "kid": req.kid,
                        "latency_ms": req.latency_ms,
                    })
                )
            except Exception as e:
                logger.warning(f"Failed to emit audit event: {e}")

        return {"status": "registered", "instance_id": req.instance_id}

    async def handle_query(self, org_id: str) -> CatalogResponse:
        """Query instances in the catalog."""
        # Get org key (optional for query, but log it)
        org_key = self.get_org_key(org_id)
        if not org_key:
            logger.warning(f"Query from unknown org_id: {org_id}")

        instances = await self.catalog.query(org_id)
        entries = [
            CatalogEntry(
                instance_id=i.instance_id,
                endpoint=i.endpoint,
                latency_ms=i.latency_ms,
                last_heartbeat=i.last_heartbeat.isoformat(),
                state=i.state,
            )
            for i in instances
        ]

        # Emit audit event (if available)
        if write_event:
            try:
                from pathlib import Path
                audit_path = Path.home() / ".corvin" / "tenants" / self.tenant_id / "global" / "audit.jsonl"
                write_event(
                    audit_path,
                    event_type="discovery.catalog_queried",
                    details={"org_id": org_id, "count": len(entries)}
                )
            except Exception as e:
                logger.warning(f"Failed to emit audit event: {e}")

        return CatalogResponse(org_id=org_id, instances=entries, total=len(entries))

    async def handle_heartbeat(self, org_id: str, req: HeartbeatRequest, auth_signature: str) -> Dict:
        """Handle instance heartbeat."""
        # Validate HMAC signature
        org_key = self.get_org_key(org_id)
        if not org_key:
            raise HTTPException(status_code=403, detail="Unknown organization")

        payload_for_sig = b'{"instance_id":"' + req.instance_id.encode() + b'"}'
        if not validate_hmac(payload_for_sig, auth_signature, org_key):
            raise HTTPException(status_code=401, detail="Invalid signature")

        # Refresh heartbeat
        updated = await self.catalog.heartbeat(org_id, req.instance_id)
        if not updated:
            raise HTTPException(status_code=404, detail="Instance not found")

        # Emit audit event (if available)
        if write_event:
            try:
                from pathlib import Path
                audit_path = Path.home() / ".corvin" / "tenants" / self.tenant_id / "global" / "audit.jsonl"
                write_event(
                    audit_path,
                    event_type="discovery.instance_heartbeat",
                    details={
                        "org_id": org_id,
                        "instance_id": req.instance_id,
                        "latency_ms": req.latency_ms,
                    }
                )
            except Exception as e:
                logger.warning(f"Failed to emit audit event: {e}")

        return {"status": "heartbeat_received", "instance_id": req.instance_id}

    async def handle_health(self) -> HealthResponse:
        """Health check endpoint."""
        stats = await self.catalog.get_stats()
        return HealthResponse(
            status="healthy",
            active_instances=stats["active_instances"],
            retired_instances=stats["retired_instances"],
            orgs_count=stats["orgs_count"],
        )


def create_relay_app(
    catalog: Optional[CatalogBackend] = None,
    tenant_id: str = "_default",
) -> tuple[FastAPI, DiscoveryRelay]:
    """
    Create and configure the FastAPI relay app.

    Returns:
        Tuple of (FastAPI app, DiscoveryRelay controller)
    """
    if catalog is None:
        catalog = InMemoryCatalogBackend()

    relay = DiscoveryRelay(catalog, tenant_id=tenant_id)
    app = FastAPI(title="Discovery Relay", version="1.0.0")

    # Enable CORS (restrict in production)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],  # TODO: restrict to known instances
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )

    # Dependency: extract org_id from path
    async def get_org_id(request: Request) -> str:
        org_id = request.path_params.get("org_id")
        if not org_id:
            raise HTTPException(status_code=400, detail="org_id required")
        return org_id

    # Dependency: extract Authorization header
    async def get_auth_signature(authorization: Optional[str] = Header(None)) -> str:
        if not authorization or not authorization.startswith("Bearer "):
            return ""
        return authorization[7:]  # Remove "Bearer " prefix

    # Endpoints
    @app.post("/discovery/{org_id}/register")
    async def register(
        org_id: str,
        req: RegisterRequest,
        auth_sig: str = Depends(get_auth_signature),
    ):
        return await relay.handle_register(org_id, req, auth_sig)

    @app.get("/discovery/{org_id}/catalog")
    async def query(org_id: str):
        return await relay.handle_query(org_id)

    @app.post("/discovery/{org_id}/heartbeat")
    async def heartbeat(
        org_id: str,
        req: HeartbeatRequest,
        auth_sig: str = Depends(get_auth_signature),
    ):
        return await relay.handle_heartbeat(org_id, req, auth_sig)

    @app.get("/health")
    async def health():
        return await relay.handle_health()

    # Lifespan for housekeeping
    @app.on_event("startup")
    async def startup():
        await relay.start_housekeeping()

    @app.on_event("shutdown")
    async def shutdown():
        await relay.stop_housekeeping()

    return app, relay
