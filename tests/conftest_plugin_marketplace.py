"""
Shared Fixtures for Plugin Marketplace Integration Tests (ADR-0249).

Provides reusable fixtures for:
- Marketplace setup
- Registry initialization
- Audit trail mocking
- Multi-tenant test contexts
- Plugin factory functions
"""

import pytest
import json
import tempfile
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Any, Optional
import uuid


# ============================================================================
# Marketplace Fixtures
# ============================================================================

@pytest.fixture
def marketplace_config() -> Dict[str, Any]:
    """Marketplace configuration for testing."""
    return {
        "max_plugins": 1000,
        "max_plugin_size_mb": 100,
        "governance_low_rating_threshold": 2.0,
        "governance_review_count_threshold": 5,
        "governance_report_threshold": 3,
        "revenue_author_percent": 0.70,
        "revenue_corvin_percent": 0.20,
        "revenue_ecosystem_percent": 0.10,
    }


@pytest.fixture
def temp_marketplace_home():
    """Create temporary CorvinOS home directory for marketplace testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corvin_home = Path(tmpdir) / ".corvin"
        corvin_home.mkdir(parents=True, exist_ok=True)
        (corvin_home / "global").mkdir(exist_ok=True)
        (corvin_home / "plugins").mkdir(exist_ok=True)
        (corvin_home / "audit.jsonl").touch()
        yield corvin_home


# ============================================================================
# Registry Fixtures
# ============================================================================

@pytest.fixture
def registry_state() -> Dict[str, Any]:
    """Initialize empty registry state."""
    return {
        "version": "1.0",
        "installed": [],
        "failed": {},
        "metadata": {
            "created_at": datetime.utcnow().isoformat(),
            "last_modified": datetime.utcnow().isoformat(),
        }
    }


@pytest.fixture
def plugin_entry_factory():
    """Factory for creating plugin registry entries."""
    def _create_entry(
        plugin_id: str,
        name: str = None,
        version: str = "1.0.0",
        repo: str = None,
        commit_hash: str = None,
        status: str = "active",
    ) -> Dict[str, Any]:
        return {
            "id": plugin_id,
            "name": name or plugin_id,
            "version": version,
            "repo": repo or f"file:///tmp/{plugin_id}",
            "commit_hash": commit_hash or "a" * 40,
            "installed_at": int(datetime.utcnow().timestamp()),
            "status": status,
        }

    return _create_entry


# ============================================================================
# Audit Trail Fixtures
# ============================================================================

@pytest.fixture
def audit_event_factory():
    """Factory for creating audit trail events."""
    def _create_event(
        event_type: str,
        details: Dict[str, Any] = None,
        timestamp: str = None,
        audit_hash: str = None,
    ) -> Dict[str, Any]:
        return {
            "timestamp": timestamp or datetime.utcnow().isoformat(),
            "event_type": event_type,
            "details": details or {},
            "audit_hash": audit_hash or "hash" + uuid.uuid4().hex[:12],
        }

    return _create_event


@pytest.fixture
def mock_audit_service(temp_marketplace_home, audit_event_factory):
    """Mock audit trail service with event logging."""
    audit_file = temp_marketplace_home / "audit.jsonl"
    events = []

    class AuditService:
        def log_event(self, event_type: str, details: Dict[str, Any]):
            """Log an audit event."""
            event = audit_event_factory(event_type, details)
            events.append(event)
            audit_file.write_text(
                audit_file.read_text() + json.dumps(event) + "\n"
            )
            return event["audit_hash"]

        def get_events(
            self,
            event_type: str = None,
            start_date: str = None,
            end_date: str = None,
        ) -> List[Dict[str, Any]]:
            """Retrieve audit events."""
            filtered = events
            if event_type:
                filtered = [e for e in filtered if e["event_type"] == event_type]
            return filtered

        def verify_chain(self) -> bool:
            """Verify audit chain integrity (hash-chained)."""
            # In production, verify hash chain
            return True

    return AuditService()


# ============================================================================
# Multi-Tenant Fixtures
# ============================================================================

@pytest.fixture
def tenant_factory():
    """Factory for creating test tenant contexts."""
    def _create_tenant(
        tenant_id: str,
        operator_id: str = None,
        org_name: str = None,
    ) -> Dict[str, Any]:
        return {
            "tenant_id": tenant_id,
            "operator_id": operator_id or f"op-{tenant_id}",
            "org_name": org_name or f"Org {tenant_id}",
        }

    return _create_tenant


@pytest.fixture
def multi_tenant_context(tenant_factory):
    """Create context with multiple tenants."""
    tenants = {
        "tenant-1": tenant_factory("tenant-1", "alice", "Acme Inc"),
        "tenant-2": tenant_factory("tenant-2", "bob", "Beta Corp"),
        "tenant-3": tenant_factory("tenant-3", "charlie", "Gamma LLC"),
    }
    return tenants


@pytest.fixture
def tenant_isolation_verifier():
    """Verify tenant isolation is maintained."""
    def _verify_isolation(
        installations: Dict[str, List[Any]],
        tenant_id: str,
    ) -> bool:
        """Check that tenant can't see other tenants' installations."""
        for plugin_id, installs in installations.items():
            for install in installs:
                if install.get("tenant_id") != tenant_id:
                    return False
        return True

    return _verify_isolation


# ============================================================================
# Plugin Manifest Fixtures
# ============================================================================

@pytest.fixture
def plugin_manifest_factory():
    """Factory for creating plugin manifests."""
    def _create_manifest(
        plugin_id: str = "test-plugin",
        version: str = "1.0.0",
        author: str = "test-author",
        category: str = "Integration",
        origin: str = "community",
        dependencies: List[str] = None,
        permissions: Dict[str, Any] = None,
    ) -> Dict[str, Any]:
        return {
            "plugin": {
                "id": plugin_id,
                "version": version,
                "author": author,
                "email": f"{author}@example.com",
                "description": f"Test plugin {plugin_id}",
                "long_description": "A test plugin for marketplace testing.",
                "homepage": f"https://example.com/plugins/{plugin_id}",
                "repository": f"https://github.com/test/{plugin_id}",
                "license": "Apache-2.0",
                "category": category,
                "origin": origin,
                "boot_layer": "installed",
                "dependencies": dependencies or [],
                "permissions": permissions or {
                    "network": False,
                    "filesystem": ["/tmp"],
                },
                "sandbox": {
                    "cpu_limit_percent": 20,
                    "memory_limit_mb": 256,
                    "timeout_seconds": 60,
                },
            }
        }

    return _create_manifest


# ============================================================================
# Test Data Fixtures
# ============================================================================

@pytest.fixture
def sample_plugins_data():
    """Sample plugins for marketplace testing."""
    return [
        {
            "plugin_id": "auth-saml-enterprise",
            "name": "Enterprise SAML Authentication",
            "version": "2.1.0",
            "category": "Authentication",
            "origin": "vetted",
            "author": "Corvin Labs",
            "description": "Enterprise SAML 2.0 provider integration.",
            "rating": 4.8,
            "rating_count": 42,
            "download_count": 1250,
        },
        {
            "plugin_id": "database-postgres-sync",
            "name": "PostgreSQL Sync",
            "version": "3.2.1",
            "category": "Database",
            "origin": "vetted",
            "author": "Corvin Labs",
            "description": "Bidirectional PostgreSQL data synchronization.",
            "rating": 4.9,
            "rating_count": 156,
            "download_count": 4230,
        },
        {
            "plugin_id": "monitoring-datadog",
            "name": "Datadog Monitoring",
            "version": "1.5.0",
            "category": "Analytics",
            "origin": "vetted",
            "author": "Corvin Labs",
            "description": "Real-time monitoring and alerting via Datadog.",
            "rating": 4.6,
            "rating_count": 28,
            "download_count": 890,
        },
        {
            "plugin_id": "security-vault-integration",
            "name": "HashiCorp Vault Integration",
            "version": "2.0.0",
            "category": "Security",
            "origin": "vetted",
            "author": "Corvin Labs",
            "description": "Secrets management via HashiCorp Vault.",
            "rating": 4.7,
            "rating_count": 89,
            "download_count": 2100,
        },
        {
            "plugin_id": "tooling-terraform-state",
            "name": "Terraform State Bridge",
            "version": "1.1.0",
            "category": "Tooling",
            "origin": "vetted",
            "author": "Corvin Labs",
            "description": "Infrastructure-as-Code state synchronization.",
            "rating": 4.3,
            "rating_count": 34,
            "download_count": 680,
        },
    ]


# ============================================================================
# Governance Fixtures
# ============================================================================

@pytest.fixture
def governance_rules():
    """Governance rules for plugin marketplace."""
    return {
        "low_rating_threshold": 2.0,
        "review_count_threshold": 5,
        "report_threshold": 3,
        "auto_remove_on_low_rating": True,
        "auto_delisting_on_reports": True,
        "security_audit_required": True,
    }


@pytest.fixture
def report_factory():
    """Factory for creating plugin reports."""
    def _create_report(
        plugin_id: str,
        reason: str = "other",
        details: str = "No details provided",
        operator_id: str = "operator-1",
    ) -> Dict[str, Any]:
        return {
            "report_id": str(uuid.uuid4()),
            "plugin_id": plugin_id,
            "reason": reason,
            "details": details,
            "operator_id": operator_id,
            "submitted_at": datetime.utcnow().isoformat(),
        }

    return _create_report


# ============================================================================
# Helper Fixtures
# ============================================================================

@pytest.fixture
def cleanup_handler():
    """Context manager for test cleanup."""
    cleanup_funcs = []

    def register_cleanup(func):
        cleanup_funcs.append(func)

    def run_cleanup():
        for func in reversed(cleanup_funcs):
            try:
                func()
            except Exception:
                pass

    class CleanupManager:
        def add(self, func):
            register_cleanup(func)

        def cleanup(self):
            run_cleanup()

    return CleanupManager()


@pytest.fixture
def performance_timer():
    """Timer for performance testing."""
    import time

    class PerformanceTimer:
        def __init__(self):
            self.times = {}

        def start(self, name: str):
            self.times[name] = {"start": time.time()}

        def stop(self, name: str) -> float:
            elapsed = time.time() - self.times[name]["start"]
            self.times[name]["elapsed"] = elapsed
            return elapsed

        def get(self, name: str) -> Optional[float]:
            return self.times.get(name, {}).get("elapsed")

        def report(self) -> Dict[str, float]:
            return {name: data["elapsed"] for name, data in self.times.items() if "elapsed" in data}

    return PerformanceTimer()


if __name__ == "__main__":
    print("Plugin Marketplace Fixtures Loaded")
