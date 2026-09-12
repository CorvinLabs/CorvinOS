"""MEDIUM FIX #9: Metrics validator — sanity checks for NaN/Inf values.

Ensures all metrics contain valid numbers without NaN or Inf, preventing
invalid data from propagating to VIBE dashboard and learning loops.
"""

import math
import logging
from typing import Dict, Any, Tuple

logger = logging.getLogger(__name__)


def validate_metric_value(value: Any, metric_name: str = "unknown") -> Tuple[bool, str]:
    """Validate a single metric value.

    Args:
        value: The metric value to validate
        metric_name: Name of the metric (for error messages)

    Returns:
        (is_valid, message)
    """
    if not isinstance(value, (int, float)):
        return False, f"{metric_name}: expected numeric value, got {type(value).__name__}"

    if math.isnan(value):
        return False, f"{metric_name}: contains NaN"

    if math.isinf(value):
        return False, f"{metric_name}: contains Inf"

    return True, "OK"


def validate_metrics_dict(metrics: Dict[str, Any]) -> Tuple[bool, str]:
    """Validate all metrics in a dictionary.

    MEDIUM FIX #9: Ensures no NaN/Inf values in metrics.

    Args:
        metrics: Dictionary of metric values

    Returns:
        (is_valid, message)
    """
    if not isinstance(metrics, dict):
        return False, f"Expected dict, got {type(metrics).__name__}"

    for key, value in metrics.items():
        if isinstance(value, (int, float)):
            is_valid, msg = validate_metric_value(value, key)
            if not is_valid:
                logger.warning(f"Metric validation failed: {msg}")
                return False, msg

    return True, "All metrics valid"


def validate_loss_vector(loss_dict: Dict[str, float]) -> Tuple[bool, str]:
    """Validate 9D loss vector (all components must be in [0, 1]).

    MEDIUM FIX #9: Ensures loss values are bounded and valid.

    Args:
        loss_dict: Dictionary with loss components

    Returns:
        (is_valid, message)
    """
    # First validate all are numbers
    is_valid, msg = validate_metrics_dict(loss_dict)
    if not is_valid:
        return False, msg

    # Then validate range [0, 1]
    for key, value in loss_dict.items():
        if not (0.0 <= value <= 1.0):
            logger.warning(f"Loss value {key}={value} outside [0, 1] range")
            return False, f"{key}: value {value} outside [0, 1] range"

    return True, "Loss vector valid"


def scrub_metrics(metrics: Dict[str, Any]) -> Dict[str, Any]:
    """MEDIUM FIX #9: Remove or replace invalid metric values.

    When a metric contains NaN or Inf, replace with a safe default (0.5 for loss).

    Args:
        metrics: Dictionary potentially containing invalid values

    Returns:
        Cleaned metrics dictionary
    """
    cleaned = {}

    for key, value in metrics.items():
        if not isinstance(value, (int, float)):
            cleaned[key] = value
            continue

        if math.isnan(value) or math.isinf(value):
            logger.warning(f"Scrubbing invalid metric {key}={value}")
            # Default: use 0.5 for loss-type metrics, 0 for count metrics
            cleaned[key] = 0.5 if "loss" in key else 0
        else:
            cleaned[key] = value

    return cleaned


# Validator registry for different metric types
VALIDATORS = {
    "metrics": validate_metrics_dict,
    "loss_vector": validate_loss_vector,
}


def validate_by_type(metrics: Dict[str, Any], validator_type: str = "metrics") -> Tuple[bool, str]:
    """Validate metrics using a registered validator.

    Args:
        metrics: Metrics to validate
        validator_type: Type of validator ("metrics" or "loss_vector")

    Returns:
        (is_valid, message)
    """
    validator = VALIDATORS.get(validator_type)
    if not validator:
        logger.warning(f"Unknown validator type: {validator_type}")
        return validate_metrics_dict(metrics)

    return validator(metrics)
