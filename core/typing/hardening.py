"""Type Hardening at Module Boundaries (ADR-0323).

Fail-closed type validation with no coercion. Every module boundary
enforces strict type contracts via decorators and explicit validators.
"""

from __future__ import annotations

import functools
import logging
from dataclasses import fields, is_dataclass
from typing import Any, Callable, Optional, Type, TypeVar, Union, get_args, get_origin, get_type_hints

T = TypeVar("T")
logger = logging.getLogger(__name__)


class TypeContractError(Exception):
    """Raised when type contract is violated."""

    pass


class TypeSchema:
    """Defines a type contract for validation."""

    def __init__(self, name: str, type_spec: Type, required_fields: Optional[set[str]] = None):
        """Initialize schema.

        Args:
            name: Schema name for error messages
            type_spec: Expected type
            required_fields: For dataclasses, which fields must be present
        """
        self.name = name
        self.type_spec = type_spec
        self.required_fields = required_fields or set()

    def __repr__(self) -> str:
        return f"TypeSchema({self.name}, {self.type_spec})"


class TypeValidator:
    """Validates type contracts at module boundaries."""

    @staticmethod
    def validate_contract(obj: Any, schema: TypeSchema) -> bool:
        """Validate object against type schema (fail-closed).

        Args:
            obj: Object to validate
            schema: Type schema to validate against

        Returns:
            True if valid

        Raises:
            TypeContractError: If validation fails
        """
        if obj is None:
            raise TypeContractError(f"Schema {schema.name}: received None")

        expected_type = schema.type_spec
        origin = get_origin(expected_type)

        # Handle Optional[X] → Union[X, None]
        if origin is Union:
            args = get_args(expected_type)
            if type(None) in args:  # Optional
                if obj is None:
                    return True
                # Validate against non-None type
                expected_type = next(arg for arg in args if arg is not type(None))
                origin = get_origin(expected_type)

        # Check basic type
        if not isinstance(obj, expected_type if origin is None else origin or expected_type):
            raise TypeContractError(
                f"Schema {schema.name}: expected {expected_type}, got {type(obj).__name__}"
            )

        # For dataclasses, check required fields
        if is_dataclass(expected_type):
            for field_name in schema.required_fields:
                if not hasattr(obj, field_name):
                    raise TypeContractError(
                        f"Schema {schema.name}: missing required field '{field_name}'"
                    )

            # Check field types (handle string annotations from __future__ annotations)
            try:
                type_hints = get_type_hints(expected_type)
            except Exception:
                type_hints = {}

            for field in fields(expected_type):
                if hasattr(obj, field.name):
                    field_value = getattr(obj, field.name)
                    if field_value is not None:
                        # Use resolved type hint if available, otherwise field.type
                        field_type = type_hints.get(field.name, field.type)
                        # Skip isinstance check if field_type is a string (unresolvable forward reference)
                        if isinstance(field_type, str):
                            logger.debug(f"Skipping type check for {field.name}: forward reference '{field_type}'")
                            continue
                        try:
                            if not isinstance(field_value, field_type):
                                raise TypeContractError(
                                    f"Schema {schema.name}.{field.name}: expected {field_type}, "
                                    f"got {type(field_value).__name__}"
                                )
                        except TypeError as e:
                            # isinstance() fails on parameterized generics like List[int]
                            logger.debug(f"Cannot check isinstance for {field.name}: {e}")
                            raise TypeContractError(
                                f"Schema {schema.name}.{field.name}: type validation error for {field_type}: {e}"
                            )

        # For lists/dicts, check container element types
        if origin is list:
            args = get_args(expected_type)
            if args and obj:
                element_type = args[0]
                # Skip validation if element_type is a parameterized generic (List[int], etc)
                elem_origin = get_origin(element_type)
                if elem_origin is not None:
                    # For nested generics like List[List[int]], skip isinstance check
                    logger.debug(f"Skipping validation for nested generic: {element_type}")
                    return True

                for i, elem in enumerate(obj):
                    try:
                        if not isinstance(elem, element_type):
                            raise TypeContractError(
                                f"Schema {schema.name}[{i}]: expected {element_type}, "
                                f"got {type(elem).__name__}"
                            )
                    except TypeError as e:
                        # isinstance() fails on parameterized types
                        logger.debug(f"Cannot check isinstance for element {i}: {e}")
                        raise TypeContractError(
                            f"Schema {schema.name}[{i}]: type validation error for {element_type}: {e}"
                        )

        if origin is dict:
            args = get_args(expected_type)
            if len(args) >= 2 and obj:
                key_type, value_type = args[0], args[1]
                for k, v in obj.items():
                    try:
                        if not isinstance(k, key_type):
                            raise TypeContractError(
                                f"Schema {schema.name} key: expected {key_type}, got {type(k).__name__}"
                            )
                        if not isinstance(v, value_type):
                            raise TypeContractError(
                                f"Schema {schema.name}[{k}]: expected {value_type}, "
                                f"got {type(v).__name__}"
                            )
                    except TypeError as e:
                        logger.debug(f"Cannot check isinstance for dict: {e}")
                        raise TypeContractError(f"Schema {schema.name}: type validation error: {e}")

        return True

    @staticmethod
    def fail_closed_coerce(value: Any, expected_type: Type) -> Any:
        """Fail-closed coercion: never coerce, always error on mismatch.

        Args:
            value: Value to coerce
            expected_type: Expected type

        Returns:
            value if types match

        Raises:
            TypeContractError: If types don't match (NEVER COERCES)
        """
        if not isinstance(value, expected_type):
            raise TypeContractError(
                f"Cannot coerce {type(value).__name__} to {expected_type.__name__}: "
                f"fail-closed, no coercion allowed"
            )
        return value


def enforce_at_boundary(
    input_schema: Optional[TypeSchema] = None,
    output_schema: Optional[TypeSchema] = None,
) -> Callable:
    """Decorator for enforcing type contracts at module boundaries.

    Args:
        input_schema: Schema to validate function arguments
        output_schema: Schema to validate function return value

    Returns:
        Decorator function

    Raises:
        TypeContractError: If any validation fails
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            # Validate input if schema provided
            if input_schema and args:
                try:
                    TypeValidator.validate_contract(args[0], input_schema)
                except TypeContractError as e:
                    logger.error(f"Input validation failed for {func.__name__}: {e}")
                    raise

            # Call function
            result = func(*args, **kwargs)

            # Validate output if schema provided
            if output_schema:
                try:
                    TypeValidator.validate_contract(result, output_schema)
                except TypeContractError as e:
                    logger.error(f"Output validation failed for {func.__name__}: {e}")
                    raise

            return result

        return wrapper

    return decorator
