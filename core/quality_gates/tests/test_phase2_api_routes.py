"""Tests for Quality Gates API routes (ADR-0688 Phase 2.2).

Tests all 7 API endpoints:
- GET  /api/quality/gates/status
- POST /api/quality/gates/run/all
- GET  /api/quality/gates/history
- GET  /api/quality/gates/graph/nodes
- GET  /api/quality/gates/graph/edges
- POST /api/quality/gates/events
- GET  /api/quality/gates/results/{artifact_id}
"""

import pytest
import json
from unittest.mock import Mock, patch, MagicMock
from datetime import datetime

from fastapi.testclient import TestClient
from fastapi import FastAPI

from core.quality_gates.models import VerdictType, GateResult
from core.console.corvin_console.routes import quality_gates


@pytest.fixture
def app():
    """Create a test FastAPI app with quality gates routes."""
    app = FastAPI()
    app.include_router(quality_gates.router)
    return app


@pytest.fixture
def client(app):
    """Create a test client."""
    return TestClient(app)


@pytest.fixture
def mock_session_record():
    """Create a mock session record."""
    record = Mock()
    record.tenant_id = "test-tenant"
    return record


@pytest.fixture
def mock_graph():
    """Create a mock KnowledgeGraph."""
    graph = Mock()
    graph.conn = Mock()
    return graph


class TestGetStatusEndpoint:
    """Test GET /api/quality/gates/status endpoint."""

    def test_get_status_returns_correct_schema(self, client, mock_session_record):
        """Test status endpoint returns correct schema."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()
                mock_graph.conn.execute.return_value.fetchall.return_value = [
                    (10, 7, 2, 1)  # total, passed, warned, failed
                ]
                mock_get_graph.return_value = mock_graph

                response = client.get("/status")
                assert response.status_code == 200
                data = response.json()
                assert "tenant_id" in data
                assert "total_gates" in data
                assert "passed" in data
                assert "warned" in data
                assert "failed" in data
                assert "pass_rate" in data
                assert data["tenant_id"] == "test-tenant"
                assert data["total_gates"] == 10
                assert data["passed"] == 7
                assert data["pass_rate"] == 70.0

    def test_get_status_zero_events(self, client, mock_session_record):
        """Test status endpoint with zero events."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()
                mock_graph.conn.execute.return_value.fetchall.return_value = []
                mock_get_graph.return_value = mock_graph

                response = client.get("/status")
                assert response.status_code == 200
                data = response.json()
                assert data["total_gates"] == 0
                assert data["passed"] == 0
                assert data["pass_rate"] == 0.0


class TestRunAllValidatorsEndpoint:
    """Test POST /api/quality/gates/run/all endpoint."""

    def test_run_all_validators_returns_results(self, client, mock_session_record):
        """Test run all validators endpoint returns results."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                with patch('core.console.corvin_console.routes.quality_gates.get_audit_logger') as mock_audit:
                    mock_graph = Mock()
                    mock_get_graph.return_value = mock_graph

                    mock_logger = Mock()
                    mock_audit.return_value = mock_logger

                    response = client.post("/run/all")
                    assert response.status_code == 200
                    data = response.json()
                    assert "tenant_id" in data
                    assert "results" in data
                    assert "IdeaGate" in data["results"]
                    assert "ConceptGate" in data["results"]
                    assert "ADRGate" in data["results"]
                    assert "ImplementationPlanGate" in data["results"]

    def test_run_all_validators_verdicts_included(self, client, mock_session_record):
        """Test run all validators include verdict and confidence."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                with patch('core.console.corvin_console.routes.quality_gates.get_audit_logger') as mock_audit:
                    mock_graph = Mock()
                    mock_get_graph.return_value = mock_graph

                    mock_logger = Mock()
                    mock_audit.return_value = mock_logger

                    response = client.post("/run/all")
                    assert response.status_code == 200
                    data = response.json()
                    for gate_name, result in data["results"].items():
                        assert "verdict" in result
                        assert "confidence" in result
                        assert "reason" in result
                        assert "findings" in result or "error" in result


class TestHistoryEndpoint:
    """Test GET /api/quality/gates/history endpoint."""

    def test_get_history_with_pagination(self, client, mock_session_record):
        """Test history endpoint with pagination."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                # Count query
                count_result = Mock()
                count_result.fetchall.return_value = [(50,)]

                # History query
                history_result = Mock()
                history_result.fetchall.return_value = [
                    ("evt-001", "2026-09-12T10:00:00Z", "ADRGate", "ADR-0688", "pass", 0.95, "OK"),
                    ("evt-002", "2026-09-12T10:01:00Z", "ConceptGate", "CONCEPT-001", "pass", 0.85, "OK"),
                ]

                mock_graph.conn.execute.side_effect = [count_result, history_result]
                mock_get_graph.return_value = mock_graph

                response = client.get("/history?limit=10&offset=0")
                assert response.status_code == 200
                data = response.json()
                assert data["total"] == 50
                assert data["limit"] == 10
                assert data["offset"] == 0
                assert len(data["events"]) == 2
                assert data["events"][0]["artifact_id"] == "ADR-0688"

    def test_get_history_default_pagination(self, client, mock_session_record):
        """Test history endpoint with default pagination values."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                count_result = Mock()
                count_result.fetchall.return_value = [(100,)]

                history_result = Mock()
                history_result.fetchall.return_value = []

                mock_graph.conn.execute.side_effect = [count_result, history_result]
                mock_get_graph.return_value = mock_graph

                response = client.get("/history")
                assert response.status_code == 200
                data = response.json()
                assert data["limit"] == 10
                assert data["offset"] == 0


class TestGraphNodesEndpoint:
    """Test GET /api/quality/gates/graph/nodes endpoint."""

    def test_get_graph_nodes_all(self, client, mock_session_record):
        """Test get all graph nodes."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = [
                    ("ADR-0688", "ADR", '{"status": "proposed"}'),
                    ("CONCEPT-0040", "Concept", '{"status": "active"}'),
                ]

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/graph/nodes")
                assert response.status_code == 200
                data = response.json()
                assert "node_count" in data
                assert "nodes" in data
                assert len(data["nodes"]) == 2

    def test_get_graph_nodes_filtered_by_type(self, client, mock_session_record):
        """Test get graph nodes filtered by type."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = [
                    ("ADR-0688", "ADR", '{"status": "proposed"}'),
                ]

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/graph/nodes?node_type=ADR")
                assert response.status_code == 200
                data = response.json()
                assert len(data["nodes"]) == 1
                assert data["nodes"][0]["node_type"] == "ADR"


class TestGraphEdgesEndpoint:
    """Test GET /api/quality/gates/graph/edges endpoint."""

    def test_get_graph_edges_all(self, client, mock_session_record):
        """Test get all graph edges."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = [
                    ("ADR-0688", "ADR-0232", "depends_on"),
                    ("CONCEPT-0040", "ADR-0688", "related_to"),
                ]

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/graph/edges")
                assert response.status_code == 200
                data = response.json()
                assert "edge_count" in data
                assert "edges" in data
                assert len(data["edges"]) == 2

    def test_get_graph_edges_filtered_by_type(self, client, mock_session_record):
        """Test get graph edges filtered by relationship type."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = [
                    ("ADR-0688", "ADR-0232", "depends_on"),
                ]

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/graph/edges?relationship_type=depends_on")
                assert response.status_code == 200
                data = response.json()
                assert len(data["edges"]) == 1
                assert data["edges"][0]["relationship_type"] == "depends_on"


class TestPublishEventEndpoint:
    """Test POST /api/quality/gates/events endpoint."""

    def test_publish_event_success(self, client):
        """Test publish event succeeds with valid data."""
        event_data = {
            "artifact_id": "ADR-0688",
            "commit_sha": "abc123",
            "tenant_id": "test-tenant",
            "validation_result": {"verdict": "pass"},
        }

        response = client.post("/events", json=event_data)
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "accepted"
        assert data["artifact_id"] == "ADR-0688"

    def test_publish_event_missing_artifact_id(self, client):
        """Test publish event fails without artifact_id."""
        event_data = {
            "commit_sha": "abc123",
            "tenant_id": "test-tenant",
        }

        response = client.post("/events", json=event_data)
        assert response.status_code == 400


class TestGetArtifactResultsEndpoint:
    """Test GET /api/quality/gates/results/{artifact_id} endpoint."""

    def test_get_artifact_results_found(self, client, mock_session_record):
        """Test get artifact results when found."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = [
                    ("ADRGate", "pass", 0.95, "OK", "2026-09-12T10:00:00Z"),
                    ("ConceptGate", "pass", 0.85, "OK", "2026-09-12T10:01:00Z"),
                ]

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/results/ADR-0688")
                assert response.status_code == 200
                data = response.json()
                assert data["artifact_id"] == "ADR-0688"
                assert data["result_count"] == 2
                assert len(data["results"]) == 2

    def test_get_artifact_results_not_found(self, client, mock_session_record):
        """Test get artifact results when not found."""
        with patch('core.console.corvin_console.routes.quality_gates.require_session') as mock_require:
            mock_require.return_value = mock_session_record
            with patch('core.console.corvin_console.routes.quality_gates.get_graph') as mock_get_graph:
                mock_graph = Mock()

                result = Mock()
                result.fetchall.return_value = []

                mock_graph.conn.execute.return_value = result
                mock_get_graph.return_value = mock_graph

                response = client.get("/results/ADR-9999")
                assert response.status_code == 404
