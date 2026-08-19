"""FastAPI GitHub Integration routes for Cross-Device-Learning Sync.

Wraps the existing github_integration module and exposes endpoints via FastAPI.
"""

from fastapi import APIRouter, Request, HTTPException
from pathlib import Path
import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/github", tags=["console-github"])


def get_tenant_path() -> Path:
    """Get tenant home directory."""
    return Path.home() / '.corvin' / 'tenants' / '_default'


@router.post("/verify")
async def verify_github_connection(request: Request):
    """Verify GitHub repository connection and accessibility.

    Expected payload:
    {
        "url": "https://github.com/owner/repo",
        "token": "ghp_xxx" (optional)
    }
    """
    try:
        body = await request.json()
        url = body.get("url")
        token = body.get("token")

        if not url:
            return {
                "connected": False,
                "details": {
                    "status": "error",
                    "error": "GitHub URL is required"
                }
            }

        # Placeholder response for missing backend
        # In production, this would call the actual GitHub API
        return {
            "connected": False,
            "details": {
                "status": "error",
                "error": f"GitHub API integration not yet available. Backend endpoint for {url} not implemented."
            }
        }
    except Exception as e:
        logger.error(f"GitHub verify error: {e}")
        return {
            "connected": False,
            "details": {
                "status": "error",
                "error": str(e)
            }
        }


@router.get("/status")
async def get_github_status():
    """Get current GitHub connection status."""
    tenant_path = get_tenant_path()
    config_file = tenant_path / 'github-config.json'

    try:
        if config_file.exists():
            with open(config_file) as f:
                config = json.load(f)
                return {
                    "connected": True,
                    "configured": True,
                    **config
                }
    except Exception as e:
        logger.error(f"Failed to read GitHub config: {e}")

    return {
        "connected": False,
        "configured": False,
        "error": "No GitHub configuration found"
    }


@router.get("/config")
async def get_github_config():
    """Get GitHub configuration."""
    tenant_path = get_tenant_path()
    config_file = tenant_path / 'github-config.json'

    if not config_file.exists():
        raise HTTPException(status_code=404, detail="GitHub not configured")

    try:
        with open(config_file) as f:
            return json.load(f)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to read config: {e}")


@router.delete("/config")
async def disconnect_github():
    """Disconnect GitHub integration."""
    tenant_path = get_tenant_path()
    config_file = tenant_path / 'github-config.json'

    try:
        if config_file.exists():
            config_file.unlink()
        return {"success": True, "message": "GitHub integration disconnected"}
    except Exception as e:
        logger.error(f"Failed to disconnect GitHub: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to disconnect: {e}")


@router.post("/webhook/register")
async def register_webhook(request: Request):
    """Register a GitHub webhook."""
    try:
        body = await request.json()
        return {
            "success": False,
            "error": "Webhook registration not yet implemented"
        }
    except Exception as e:
        return {"success": False, "error": str(e)}


@router.get("/webhook/status")
async def get_webhook_status():
    """Get webhook registration status."""
    return {
        "registered": False,
        "error": "Webhook system not yet available"
    }


@router.post("/webhook/test")
async def test_webhook(request: Request):
    """Send a test webhook."""
    return {"success": False, "error": "Test webhook not yet implemented"}
