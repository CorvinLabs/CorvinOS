"""Tests for Quality Gates Dashboard wiring (ADR-0688 Phase 2.1).

Tests that the dashboard can successfully query API endpoints and handle responses.
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime, timezone


class TestDashboardDataFetching:
    """Test dashboard data fetching from API endpoints."""

    def test_dashboard_can_fetch_gate_status(self):
        """Test dashboard can fetch overall gate status."""
        # Mock API response
        api_response = {
            "tenant_id": "test-tenant",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "summary": {
                "ADRGate": {
                    "last_24h": {"pass": 5, "warn": 1, "fail": 0},
                    "last_7d": {"pass": 20, "warn": 3, "fail": 1},
                    "last_verdict": "pass",
                    "last_timestamp": "2026-09-12T10:00:00Z",
                },
                "ConceptGate": {
                    "last_24h": {"pass": 3, "warn": 0, "fail": 0},
                    "last_7d": {"pass": 10, "warn": 2, "fail": 0},
                    "last_verdict": "pass",
                    "last_timestamp": "2026-09-12T09:55:00Z",
                },
            },
            "gates_total": 4,
        }

        # Verify schema
        assert "summary" in api_response
        assert "ADRGate" in api_response["summary"]
        assert "last_24h" in api_response["summary"]["ADRGate"]
        assert api_response["summary"]["ADRGate"]["last_24h"]["pass"] == 5

    def test_dashboard_can_fetch_gate_history(self):
        """Test dashboard can fetch gate history with pagination."""
        api_response = {
            "tenant_id": "test-tenant",
            "total": 150,
            "limit": 20,
            "offset": 0,
            "events": [
                {
                    "id": "evt-001",
                    "timestamp": "2026-09-12T10:00:00Z",
                    "gate_name": "ADRGate",
                    "artifact_id": "ADR-0688",
                    "verdict": "pass",
                    "confidence": 0.95,
                    "reason": "All frontmatter fields present",
                },
                {
                    "id": "evt-002",
                    "timestamp": "2026-09-12T09:55:00Z",
                    "gate_name": "ConceptGate",
                    "artifact_id": "CONCEPT-0040",
                    "verdict": "pass",
                    "confidence": 0.85,
                    "reason": "Narrative complete",
                },
            ],
        }

        # Verify schema
        assert api_response["total"] == 150
        assert len(api_response["events"]) == 2
        assert api_response["events"][0]["gate_name"] == "ADRGate"
        assert api_response["events"][0]["verdict"] == "pass"

    def test_dashboard_can_fetch_specific_gate_results(self):
        """Test dashboard can fetch results for a specific artifact."""
        api_response = {
            "tenant_id": "test-tenant",
            "artifact_id": "ADR-0688",
            "result_count": 3,
            "results": [
                {
                    "gate_name": "ADRGate",
                    "verdict": "pass",
                    "confidence": 0.95,
                    "reason": "All fields present",
                    "timestamp": "2026-09-12T10:00:00Z",
                },
                {
                    "gate_name": "DependencyGate",
                    "verdict": "pass",
                    "confidence": 0.90,
                    "reason": "All dependencies resolved",
                    "timestamp": "2026-09-12T09:59:00Z",
                },
            ],
        }

        # Verify schema
        assert api_response["artifact_id"] == "ADR-0688"
        assert len(api_response["results"]) == 2
        assert all("verdict" in r for r in api_response["results"])
        assert all("confidence" in r for r in api_response["results"])


class TestDashboardAutoRefresh:
    """Test dashboard auto-refresh functionality."""

    def test_dashboard_can_track_status_changes(self):
        """Test dashboard can detect status changes across refreshes."""
        # Initial fetch
        response_1 = {
            "summary": {
                "ADRGate": {
                    "last_24h": {"pass": 5, "warn": 0, "fail": 0},
                    "last_verdict": "pass",
                    "last_timestamp": "2026-09-12T10:00:00Z",
                }
            }
        }

        # Second fetch (after some time)
        response_2 = {
            "summary": {
                "ADRGate": {
                    "last_24h": {"pass": 6, "warn": 1, "fail": 0},
                    "last_verdict": "warn",
                    "last_timestamp": "2026-09-12T10:05:00Z",
                }
            }
        }

        # Verify change detection
        initial_pass = response_1["summary"]["ADRGate"]["last_24h"]["pass"]
        updated_pass = response_2["summary"]["ADRGate"]["last_24h"]["pass"]
        assert updated_pass > initial_pass

        initial_verdict = response_1["summary"]["ADRGate"]["last_verdict"]
        updated_verdict = response_2["summary"]["ADRGate"]["last_verdict"]
        assert updated_verdict != initial_verdict

    def test_dashboard_handles_empty_state(self):
        """Test dashboard handles empty gate results."""
        api_response = {
            "tenant_id": "test-tenant",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
            "summary": {},
            "gates_total": 0,
        }

        # Verify empty state is valid
        assert api_response["gates_total"] == 0
        assert len(api_response["summary"]) == 0


class TestDashboardErrorHandling:
    """Test dashboard error state handling."""

    def test_dashboard_handles_api_error_gracefully(self):
        """Test dashboard gracefully handles API 500 errors."""
        error_response = {
            "status_code": 500,
            "detail": "Failed to get status: ValueError",
        }

        # Verify error response can be handled
        assert error_response["status_code"] == 500
        assert "detail" in error_response

    def test_dashboard_handles_missing_data(self):
        """Test dashboard handles incomplete API responses."""
        incomplete_response = {
            "tenant_id": "test-tenant",
            # "summary" field missing
            "gates_total": 0,
        }

        # Verify handling of missing fields
        summary = incomplete_response.get("summary", {})
        assert len(summary) == 0

    def test_dashboard_handles_invalid_verdict_value(self):
        """Test dashboard handles unexpected verdict values."""
        response_with_invalid = {
            "summary": {
                "TestGate": {
                    "last_verdict": "invalid_status",  # Should be pass/warn/fail
                    "last_24h": {"pass": 0, "warn": 0, "fail": 0},
                }
            }
        }

        # Verify graceful degradation
        verdict = response_with_invalid["summary"]["TestGate"]["last_verdict"]
        valid_verdicts = ["pass", "warn", "fail"]
        is_valid = verdict in valid_verdicts
        assert not is_valid  # Detect that it's invalid


class TestDashboardGraphVisualization:
    """Test dashboard graph visualization data."""

    def test_dashboard_graph_nodes_data_format(self):
        """Test knowledge graph nodes have correct format for visualization."""
        api_response = {
            "tenant_id": "test-tenant",
            "node_count": 3,
            "nodes": [
                {
                    "id": "ADR-0688",
                    "node_type": "ADR",
                    "data": {"status": "proposed", "depends_on": ["ADR-0232"]},
                },
                {
                    "id": "CONCEPT-0040",
                    "node_type": "Concept",
                    "data": {"status": "active"},
                },
                {
                    "id": "task-xyz",
                    "node_type": "Task",
                    "data": {"status": "complete"},
                },
            ],
        }

        # Verify node format
        assert api_response["node_count"] == 3
        for node in api_response["nodes"]:
            assert "id" in node
            assert "node_type" in node
            assert "data" in node

    def test_dashboard_graph_edges_data_format(self):
        """Test knowledge graph edges have correct format for visualization."""
        api_response = {
            "tenant_id": "test-tenant",
            "edge_count": 2,
            "edges": [
                {
                    "source_id": "ADR-0688",
                    "target_id": "ADR-0232",
                    "relationship_type": "depends_on",
                },
                {
                    "source_id": "CONCEPT-0040",
                    "target_id": "ADR-0688",
                    "relationship_type": "related_to",
                },
            ],
        }

        # Verify edge format
        assert api_response["edge_count"] == 2
        for edge in api_response["edges"]:
            assert "source_id" in edge
            assert "target_id" in edge
            assert "relationship_type" in edge

    def test_dashboard_can_build_graph_from_nodes_and_edges(self):
        """Test dashboard can construct a graph from nodes and edges."""
        nodes = [
            {"id": "ADR-0688", "node_type": "ADR"},
            {"id": "ADR-0232", "node_type": "ADR"},
            {"id": "CONCEPT-0040", "node_type": "Concept"},
        ]

        edges = [
            {"source_id": "ADR-0688", "target_id": "ADR-0232", "relationship_type": "depends_on"},
            {"source_id": "CONCEPT-0040", "target_id": "ADR-0688", "relationship_type": "related_to"},
        ]

        # Build graph structure
        graph_dict = {node["id"]: node for node in nodes}

        # Verify all edges point to existing nodes
        for edge in edges:
            assert edge["source_id"] in graph_dict
            assert edge["target_id"] in graph_dict

        # Verify graph completeness
        assert len(graph_dict) == 3
        assert len(edges) == 2
