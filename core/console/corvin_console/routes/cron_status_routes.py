"""Cron Service Status Routes — monitor and control scheduled polling.

Endpoints:
- GET /v1/console/cron/status — Last poll time, interval, enabled state
- POST /v1/console/cron/trigger-now — Manual trigger (blocking)
- POST /v1/console/cron/trigger-now-all — Manual trigger for all tenants
- POST /v1/console/cron/pause — Pause scheduled polling
- POST /v1/console/cron/resume — Resume paused polling

ADR-0613: Loss signals feed autonomous forge loop.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from .. import auth as session_auth
from ..deps import require_session, require_csrf

from corvin_operator.skill_forge.automation import get_cron_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/cron/status")
def cron_status(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return cron service status.

    Returns:
        dict: {running, paused, last_poll_time, poll_interval_minutes, last_poll_count}
    """
    try:
        service = get_cron_service()
        status_dict = service.get_status()
        status_dict["tenant_id"] = rec.tenant_id
        status_dict["ts"] = time.time()
        return status_dict
    except Exception as e:
        logger.error(f"Error getting cron status: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get cron status: {e}",
        )


@router.post("/cron/trigger-now")
def cron_trigger_now(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Manually trigger poll for a specific tenant (blocking).

    Args:
        tenant_id: Tenant to poll (defaults to session tenant)

    Returns:
        dict: {tenant_id, loss_signals, timestamp, elapsed_seconds}

    Side effects:
        - Runs cron poller immediately
        - Emits audit events for any loss signals
    """
    target_tenant = tenant_id or rec.tenant_id

    try:
        service = get_cron_service()
        result = service.trigger_now(target_tenant)
        result["tenant_id"] = rec.tenant_id
        result["ts"] = time.time()
        return result
    except Exception as e:
        logger.error(f"Error triggering cron poll: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger cron poll: {e}",
        )


@router.post("/cron/trigger-now-all")
def cron_trigger_now_all(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Manually trigger poll for all active tenants (blocking).

    Returns:
        dict: {total_loss_signals, timestamp, elapsed_seconds}

    Side effects:
        - Runs cron poller for all tenants immediately
        - Emits audit events for any loss signals
    """
    try:
        service = get_cron_service()
        result = service.trigger_now_all()
        result["tenant_id"] = rec.tenant_id
        result["ts"] = time.time()
        return result
    except Exception as e:
        logger.error(f"Error triggering all cron polls: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to trigger cron polls: {e}",
        )


@router.post("/cron/pause")
def cron_pause(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Pause scheduled cron polling (operator control).

    Returns:
        dict: {paused, timestamp}

    Side effects:
        - Removes cron job from scheduler
        - Sets paused flag
    """
    try:
        service = get_cron_service()
        service.pause()
        return {
            "paused": True,
            "tenant_id": rec.tenant_id,
            "ts": time.time(),
        }
    except Exception as e:
        logger.error(f"Error pausing cron service: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to pause cron service: {e}",
        )


@router.post("/cron/resume")
def cron_resume(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Resume paused scheduled polling (operator control).

    Returns:
        dict: {paused, timestamp}

    Side effects:
        - Re-registers cron job with scheduler
        - Clears paused flag
    """
    try:
        service = get_cron_service()
        service.resume()
        return {
            "paused": False,
            "tenant_id": rec.tenant_id,
            "ts": time.time(),
        }
    except Exception as e:
        logger.error(f"Error resuming cron service: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to resume cron service: {e}",
        )
