"""Tests for type hardening at module boundaries (ADR-0323)."""

import pytest
from dataclasses import dataclass
from typing import List, Dict, Optional

from core.type_hardening.hardening import (
    TypeContractError,
    TypeSchema,
    TypeValidator,
    enforce_at_boundary,
)


@dataclass
class SampleData:
    """Test dataclass."""

    name: str
    value: int
    optional_field: Optional[str] = None


class TestTypeValidator:
    """Tests for TypeValidator."""

    def test_validate_contract_passes_valid_objects(self):
        """Valid object passes validation."""
        schema = TypeSchema("string", str)
        assert TypeValidator.validate_contract("hello", schema) is True

    def test_validate_contract_rejects_type_mismatch(self):
        """Type mismatch raises error."""
        schema = TypeSchema("string", str)
        with pytest.raises(TypeContractError, match="expected str, got int"):
            TypeValidator.validate_contract(42, schema)

    def test_validate_contract_checks_nested_types(self):
        """Nested type validation works."""
        schema = TypeSchema("list_of_ints", List[int])
        assert TypeValidator.validate_contract([1, 2, 3], schema) is True

    def test_validate_contract_rejects_invalid_nested_types(self):
        """Invalid nested types raise error."""
        schema = TypeSchema("list_of_ints", List[int])
        with pytest.raises(TypeContractError, match="expected int"):
            TypeValidator.validate_contract([1, "two", 3], schema)

    def test_validate_contract_checks_dataclass_fields(self):
        """Dataclass field validation works."""
        schema = TypeSchema(
            "sample_data",
            SampleData,
            required_fields={"name", "value"},
        )
        obj = SampleData(name="test", value=42)
        assert TypeValidator.validate_contract(obj, schema) is True

    def test_validate_contract_rejects_missing_required_field(self):
        """Missing required field raises error."""
        schema = TypeSchema(
            "sample_data",
            SampleData,
            required_fields={"missing_field"},
        )
        obj = SampleData(name="test", value=42)
        with pytest.raises(TypeContractError, match="missing required field"):
            TypeValidator.validate_contract(obj, schema)

    def test_validate_contract_rejects_none(self):
        """None always raises error."""
        schema = TypeSchema("string", str)
        with pytest.raises(TypeContractError, match="received None"):
            TypeValidator.validate_contract(None, schema)

    def test_validate_contract_accepts_optional_none(self):
        """Optional type accepts None."""
        schema = TypeSchema("optional_string", Optional[str])
        assert TypeValidator.validate_contract(None, schema) is True

    def test_fail_closed_coerce_accepts_matching_type(self):
        """Matching type is accepted."""
        result = TypeValidator.fail_closed_coerce(42, int)
        assert result == 42

    def test_fail_closed_coerce_rejects_mismatch(self):
        """Mismatched type raises error, never coerces."""
        with pytest.raises(TypeContractError, match="Cannot coerce"):
            TypeValidator.fail_closed_coerce("42", int)

    def test_validate_contract_checks_dict_types(self):
        """Dict type validation works."""
        schema = TypeSchema("dict_str_int", Dict[str, int])
        assert TypeValidator.validate_contract({"a": 1, "b": 2}, schema) is True

    def test_validate_contract_rejects_invalid_dict_values(self):
        """Invalid dict values raise error."""
        schema = TypeSchema("dict_str_int", Dict[str, int])
        with pytest.raises(TypeContractError, match="expected int"):
            TypeValidator.validate_contract({"a": 1, "b": "two"}, schema)


class TestEnforceBoundary:
    """Tests for enforce_at_boundary decorator."""

    def test_enforce_boundary_decorator_wraps_function(self):
        """Decorator wraps function correctly."""

        @enforce_at_boundary()
        def simple_fn(x: int) -> int:
            return x + 1

        result = simple_fn(41)
        assert result == 42

    def test_enforce_boundary_validates_input(self):
        """Input validation works."""
        input_schema = TypeSchema("int_input", int)

        @enforce_at_boundary(input_schema=input_schema)
        def add_one(x: int) -> int:
            return x + 1

        result = add_one(41)
        assert result == 42

    def test_enforce_boundary_rejects_invalid_input(self):
        """Invalid input raises error."""
        input_schema = TypeSchema("int_input", int)

        @enforce_at_boundary(input_schema=input_schema)
        def add_one(x: int) -> int:
            return x + 1

        with pytest.raises(TypeContractError):
            add_one("not an int")

    def test_enforce_boundary_validates_output(self):
        """Output validation works."""
        output_schema = TypeSchema("int_output", int)

        @enforce_at_boundary(output_schema=output_schema)
        def get_answer() -> int:
            return 42

        result = get_answer()
        assert result == 42

    def test_enforce_boundary_rejects_invalid_output(self):
        """Invalid output raises error."""
        output_schema = TypeSchema("int_output", int)

        @enforce_at_boundary(output_schema=output_schema)
        def get_answer() -> int:
            return "not an int"

        with pytest.raises(TypeContractError):
            get_answer()

    def test_enforce_boundary_both_input_output(self):
        """Both input and output validation work."""
        input_schema = TypeSchema("int_input", int)
        output_schema = TypeSchema("int_output", int)

        @enforce_at_boundary(input_schema=input_schema, output_schema=output_schema)
        def double(x: int) -> int:
            return x * 2

        result = double(21)
        assert result == 42

    def test_enforce_boundary_preserves_function_metadata(self):
        """Decorator preserves function name and doc."""

        @enforce_at_boundary()
        def documented_fn(x: int) -> int:
            """This is a documented function."""
            return x + 1

        assert documented_fn.__name__ == "documented_fn"
        assert documented_fn.__doc__ == "This is a documented function."


class TestTypeSchema:
    """Tests for TypeSchema."""

    def test_type_schema_repr(self):
        """TypeSchema has useful repr."""
        schema = TypeSchema("test", int)
        assert "test" in repr(schema)
        assert "int" in repr(schema)

    def test_type_schema_with_required_fields(self):
        """TypeSchema stores required fields."""
        required = {"field1", "field2"}
        schema = TypeSchema("test", dict, required_fields=required)
        assert schema.required_fields == required


class TestTypeContractIntegration:
    """Integration tests for type contracts."""

    def test_complex_type_validation(self):
        """Complex nested types validate correctly."""
        schema = TypeSchema("complex", Dict[str, List[int]])
        valid_data = {"a": [1, 2], "b": [3, 4]}
        assert TypeValidator.validate_contract(valid_data, schema) is True

    def test_dataclass_with_nested_list_field(self):
        """Dataclass with list field validates correctly."""

        @dataclass
        class Container:
            items: List[int]
            name: str

        schema = TypeSchema("container", Container, required_fields={"items", "name"})
        obj = Container(items=[1, 2, 3], name="test")
        assert TypeValidator.validate_contract(obj, schema) is True
