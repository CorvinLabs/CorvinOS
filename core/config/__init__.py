"""
Phase 2: Centralized Configuration Management

Exports:
- CentralizedConfigManager — Main configuration manager
- ConfigDrift — Configuration divergence detection
- DEFAULT_SAFE_CONFIG — Fail-closed default configuration
"""

from core.config.centralized_manager import (
    CentralizedConfigManager,
    ConfigDrift,
    ConfigValidationResult,
    DEFAULT_SAFE_CONFIG,
    get_global_config_manager,
)

__all__ = [
    "CentralizedConfigManager",
    "ConfigDrift",
    "ConfigValidationResult",
    "DEFAULT_SAFE_CONFIG",
    "get_global_config_manager",
]
