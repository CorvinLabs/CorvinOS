"""Phase 4: Vibe Plugin Installation API Routes (Console).

FastAPI routes for POST /v1/vibe/plugins/install, GET /v1/vibe/plugins, etc.
Includes marketplace discovery endpoint (ADR-0249 v0.1).
"""

from fastapi import APIRouter, Query
import logging
from typing import Optional, List, Dict, Any
from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Create FastAPI router
router = APIRouter(prefix="/vibe/plugins", tags=["vibe-plugins"])

# Global plugin API handler (injected at app init)
_plugin_api = None

# Hardcoded marketplace for v0.1 (discovery-only)
# In v0.2, this will be backed by a remote registry
_MARKETPLACE_PLUGINS: List[Dict[str, Any]] = [
    {
        "plugin_id": "auth-saml-enterprise",
        "name": "Enterprise SAML Authentication",
        "version": "2.1.0",
        "category": "Authentication",
        "origin": "vetted",
        "author": "Corvin Labs",
        "author_email": "support@corvin.io",
        "description": "Enterprise SAML 2.0 provider integration for CorvinOS.",
        "long_description": "Enables SAML 2.0 single sign-on for enterprise deployments. Supports multiple IdP configurations, attribute mapping, and audit logging.",
        "rating": 4.8,
        "rating_count": 42,
        "download_count": 1250,
        "pii_risk": "medium",
        "locality": "eu_cloud",
        "network_egress": "external",
        "egress_hosts": ["idp.enterprise.example.com"],
        "boot_layer": "bundled",
        "license": "Apache-2.0",
        "repository_url": "https://github.com/corvin-labs/plugin-auth-saml",
        "homepage_url": "https://docs.corvin.io/plugins/auth-saml",
        "dependencies": [],
        "requires_consent": True,
        "listed": True,
    },
    {
        "plugin_id": "monitoring-datadog",
        "name": "Datadog Monitoring",
        "version": "1.5.0",
        "category": "Analytics",
        "origin": "vetted",
        "author": "Corvin Labs",
        "author_email": "support@corvin.io",
        "description": "Real-time monitoring and alerting via Datadog.",
        "long_description": "Integrates CorvinOS metrics with Datadog dashboards and alert pipelines. Supports custom metrics, distributed tracing, and SLO tracking.",
        "rating": 4.6,
        "rating_count": 28,
        "download_count": 890,
        "pii_risk": "low",
        "locality": "us_cloud",
        "network_egress": "external",
        "egress_hosts": ["api.datadoghq.com"],
        "boot_layer": "bundled",
        "license": "Apache-2.0",
        "repository_url": "https://github.com/corvin-labs/plugin-datadog",
        "homepage_url": "https://docs.corvin.io/plugins/datadog",
        "dependencies": [],
        "requires_consent": False,
        "listed": True,
    },
    {
        "plugin_id": "database-postgres-sync",
        "name": "PostgreSQL Sync",
        "version": "3.2.1",
        "category": "Database",
        "origin": "vetted",
        "author": "Corvin Labs",
        "author_email": "support@corvin.io",
        "description": "Bidirectional PostgreSQL data synchronization.",
        "long_description": "Maintains real-time sync between CorvinOS and PostgreSQL databases. Supports CDC (Change Data Capture), conflict resolution, and automatic failover.",
        "rating": 4.9,
        "rating_count": 156,
        "download_count": 4230,
        "pii_risk": "high",
        "locality": "local",
        "network_egress": "local",
        "egress_hosts": [],
        "boot_layer": "installed",
        "license": "Apache-2.0",
        "repository_url": "https://github.com/corvin-labs/plugin-postgres-sync",
        "homepage_url": "https://docs.corvin.io/plugins/postgres-sync",
        "dependencies": ["postgres-driver@14+"],
        "requires_consent": True,
        "listed": True,
    },
    {
        "plugin_id": "security-vault-integration",
        "name": "HashiCorp Vault Integration",
        "version": "2.0.0",
        "category": "Security",
        "origin": "vetted",
        "author": "Corvin Labs",
        "author_email": "support@corvin.io",
        "description": "Secrets management via HashiCorp Vault.",
        "long_description": "Secure credential storage and rotation. Supports dynamic secrets, audit logging, and multi-cloud deployments.",
        "rating": 4.7,
        "rating_count": 89,
        "download_count": 2100,
        "pii_risk": "high",
        "locality": "local",
        "network_egress": "local",
        "egress_hosts": [],
        "boot_layer": "core",
        "license": "Apache-2.0",
        "repository_url": "https://github.com/corvin-labs/plugin-vault",
        "homepage_url": "https://docs.corvin.io/plugins/vault",
        "dependencies": [],
        "requires_consent": False,
        "listed": True,
    },
    {
        "plugin_id": "tooling-terraform-state",
        "name": "Terraform State Bridge",
        "version": "1.1.0",
        "category": "Tooling",
        "origin": "vetted",
        "author": "Corvin Labs",
        "author_email": "support@corvin.io",
        "description": "Infrastructure-as-Code state synchronization.",
        "long_description": "Sync Terraform state with CorvinOS resource graph. Enables drift detection and policy enforcement.",
        "rating": 4.3,
        "rating_count": 34,
        "download_count": 680,
        "pii_risk": "low",
        "locality": "local",
        "network_egress": "local",
        "egress_hosts": [],
        "boot_layer": "installed",
        "license": "Apache-2.0",
        "repository_url": "https://github.com/corvin-labs/plugin-terraform",
        "homepage_url": "https://docs.corvin.io/plugins/terraform",
        "dependencies": ["terraform@1.5+"],
        "requires_consent": False,
        "listed": True,
    },
]

def set_plugin_api(api):
    """Inject plugin API handler."""
    global _plugin_api
    _plugin_api = api

def get_plugin_api():
    """Get plugin API handler."""
    return _plugin_api

@router.post("/install")
async def install_plugin(body: Dict[str, Any]):
    """
    Install plugin from manifest.

    Request:
    {
        "manifest_url": "file:///path/to/plugin.json",
        "or": "manifest_json": "{...}"
    }

    Response:
    {
        "plugin_id": "my_plugin",
        "status": "success",
        "message": "Plugin installed",
        "manifest": {...}
    }
    """
    from ...vibe_engineering.plugin_api import PluginInstallRequest

    try:
        api = get_plugin_api()
        if not api:
            return {"error": "Plugin API not initialized"}

        install_req = PluginInstallRequest.from_request_body(body)
        response = await api.install_plugin(install_req)

        return response.to_dict()

    except Exception as e:
        logger.error(f"Install failed: {e}")
        return {"error": str(e)}

@router.get("/list")
async def list_plugins():
    """
    List installed plugins.

    Response:
    {
        "loaded": [...],
        "failed": {...},
        "count_total": 5
    }
    """
    try:
        api = get_plugin_api()
        if not api:
            return {"error": "Plugin API not initialized"}

        result = await api.list_plugins()
        return result

    except Exception as e:
        logger.error(f"List failed: {e}")
        return {"error": str(e)}

@router.get("/{plugin_id}")
async def get_plugin(plugin_id: str):
    """Get plugin details."""
    try:
        api = get_plugin_api()
        if not api:
            return {"error": "Plugin API not initialized"}

        plugin = await api.get_plugin(plugin_id)
        if not plugin:
            return {"error": f"Plugin {plugin_id} not found"}

        return plugin

    except Exception as e:
        logger.error(f"Get plugin failed: {e}")
        return {"error": str(e)}

@router.post("/{plugin_id}/disable")
async def disable_plugin(plugin_id: str):
    """Disable plugin."""
    try:
        api = get_plugin_api()
        response = await api.disable_plugin(plugin_id)
        return response.to_dict()
    except Exception as e:
        return {"error": str(e)}

@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str):
    """Uninstall plugin."""
    try:
        api = get_plugin_api()
        response = await api.uninstall_plugin(plugin_id)
        return response.to_dict()
    except Exception as e:
        return {"error": str(e)}

@router.post("/{plugin_id}/report")
async def report_plugin(plugin_id: str, body: Dict[str, Any]):
    """
    Report a plugin as inappropriate or malicious.

    Request:
    {
        "reason": "malicious|inappropriate|permission_abuse|misrepresentation|other",
        "details": "Description of the issue"
    }

    Response:
    {
        "status": "success",
        "message": "Report submitted",
        "report_id": "uuid-here"
    }

    ADR-0249: Plugin Trust Anchor — allows community to flag malicious plugins.
    """
    try:
        from pathlib import Path
        import sys
        import uuid

        # Import audit trail
        _THIS_DIR = Path(__file__).resolve().parent.parent
        _CONSOLE_AUDIT = _THIS_DIR / "audit.py"
        if str(_THIS_DIR) not in sys.path:
            sys.path.insert(0, str(_THIS_DIR))

        # Validate request
        reason = body.get("reason", "").strip()
        details = body.get("details", "").strip()

        if not reason:
            return {"error": "Missing reason field"}

        if not details:
            return {"error": "Missing details field"}

        if len(details) < 10 or len(details) > 500:
            return {"error": "Details must be 10-500 characters"}

        valid_reasons = {
            "malicious",
            "inappropriate",
            "permission_abuse",
            "misrepresentation",
            "other",
        }
        if reason not in valid_reasons:
            return {"error": f"Invalid reason. Must be one of {valid_reasons}"}

        # Generate report ID
        report_id = str(uuid.uuid4())

        # Write audit event (metadata-only: no details, just reason)
        try:
            from forge import security_events
            security_events.write_event(
                event_type="plugin.reported",
                details={
                    "plugin_id": plugin_id,
                    "reason": reason,
                    "report_id": report_id,
                },
            )
        except Exception as audit_err:
            logger.error(f"Failed to write audit event: {audit_err}")
            # Don't fail the API call — audit failure is logged but not blocking

        logger.info(f"Plugin {plugin_id} reported: {reason} (report_id={report_id})")

        return {
            "status": "success",
            "message": "Report submitted. Thank you for reporting this plugin.",
            "report_id": report_id,
        }

    except Exception as e:
        logger.error(f"Report plugin failed: {e}")
        return {"error": str(e)}

@router.get("/marketplace")
async def list_marketplace(
    category: Optional[str] = Query(None),
    query: Optional[str] = Query(None),
    origin: Optional[str] = Query(None),
    sort: str = Query("rating"),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    """
    Discover available plugins in the marketplace.

    Query parameters:
    - category: Filter by category (Authentication, Performance, Security, Database, Integration, UI, Analytics, Tooling)
    - query: Search in name/description
    - origin: Filter by origin (vetted, community, builtin)
    - sort: Sort by rating|downloads|recent (default: rating)
    - limit: Max results (default: 20, max: 100)
    - offset: Pagination offset (default: 0)

    Response:
    {
        "plugins": [
            {
                "plugin_id": "...",
                "name": "...",
                "version": "...",
                "category": "...",
                "origin": "vetted|community",
                "author": "...",
                "description": "...",
                "rating": 4.8,
                "rating_count": 42,
                "download_count": 1250,
                "pii_risk": "none|low|medium|high",
                "locality": "local|eu_cloud|us_cloud",
                "network_egress": "none|local|external",
                "egress_hosts": ["..."],
                "trust_badge": "verified|community|unverified",
                "listed": true
            }
        ],
        "total": 42,
        "limit": 20,
        "offset": 0
    }

    ADR-0249 v0.1: Discovery-only marketplace. Install-from-catalog deferred to v0.2.
    """
    try:
        # Normalize query parameters
        category_filter = (category or "").strip().lower()
        query_filter = (query or "").strip().lower()
        origin_filter = (origin or "").strip().lower()

        # Start with all marketplace plugins
        results = []
        for plugin in _MARKETPLACE_PLUGINS:
            # Filter by category
            if category_filter and plugin.get("category", "").lower() != category_filter:
                continue

            # Filter by origin
            if origin_filter and plugin.get("origin", "").lower() != origin_filter:
                continue

            # Filter by search query
            if query_filter:
                name = plugin.get("name", "").lower()
                description = plugin.get("description", "").lower()
                long_desc = plugin.get("long_description", "").lower()
                if query_filter not in name and query_filter not in description and query_filter not in long_desc:
                    continue

            # Must be listed
            if not plugin.get("listed", True):
                continue

            # Compute trust badge
            plugin_origin = plugin.get("origin", "community")
            if plugin_origin == "vetted":
                trust_badge = "verified"
            elif plugin_origin == "builtin":
                trust_badge = "verified"
            else:
                trust_badge = "community"

            # Add badge to result
            result_plugin = plugin.copy()
            result_plugin["trust_badge"] = trust_badge

            results.append(result_plugin)

        # Sort results
        if sort == "rating":
            results.sort(key=lambda p: (-p.get("rating", 0), -p.get("download_count", 0)))
        elif sort == "downloads":
            results.sort(key=lambda p: (-p.get("download_count", 0), -p.get("rating", 0)))
        elif sort == "recent":
            results.sort(key=lambda p: p.get("plugin_id", ""))  # Stub for now

        # Apply pagination
        total = len(results)
        paginated = results[offset:offset + limit]

        return {
            "plugins": paginated,
            "total": total,
            "limit": limit,
            "offset": offset,
        }

    except ValueError as e:
        logger.warning(f"Invalid query parameter: {e}")
        return {"error": f"Invalid query parameter: {e}"}
    except Exception as e:
        logger.error(f"Marketplace list failed: {e}")
        return {"error": str(e)}
