"""Unit tests for Context Engineering Config Schema."""

import pytest
from core.console.corvin_console.context_engineering_config import (
    ContextEngineeringConfigSchema,
    ConfigValidationError,
)


def test_default_config():
    """Test default config creation."""
    config = ContextEngineeringConfigSchema()
    assert config.enabled is True
    assert config.license_tier == "free"
    assert config.stages["memory_lookup"]["enabled"] is True
    assert config.quota["daily_units"] == 10


def test_config_merge():
    """Test config with custom values."""
    config = ContextEngineeringConfigSchema(
        enabled=True,
        quota={"daily_units": 100, "soft_limit_percent": 90, "hard_limit_behavior": "degrade"},
    )
    assert config.quota["daily_units"] == 100
    assert config.quota["soft_limit_percent"] == 90


def test_invalid_strategy():
    """Test validation: invalid degradation strategy."""
    with pytest.raises(ValueError, match="Invalid strategy"):
        ContextEngineeringConfigSchema(
            degradation={"strategy": "invalid_strategy", "min_confidence": 0.70, "preserve_mandatory": True}
        )


def test_invalid_daily_units():
    """Test validation: daily_units <= 0."""
    with pytest.raises(ValueError, match="daily_units"):
        ContextEngineeringConfigSchema(quota={"daily_units": 0, "soft_limit_percent": 80, "hard_limit_behavior": "fail_closed"})


def test_missing_required_stages():
    """Test validation: missing required stages."""
    with pytest.raises(ValueError, match="Missing required stages"):
        ContextEngineeringConfigSchema(stages={"memory_lookup": {}})


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
