"""Tests for ADR-0296 Input Validator Factory.

Coverage:
- String validation (length, patterns, characters)
- Integer validation (range checking)
- JSON validation (depth, forbidden keys)
- Shell command validation (metacharacters)
- File path validation (safe boundaries)
- Email validation
"""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.validation.input_validator_factory import (
    InputValidatorFactory,
    ValidationConfig,
    ValidationError,
    StringValidator,
    IntegerValidator,
    JSONValidator,
    ShellCommandValidator,
    FilePathValidator,
    EmailValidator,
    InputType,
)


class TestStringValidator:
    """Test string validation."""

    def test_valid_string(self):
        """Test valid string passes."""
        config = ValidationConfig()
        validator = StringValidator(config)
        result = validator.validate("hello world")
        assert result == "hello world"

    def test_string_too_long(self):
        """Test long string rejected."""
        config = ValidationConfig(max_length=10)
        validator = StringValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("hello world this is too long")

    def test_forbidden_pattern_rejected(self):
        """Test string with forbidden pattern rejected."""
        config = ValidationConfig(forbidden_patterns=[r"(script|eval)"])
        validator = StringValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("eval('code')")

    def test_non_string_rejected(self):
        """Test non-string rejected."""
        config = ValidationConfig()
        validator = StringValidator(config)
        with pytest.raises(ValidationError):
            validator.validate(123)


class TestIntegerValidator:
    """Test integer validation."""

    def test_valid_integer(self):
        """Test valid integer passes."""
        config = ValidationConfig()
        validator = IntegerValidator(config)
        assert validator.validate(42) == 42

    def test_string_integer_converted(self):
        """Test string integer is converted."""
        config = ValidationConfig()
        validator = IntegerValidator(config)
        assert validator.validate("42") == 42

    def test_integer_out_of_range(self):
        """Test integer outside range rejected."""
        config = ValidationConfig(min_value=0, max_value=100)
        validator = IntegerValidator(config)
        with pytest.raises(ValidationError):
            validator.validate(150)

    def test_boolean_rejected(self):
        """Test boolean rejected (fail-closed)."""
        config = ValidationConfig()
        validator = IntegerValidator(config)
        with pytest.raises(ValidationError):
            validator.validate(True)


class TestJSONValidator:
    """Test JSON validation."""

    def test_valid_json(self):
        """Test valid JSON passes."""
        config = ValidationConfig()
        validator = JSONValidator(config)
        result = validator.validate('{"key": "value"}')
        assert result == {"key": "value"}

    def test_forbidden_key_rejected(self):
        """Test JSON with forbidden key rejected."""
        config = ValidationConfig()
        validator = JSONValidator(config)
        with pytest.raises(ValidationError):
            validator.validate('{"__proto__": "attack"}')

    def test_deep_nesting_rejected(self):
        """Test deeply nested JSON rejected."""
        config = ValidationConfig(max_depth=5)
        validator = JSONValidator(config)

        # Create deeply nested structure
        obj = {"a": {"b": {"c": {"d": {"e": {"f": {"g": "too deep"}}}}}}}
        with pytest.raises(ValidationError):
            validator.validate(obj)

    def test_invalid_json_rejected(self):
        """Test invalid JSON rejected."""
        config = ValidationConfig()
        validator = JSONValidator(config)
        with pytest.raises(ValidationError):
            validator.validate('{invalid json}')


class TestShellCommandValidator:
    """Test shell command validation."""

    def test_valid_command(self):
        """Test valid command passes."""
        config = ValidationConfig()
        validator = ShellCommandValidator(config)
        result = validator.validate("ls -la /tmp")
        assert result == "ls -la /tmp"

    def test_command_with_pipe_rejected(self):
        """Test command with pipe rejected."""
        config = ValidationConfig()
        validator = ShellCommandValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("cat file | grep pattern")

    def test_command_with_injection_rejected(self):
        """Test command injection attempt rejected."""
        config = ValidationConfig()
        validator = ShellCommandValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("ls; rm -rf /")

    def test_command_whitelist_enforced(self):
        """Test command whitelist is enforced."""
        config = ValidationConfig(allowed_commands=["ls", "cat"])
        validator = ShellCommandValidator(config)

        result = validator.validate("ls -la")
        assert result == "ls -la"

        with pytest.raises(ValidationError):
            validator.validate("rm -rf /")


class TestFilePathValidator:
    """Test file path validation."""

    def test_valid_path(self):
        """Test valid path passes."""
        config = ValidationConfig()
        validator = FilePathValidator(config)
        result = validator.validate("/tmp/file.txt")
        assert isinstance(result, Path)

    def test_path_traversal_rejected(self):
        """Test path traversal attack rejected."""
        config = ValidationConfig()
        validator = FilePathValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("../../../etc/passwd")

    def test_path_boundary_enforced(self):
        """Test path boundary is enforced."""
        with TemporaryDirectory() as tmpdir:
            allowed = Path(tmpdir)
            config = ValidationConfig(allowed_directories=[allowed])
            validator = FilePathValidator(config)

            # Within boundary: OK
            result = validator.validate(tmpdir + "/file.txt")
            assert isinstance(result, Path)

            # Outside boundary: REJECTED
            with pytest.raises(ValidationError):
                validator.validate("/etc/passwd")


class TestEmailValidator:
    """Test email validation."""

    def test_valid_email(self):
        """Test valid email passes."""
        config = ValidationConfig()
        validator = EmailValidator(config)
        result = validator.validate("user@example.com")
        assert result == "user@example.com"

    def test_invalid_email_rejected(self):
        """Test invalid email rejected."""
        config = ValidationConfig()
        validator = EmailValidator(config)
        with pytest.raises(ValidationError):
            validator.validate("not-an-email")

    def test_email_lowercase(self):
        """Test email is lowercased."""
        config = ValidationConfig()
        validator = EmailValidator(config)
        result = validator.validate("User@EXAMPLE.COM")
        assert result == "user@example.com"


class TestInputValidatorFactory:
    """Test factory interface."""

    def test_factory_creates_validators(self):
        """Test factory creates validators correctly."""
        factory = InputValidatorFactory()

        # Test string
        result = factory.validate_string("hello")
        assert result == "hello"

        # Test integer
        result = factory.validate_integer(42)
        assert result == 42

        # Test email
        result = factory.validate_email("user@example.com")
        assert result == "user@example.com"

    def test_validate_dispatch(self):
        """Test validate() method dispatches correctly."""
        factory = InputValidatorFactory()

        result = factory.validate("test", InputType.STRING)
        assert result == "test"

        result = factory.validate(42, InputType.INTEGER)
        assert result == 42

    def test_unknown_type_rejected(self):
        """Test unknown input type rejected."""
        factory = InputValidatorFactory()
        with pytest.raises(ValidationError):
            factory.validate("value", "unknown_type")


class TestFailClosed:
    """Test fail-closed semantics throughout."""

    def test_all_validators_fail_closed(self):
        """Test that all validators reject on doubt."""
        config = ValidationConfig()

        validators = [
            (StringValidator(config), "valid", 123),
            (IntegerValidator(config), 42, "not_int"),
            (EmailValidator(config), "user@example.com", "not@email"),
        ]

        for validator, valid_input, invalid_input in validators:
            # Valid input passes
            try:
                validator.validate(valid_input)
            except ValidationError:
                pytest.fail(f"Validator rejected valid input: {valid_input}")

            # Invalid input rejected
            with pytest.raises(ValidationError):
                validator.validate(invalid_input)
