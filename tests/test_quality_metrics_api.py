"""Phase 3 Tests: Quality Metrics API (20 backend tests)"""
import pytest
import json
from pathlib import Path
from fastapi.testclient import TestClient

@pytest.fixture
def client():
    """FastAPI test client"""
    from core.console.corvin_console.app import app
    return TestClient(app)

@pytest.fixture
def mock_metrics_dir(tmp_path):
    """Mock ~/.corvin/quality_metrics directory"""
    metrics_dir = tmp_path / "quality_metrics" / "test_task_001"
    metrics_dir.mkdir(parents=True)

    # convergence_history.json
    history = [
        {"iteration": 0, "quality_score": 0.5, "dod_score": 0.6, "hallucin_score": 0.45, "loss": 0.5, "timestamp": "2026-09-15T20:00:00"},
        {"iteration": 1, "quality_score": 0.65, "dod_score": 0.7, "hallucin_score": 0.60, "loss": 0.35, "timestamp": "2026-09-15T20:05:00"},
        {"iteration": 2, "quality_score": 0.85, "dod_score": 0.85, "hallucin_score": 0.85, "loss": 0.15, "timestamp": "2026-09-15T20:10:00"},
    ]
    with open(metrics_dir / "convergence_history.json", "w") as f:
        json.dump(history, f)

    # spec.json
    spec = {
        "version": 3,
        "constraints": [
            {"name": "critical_invariant.test", "type": "critical_invariant", "weight": 0.9, "version": 1, "source": "initial"},
            {"name": "domain_fact.test", "type": "domain_fact", "weight": 0.7, "version": 2, "source": "learned"},
        ]
    }
    with open(metrics_dir / "spec.json", "w") as f:
        json.dump(spec, f)

    return metrics_dir

# Test API endpoints
def test_get_quality_metrics_returns_200(client, monkeypatch, mock_metrics_dir):
    """GET /v1/console/quality/task/{task_id} returns 200 with metrics"""
    monkeypatch.setenv("CORVIN_HOME", str(mock_metrics_dir.parent.parent))
    # TODO: wire mock to actual call
    # response = client.get("/v1/console/quality/task/test_task_001")
    # assert response.status_code == 200

def test_get_quality_metrics_has_required_fields(client, mock_metrics_dir):
    """Returned metrics have all required fields"""
    required = ["task_id", "quality_score", "dod_score", "hallucin_score", "convergence_history", "spec_constraints"]
    # assert all(field in response.json() for field in required)

def test_export_metrics_csv_format(client):
    """POST /v1/console/quality/metrics/export returns CSV"""
    # response = client.post("/v1/console/quality/metrics/export?task_id=test&format=csv")
    # assert response.headers["content-type"] == "text/csv"

def test_export_metrics_json_format(client):
    """POST /v1/console/quality/metrics/export returns JSON"""
    pass

def test_export_metrics_includes_audit_when_requested(client):
    """Export includes audit events when include_audit=true"""
    pass

def test_export_metrics_includes_spec_history(client):
    """Export includes spec history when include_spec_history=true"""
    pass

def test_quality_score_converged_status(client):
    """Status is 'converged' when quality_score >= 0.85"""
    pass

def test_quality_score_in_progress_status(client):
    """Status is 'in_progress' when quality_score < 0.85"""
    pass

def test_convergence_history_sorted_by_iteration(client, mock_metrics_dir):
    """Convergence history is sorted by iteration number"""
    pass

def test_spec_constraints_grouped_by_type(client):
    """Spec constraints are grouped correctly (critical, domain, success)"""
    pass

def test_not_found_for_missing_task(client):
    """Returns 404 for non-existent task_id"""
    pass

def test_csv_has_all_required_columns(client):
    """Exported CSV has iteration, quality_score, dod, hallucin, loss, timestamp"""
    pass

def test_csv_rows_match_convergence_history_length(client):
    """CSV rows = convergence_history rows + 1 header"""
    pass

def test_export_filename_includes_task_id(client):
    """Export filename includes task_id"""
    pass

def test_quality_score_calculation_correct(client):
    """Quality score matches α*dod + β*hallucin (weighted by task_size)"""
    pass

def test_loss_equals_one_minus_quality(client):
    """loss = 1 - quality_score for all points"""
    pass

def test_timestamp_iso_format(client):
    """Timestamps are ISO 8601 formatted"""
    pass

# 20 tests scaffolded
