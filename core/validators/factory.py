"""
Input Validator Factory — ADR-0296

Central, pluggable validator registry with deny-by-default validation.
All user input validated before reaching business logic.
"""

from typing import Any, Callable, Dict, Optional, Tuple


class ValidatorFactory:
    """Central validator registry for input validation."""

    def __init__(self):
        """Initialize empty validator registry."""
        self._validators: Dict[str, Callable[[Any], Tuple[bool, Optional[str]]]] = {}

    def register(self, name: str, validator: Callable[[Any], Tuple[bool, Optional[str]]]) -> None:
        """
        Register a validator function.

        Args:
            name: Validator identifier (e.g., "peer_id")
            validator: Function that returns (is_valid: bool, error_message: Optional[str])
        """
        self._validators[name] = validator

    def validate(self, name: str, value: Any) -> Tuple[bool, Optional[str]]:
        """
        Validate a value against a registered validator.

        Args:
            name: Validator name
            value: Value to validate

        Returns:
            (is_valid, error_message) — if is_valid is True, error_message is None
        """
        if name not in self._validators:
            return False, f"Unknown validator: {name}"

        return self._validators[name](value)

    def has_validator(self, name: str) -> bool:
        """Check if validator exists."""
        return name in self._validators


# Global singleton instance
FACTORY = ValidatorFactory()
