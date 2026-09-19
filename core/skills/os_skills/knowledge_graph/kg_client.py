"""
KG HTTP Client — thin wrapper around Corvin-Knowledge web_api.py
Responsible for querying the Knowledge Graph and returning structured results.
"""

import requests
import json
import logging
import os
from typing import Dict, List, Tuple, Optional, Any
from datetime import datetime

logger = logging.getLogger(__name__)


def _get_current_tenant() -> str:
    """
    Resolve current tenant from environment or context.
    Tries multiple sources:
    1. CORVIN_TENANT_ID env var
    2. forge.tenants.current_tenant() if available
    3. Fallback to "_default"
    """
    # Try env var first
    tenant_id = os.environ.get("CORVIN_TENANT_ID", "").strip()
    if tenant_id:
        return tenant_id

    # Try importing from forge if available
    try:
        from forge.tenants import current_tenant
        result = current_tenant()
        if result:
            return result
    except (ImportError, AttributeError):
        pass

    # Fallback
    return "_default"


class KGClientError(Exception):
    """Base exception for KG client errors."""
    pass


class KGConnectionError(KGClientError):
    """Raised when KG server is unreachable."""
    pass


class KGTimeoutError(KGClientError):
    """Raised when KG query exceeds timeout."""
    pass


class KGClient:
    """
    HTTP client for querying the Corvin-Knowledge Graph.

    All queries are tenant-scoped and fail-closed on error.
    No fallback dummy data — errors are explicit.
    """

    def __init__(self, kg_url: str = "http://localhost:8001", timeout_s: float = 5.0):
        """
        Initialize KG client.

        Args:
            kg_url: Base URL of Corvin-Knowledge web_api
            timeout_s: Request timeout in seconds
        """
        self.kg_url = kg_url.rstrip('/')
        self.timeout_s = timeout_s
        self.session = requests.Session()

    def _get_tenant_id(self, tenant_id: Optional[str] = None) -> str:
        """
        Resolve tenant_id: use provided or default to _get_current_tenant().
        Fail-closed: if no tenant, raise.
        """
        if tenant_id:
            return tenant_id

        try:
            tid = _get_current_tenant()
            if not tid:
                raise KGClientError("Tenant ID not set and _get_current_tenant() returned None")
            return tid
        except Exception as e:
            raise KGClientError(f"Failed to resolve current tenant: {e}")

    def query_entity(self, entity_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Query a single entity by ID.

        Args:
            entity_id: Entity ID (e.g., "ADR-0516")
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            Dict with entity data

        Raises:
            KGClientError: On any error (server down, invalid ID, etc.)
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        try:
            url = f"{self.kg_url}/graph/query"
            payload = {"entity_id": entity_id, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise KGClientError(f"Entity not found: {entity_id}")
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")

    def search(self, query: str, limit: int = 10, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Full-text search across the graph.

        Args:
            query: Search query string
            limit: Max results to return (bounds: 1-100, default 10)
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            List of entity dicts with search score

        Raises:
            KGClientError: On any error
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        # Validate inputs
        if not query or len(query) < 1:
            raise KGClientError("Search query cannot be empty")
        if limit < 1 or limit > 100:
            limit = min(max(limit, 1), 100)

        try:
            url = f"{self.kg_url}/graph/search"
            payload = {"query": query, "limit": limit, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                return response.json().get("results", [])
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")

    def list_relations(self, entity_id: str, tenant_id: Optional[str] = None) -> List[Tuple[str, str, str]]:
        """
        Get outgoing relations from an entity.

        Args:
            entity_id: Source entity ID
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            List of (target_id, relation_type, attributes) tuples

        Raises:
            KGClientError: On any error
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        try:
            url = f"{self.kg_url}/graph/relations"
            payload = {"entity_id": entity_id, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                relations = response.json().get("relations", [])
                return [
                    (r.get("target_id"), r.get("relation_type"), r.get("attributes", {}))
                    for r in relations
                ]
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")

    def get_schema(self, entity_type: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Introspect entity type schema.

        Args:
            entity_type: Entity type (e.g., "ADR", "Task", "Entity")
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            Dict with field names, constraints, descriptions

        Raises:
            KGClientError: On any error
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        try:
            url = f"{self.kg_url}/graph/schema"
            payload = {"entity_type": entity_type, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise KGClientError(f"Entity type not found: {entity_type}")
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")

    def adr_dependency_graph(self, adr_id: str, tenant_id: Optional[str] = None) -> Dict[str, Any]:
        """
        Trace ADR dependency graph (depends_on chain).

        Args:
            adr_id: ADR ID (e.g., "ADR-0516")
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            Dict with ADR, dependencies, related ADRs, full DAG

        Raises:
            KGClientError: On any error
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        try:
            url = f"{self.kg_url}/graph/adr-deps"
            payload = {"adr_id": adr_id, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                return response.json()
            elif response.status_code == 404:
                raise KGClientError(f"ADR not found: {adr_id}")
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")

    def audit_trail_links(self, entity_id: str, tenant_id: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Cross-reference audit events linked to an entity.

        Args:
            entity_id: Entity ID
            tenant_id: Tenant ID (defaults to current_tenant())

        Returns:
            List of audit event dicts

        Raises:
            KGClientError: On any error
            KGTimeoutError: If query exceeds timeout
        """
        tenant_id = self._get_tenant_id(tenant_id)

        try:
            url = f"{self.kg_url}/graph/audit-links"
            payload = {"entity_id": entity_id, "tenant_id": tenant_id}

            response = self.session.post(
                url,
                json=payload,
                timeout=self.timeout_s
            )

            if response.status_code == 200:
                return response.json().get("audit_links", [])
            else:
                raise KGClientError(f"KG server error: {response.status_code} {response.text}")

        except requests.Timeout:
            raise KGTimeoutError(f"Query exceeded {self.timeout_s}s timeout")
        except requests.ConnectionError:
            raise KGConnectionError(f"Cannot reach KG server at {self.kg_url}")
        except json.JSONDecodeError as e:
            raise KGClientError(f"Invalid JSON response from KG server: {e}")
