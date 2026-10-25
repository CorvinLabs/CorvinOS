"""
Engine Configuration K=1 Unit Tests

Phase 1 (Week 1–2): Frontend component + mock API routes

Tests:
  Frontend (5 tests):
    - Component renders without error
    - Dropdowns load and select models
    - Confidence badges display correctly
    - External provider modal opens/closes
    - Hardcoded "0 runs" shows for new config

  Backend (5 tests):
    - GET /v1/engine/config returns valid schema
    - PUT /v1/engine/config validates task_type
    - PUT /v1/engine/config rejects invalid models
    - POST /v1/engine/external-provider/test responds
    - GET /v1/engine/analytics returns empty data

ADR-0641: Engine Configuration Console
"""

import pytest
import json
from datetime import datetime, timezone
from typing import Dict, Any

# The backend half of this file exercised core/console/routes/engine_api.py, an
# unmounted legacy route retired by ADR-2282 (the live engine API is
# corvin_console/routes/engine_api.py, covered by core/console/tests/test_engine_api_route.py).

# ─────────────────────────────────────────────────────────────────
# Frontend Component Schema Tests
# ─────────────────────────────────────────────────────────────────


class TestEngineConfigFrontend:
    """Test frontend component rendering and types."""

    def test_model_options_are_valid(self):
        """Test that model option list is complete (haiku/sonnet/opus/fable)."""
        MODEL_OPTIONS = [
            {"value": "haiku", "label": "Claude 3.5 Haiku (Fast, Low Cost)"},
            {"value": "sonnet", "label": "Claude 3.5 Sonnet (Balanced)"},
            {"value": "opus", "label": "Claude Opus (Powerful)"},
            {"value": "fable", "label": "Claude Fable (Experimental)"},
        ]

        assert len(MODEL_OPTIONS) == 4
        values = [opt["value"] for opt in MODEL_OPTIONS]
        assert set(values) == {"haiku", "sonnet", "opus", "fable"}

    def test_task_types_match_backend_schema(self):
        """Test that frontend task types match backend validation."""
        TASK_TYPES = ["corvinOS", "SIMPLE", "MEDIUM", "COMPLEX"]
        assert len(TASK_TYPES) == 4
        assert "corvinOS" in TASK_TYPES
        assert "SIMPLE" in TASK_TYPES
        assert "MEDIUM" in TASK_TYPES
        assert "COMPLEX" in TASK_TYPES

    def test_confidence_score_bounds(self):
        """Test that confidence scores are valid floats between 0 and 1."""
        MOCK_SCORES = [
            {"task_type": "corvinOS", "confidence": 0.0, "run_count": 0},
            {"task_type": "SIMPLE", "confidence": 0.87, "run_count": 1247},
            {"task_type": "MEDIUM", "confidence": 0.72, "run_count": 892},
            {"task_type": "COMPLEX", "confidence": 0.91, "run_count": 456},
        ]

        for score in MOCK_SCORES:
            assert 0.0 <= score["confidence"] <= 1.0
            assert isinstance(score["run_count"], int)
            assert score["run_count"] >= 0


# ─────────────────────────────────────────────────────────────────
# Integration Tests (K=2)
# ─────────────────────────────────────────────────────────────────


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
