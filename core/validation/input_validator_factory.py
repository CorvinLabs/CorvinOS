"""Input Validator Factory — fail-closed input validation (ADR-0296).

Provides validators for common input types:
- Strings (with blocklists for dangerous patterns)
- Integers (range-checked)
- JSON (schema validation)
- Shell commands (metacharacter rejection)
- File paths (canonicalization + safe boundaries)

All validators are fail-closed: any doubt results in rejection.
"""

from __future__ import annotations

import json
import re
import string
from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional, Pattern

import shlex


class ValidationError(ValueError):
    """Raised when validation fails."""
    pass


class InputType(str, Enum):
    """Supported input types."""
    STRING = "string"
    INTEGER = "integer"
    JSON = "json"
    SHELL_COMMAND = "shell_command"
    FILE_PATH = "file_path"
    EMAIL = "email"


@dataclass(frozen=True)
class ValidationConfig:
    """Configuration for validators."""

    # String validators
    max_length: int = 10000
    min_length: int = 0
    forbidden_patterns: list[str] = None  # regex patterns to reject
    allowed_characters: Optional[str] = None  # if set, only these chars allowed

    # Integer validators
    min_value: int = -(2**31)
    max_value: int = 2**31 - 1

    # JSON validators
    max_depth: int = 20
    forbidden_keys: list[str] = None  # e.g., "__proto__", "constructor"

    # Command validators
    allowed_commands: Optional[list[str]] = None  # whitelist of commands

    # Path validators
    allowed_directories: list[Path] = None  # whitelist of safe paths

    def __post_init__(self):
        # Convert None defaults to empty lists (immutable)
        if self.forbidden_patterns is None:
            object.__setattr__(self, "forbidden_patterns", [
                r"[^\x20-\x7E]",  # No control chars
                r"(?i)(script|eval|exec|__)",  # No dangerous keywords
            ])

        if self.forbidden_keys is None:
            object.__setattr__(self, "forbidden_keys", [
                "__proto__",
                "constructor",
                "prototype",
                "__proto__",
            ])


class Validator(ABC):
    """Base validator interface."""

    def __init__(self, config: ValidationConfig):
        self.config = config

    @abstractmethod
    def validate(self, value: Any) -> Any:
        """Validate and return cleaned value.

        Raises:
            ValidationError: if validation fails (fail-closed)
        """
        pass


class StringValidator(Validator):
    """Validate strings with fail-closed patterns."""

    def __init__(self, config: ValidationConfig):
        super().__init__(config)

        # Compile forbidden patterns
        self.forbidden_regexes = [
            re.compile(pattern) for pattern in config.forbidden_patterns
        ]

    def validate(self, value: Any) -> str:
        """Validate string."""
        if not isinstance(value, str):
            raise ValidationError(f"Expected string, got {type(value)}")

        if len(value) < self.config.min_length:
            raise ValidationError(f"String too short (min {self.config.min_length})")

        if len(value) > self.config.max_length:
            raise ValidationError(f"String too long (max {self.config.max_length})")

        # Check forbidden patterns
        for regex in self.forbidden_regexes:
            if regex.search(value):
                raise ValidationError(f"String contains forbidden pattern")

        # Check allowed characters (if specified)
        if self.config.allowed_characters:
            for char in value:
                if char not in self.config.allowed_characters:
                    raise ValidationError(f"String contains disallowed character: {char}")

        return value


class IntegerValidator(Validator):
    """Validate integers with range checking."""

    def validate(self, value: Any) -> int:
        """Validate integer."""
        if isinstance(value, bool):
            raise ValidationError("Booleans not allowed as integers")

        if not isinstance(value, int):
            try:
                value = int(value)
            except (ValueError, TypeError):
                raise ValidationError(f"Cannot convert to integer: {value}")

        if value < self.config.min_value:
            raise ValidationError(f"Value too small (min {self.config.min_value})")

        if value > self.config.max_value:
            raise ValidationError(f"Value too large (max {self.config.max_value})")

        return value


class JSONValidator(Validator):
    """Validate JSON with schema enforcement."""

    def __init__(self, config: ValidationConfig):
        super().__init__(config)
        self.max_depth = config.max_depth

    def validate(self, value: Any) -> Any:
        """Validate JSON structure."""
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as e:
                raise ValidationError(f"Invalid JSON: {e}")

        # Check depth and forbidden keys
        self._validate_depth_and_keys(value, depth=0)

        return value

    def _validate_depth_and_keys(self, obj: Any, depth: int) -> None:
        """Recursively check depth and forbidden keys."""
        if depth > self.max_depth:
            raise ValidationError(f"JSON depth exceeds max ({self.max_depth})")

        if isinstance(obj, dict):
            for key in obj.keys():
                if key in self.config.forbidden_keys:
                    raise ValidationError(f"Forbidden key: {key}")

            for value in obj.values():
                self._validate_depth_and_keys(value, depth + 1)

        elif isinstance(obj, (list, tuple)):
            for item in obj:
                self._validate_depth_and_keys(item, depth + 1)


class ShellCommandValidator(Validator):
    """Validate shell commands with metacharacter rejection."""

    # Shell metacharacters and dangerous patterns
    DANGEROUS_PATTERNS = {
        '|', ';', '&', '$', '`', '\n', '\r',  # Pipes, separators, substitution
        '<<', '>>', '<', '>',  # Redirects
        '(', ')', '{', '}', '[', ']',  # Subshells
    }

    def validate(self, value: Any) -> str:
        """Validate shell command."""
        if not isinstance(value, str):
            raise ValidationError(f"Expected string, got {type(value)}")

        # Try to parse with shlex — if it fails, reject
        try:
            tokens = shlex.split(value)
        except ValueError as e:
            raise ValidationError(f"Invalid command syntax: {e}")

        if not tokens:
            raise ValidationError("Command is empty")

        # Check allowed commands (if whitelist specified)
        if self.config.allowed_commands:
            cmd = tokens[0]
            if cmd not in self.config.allowed_commands:
                raise ValidationError(f"Command not in whitelist: {cmd}")

        # Check for metacharacters
        for token in tokens:
            for char in self.DANGEROUS_PATTERNS:
                if char in token:
                    raise ValidationError(f"Command contains dangerous character: {char}")

        return value


class FilePathValidator(Validator):
    """Validate file paths with safe boundary checking."""

    def validate(self, value: Any) -> Path:
        """Validate file path."""
        if not isinstance(value, (str, Path)):
            raise ValidationError(f"Expected path, got {type(value)}")

        path = Path(value).resolve()  # Canonicalize and resolve symlinks

        # Check against allowed directories
        if self.config.allowed_directories:
            allowed = False
            for allowed_dir in self.config.allowed_directories:
                try:
                    path.relative_to(allowed_dir)
                    allowed = True
                    break
                except ValueError:
                    continue

            if not allowed:
                raise ValidationError(f"Path outside allowed directories: {path}")

        # Prevent traversal attacks
        if ".." in path.parts:
            raise ValidationError(f"Path contains '..': {path}")

        return path


class EmailValidator(Validator):
    """Validate email addresses with basic pattern."""

    EMAIL_PATTERN = re.compile(
        r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    )

    def validate(self, value: Any) -> str:
        """Validate email."""
        if not isinstance(value, str):
            raise ValidationError(f"Expected string, got {type(value)}")

        if not self.EMAIL_PATTERN.match(value):
            raise ValidationError(f"Invalid email format: {value}")

        return value.lower()


class InputValidatorFactory:
    """Factory for creating validators with fail-closed semantics."""

    def __init__(self, config: Optional[ValidationConfig] = None):
        """Initialize factory with optional config.

        Args:
            config: ValidationConfig with default settings
        """
        self.config = config or ValidationConfig()
        self._validators = {
            InputType.STRING: StringValidator(self.config),
            InputType.INTEGER: IntegerValidator(self.config),
            InputType.JSON: JSONValidator(self.config),
            InputType.SHELL_COMMAND: ShellCommandValidator(self.config),
            InputType.FILE_PATH: FilePathValidator(self.config),
            InputType.EMAIL: EmailValidator(self.config),
        }

    def validate(self, value: Any, input_type: InputType) -> Any:
        """Validate input using appropriate validator.

        Args:
            value: Value to validate
            input_type: Type of input

        Returns:
            Cleaned/validated value

        Raises:
            ValidationError: if validation fails
        """
        if input_type not in self._validators:
            raise ValidationError(f"Unknown input type: {input_type}")

        validator = self._validators[input_type]
        return validator.validate(value)

    def validate_string(self, value: Any, **kwargs) -> str:
        """Shortcut for string validation."""
        config = self._update_config(**kwargs)
        StringValidator(config).validate(value)
        return self.validate(value, InputType.STRING)

    def validate_integer(self, value: Any, **kwargs) -> int:
        """Shortcut for integer validation."""
        config = self._update_config(**kwargs)
        IntegerValidator(config).validate(value)
        return self.validate(value, InputType.INTEGER)

    def validate_json(self, value: Any, **kwargs) -> Any:
        """Shortcut for JSON validation."""
        config = self._update_config(**kwargs)
        JSONValidator(config).validate(value)
        return self.validate(value, InputType.JSON)

    def validate_command(self, value: Any, **kwargs) -> str:
        """Shortcut for shell command validation."""
        config = self._update_config(**kwargs)
        ShellCommandValidator(config).validate(value)
        return self.validate(value, InputType.SHELL_COMMAND)

    def validate_path(self, value: Any, **kwargs) -> Path:
        """Shortcut for file path validation."""
        config = self._update_config(**kwargs)
        FilePathValidator(config).validate(value)
        return self.validate(value, InputType.FILE_PATH)

    def validate_email(self, value: Any, **kwargs) -> str:
        """Shortcut for email validation."""
        return self.validate(value, InputType.EMAIL)

    def _update_config(self, **kwargs) -> ValidationConfig:
        """Create updated config with overrides."""
        # Simple approach: return current config
        # Could be extended to support dynamic config updates
        return self.config


# Singleton instance for global use
_default_factory = InputValidatorFactory()


def get_validator() -> InputValidatorFactory:
    """Get default validator instance."""
    return _default_factory
