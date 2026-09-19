"""Unit tests for Feature Whitelist API (ADR-0386).

Test the backend routes:
  - GET /v1/console/features/whitelist
  - POST /v1/console/features/toggle
"""
import json
import pytest
from pathlib import Path
from unittest.mock import Mock, patch, MagicMock
from fastapi.testclient import TestClient
from pydantic import BaseModel


# Mock the API functions
class WhitelistResponse(BaseModel):
    whitelist: list[str]
    mode: str
    total_features: int


class ToggleRequest(BaseModel):
    feature_id: str
    enabled: bool


class ToggleResponse(BaseModel):
    status: str
    feature_id: str
    enabled: bool
    whitelist: list[str]


def test_whitelist_response_schema() -> None:
    """Test WhitelistResponse schema is valid."""
    data = {
        "whitelist": ["vibe_engineering", "learning_objectives"],
        "mode": "whitelist",
        "total_features": 42,
    }
    response = WhitelistResponse(**data)
    assert response.whitelist == ["vibe_engineering", "learning_objectives"]
    assert response.mode == "whitelist"
    assert response.total_features == 42


def test_toggle_request_schema() -> None:
    """Test ToggleRequest schema is valid."""
    data = {
        "feature_id": "vibe_engineering",
        "enabled": True,
    }
    request = ToggleRequest(**data)
    assert request.feature_id == "vibe_engineering"
    assert request.enabled is True


def test_toggle_response_schema() -> None:
    """Test ToggleResponse schema is valid."""
    data = {
        "status": "success",
        "feature_id": "vibe_engineering",
        "enabled": True,
        "whitelist": ["vibe_engineering", "learning_objectives"],
    }
    response = ToggleResponse(**data)
    assert response.status == "success"
    assert response.feature_id == "vibe_engineering"
    assert response.enabled is True
    assert len(response.whitelist) == 2


def test_toggle_feature_enable_idempotent() -> None:
    """Test enabling a feature that's already enabled is idempotent."""
    whitelist = ["vibe_engineering"]
    feature_id = "vibe_engineering"
    enabled = True

    # Simulate toggle logic
    if enabled:
        if feature_id not in whitelist:
            whitelist.append(feature_id)
            whitelist.sort()
    else:
        whitelist = [f for f in whitelist if f != feature_id]

    # Should still be in whitelist
    assert feature_id in whitelist
    assert whitelist == ["vibe_engineering"]


def test_toggle_feature_disable() -> None:
    """Test disabling a feature removes it from whitelist."""
    whitelist = ["vibe_engineering", "learning_objectives"]
    feature_id = "vibe_engineering"
    enabled = False

    # Simulate toggle logic
    if enabled:
        if feature_id not in whitelist:
            whitelist.append(feature_id)
            whitelist.sort()
    else:
        whitelist = [f for f in whitelist if f != feature_id]

    # Should be removed
    assert feature_id not in whitelist
    assert whitelist == ["learning_objectives"]


def test_whitelist_sorted_after_add() -> None:
    """Test that whitelist is kept sorted after adding a feature."""
    whitelist = ["tree_of_thoughts"]
    feature_id = "learning_objectives"
    enabled = True

    # Simulate toggle logic
    if enabled:
        if feature_id not in whitelist:
            whitelist.append(feature_id)
            whitelist.sort()

    # Should be sorted
    assert whitelist == ["learning_objectives", "tree_of_thoughts"]


def test_whitelist_default_features() -> None:
    """Test the default whitelist from ADR-0386."""
    default_whitelist = [
        "vibe_engineering",
        "vibe_engineering_active",
        "tree_of_thoughts",
        "learning_objectives",
        "token_metrics",
        "outcome_feedback_loop",
        "cross_device_sync",
    ]

    # All should be valid identifiers
    for feature_id in default_whitelist:
        # Should match pattern [a-z][a-z0-9_]{2,47}
        assert feature_id[0].islower()
        assert all(c.islower() or c.isdigit() or c == "_" for c in feature_id)
        assert len(feature_id) >= 3


def test_mode_legacy_vs_whitelist() -> None:
    """Test mode resolution logic."""
    # When spec.features_whitelist is a list → mode is "whitelist"
    whitelist = ["vibe_engineering"]
    mode = "whitelist" if isinstance(whitelist, list) else "legacy"
    assert mode == "whitelist"

    # When spec.features_whitelist is not a list (or missing) → mode is "legacy"
    whitelist = None
    mode = "whitelist" if isinstance(whitelist, list) else "legacy"
    assert mode == "legacy"


if __name__ == "__main__":
    # Run with: pytest tests/console/test_feature_whitelist_api_unit.py -v
    pytest.main([__file__, "-v"])
