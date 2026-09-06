"""Unit tests for module contract system (Phase 4.5, ADR-0426).

Tests contract registration, validation, and violation detection.
"""

import pytest
from core.modularization import (
    ContractAnalyzer,
    ContractRegistry,
    ContractViolation,
    PrivateAPI,
    PublicAPI,
    SimpleModuleContract,
    ViolationDetector,
    get_global_registry,
    reset_global_registry,
)


# ─────────────────────────────────────────────────────────────────────────
# TEST FIXTURES
# ─────────────────────────────────────────────────────────────────────────


class MockModule:
    """Mock module for testing."""

    @PublicAPI
    def public_method(self):
        return "public"

    @PrivateAPI
    def private_method(self):
        return "private"

    def unmarked_method(self):
        return "unmarked"


class AnotherModule:
    """Another mock module."""

    @PublicAPI
    def get_data(self):
        return {"key": "value"}

    @PublicAPI
    async def async_method(self):
        return "async_result"


@pytest.fixture
def registry():
    """Create a fresh registry for each test."""
    return ContractRegistry()


@pytest.fixture
def cleanup():
    """Clean up global registry after test."""
    yield
    reset_global_registry()


# ─────────────────────────────────────────────────────────────────────────
# TEST: API MARKERS
# ─────────────────────────────────────────────────────────────────────────


def test_public_api_marker():
    """Test PublicAPI decorator."""
    @PublicAPI
    def func():
        pass

    assert hasattr(func, "_is_public_api")
    assert func._is_public_api is True


def test_private_api_marker():
    """Test PrivateAPI decorator."""
    @PrivateAPI
    def func():
        pass

    assert hasattr(func, "_is_public_api")
    assert func._is_public_api is False


def test_unmarked_api():
    """Test unmarked functions."""
    def func():
        pass

    assert not hasattr(func, "_is_public_api") or func._is_public_api is None


# ─────────────────────────────────────────────────────────────────────────
# TEST: SIMPLE MODULE CONTRACT
# ─────────────────────────────────────────────────────────────────────────


def test_contract_creation():
    """Test creating a module contract."""
    contract = SimpleModuleContract(
        module_name="test_module",
        version="1.0.0",
        public_methods=["method1", "method2"],
        private_methods=["_internal"],
    )
    assert contract.module_name == "test_module"
    assert contract.version == "1.0.0"
    assert len(contract.public_methods) == 2


def test_contract_validation_required_fields():
    """Test contract validation of required fields."""
    with pytest.raises(ValueError, match="module_name"):
        SimpleModuleContract(
            module_name="",
            version="1.0.0",
            public_methods=["method1"], private_methods=[],
        )

    with pytest.raises(ValueError, match="version"):
        SimpleModuleContract(
            module_name="test",
            version="",
            public_methods=["method1"], private_methods=[],
        )


def test_contract_has_public_method():
    """Test checking for public method."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["method1"], private_methods=[],
    )
    assert contract.has_public_method("method1")
    assert not contract.has_public_method("method2")


def test_contract_has_private_method():
    """Test checking for private method."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=[],
        private_methods=["_internal"],
    )
    assert contract.has_private_method("_internal")
    assert not contract.has_private_method("other")


def test_contract_dependency_satisfaction():
    """Test dependency checking."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=[], private_methods=[],
        dependencies=["dep1", "dep2"],
    )
    assert contract.is_dependency_satisfied("dep1")
    assert contract.is_dependency_satisfied("dep2")
    assert not contract.is_dependency_satisfied("dep3")


# ─────────────────────────────────────────────────────────────────────────
# TEST: CONTRACT REGISTRY
# ─────────────────────────────────────────────────────────────────────────


def test_register_contract(registry):
    """Test registering a contract."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["method1"], private_methods=[],
    )
    registry.register_contract(contract)
    assert "test" in registry._contracts


def test_register_contract_duplicate(registry):
    """Test that duplicate contracts are rejected."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["method1"], private_methods=[],
    )
    registry.register_contract(contract)
    with pytest.raises(ValueError, match="already registered"):
        registry.register_contract(contract)


def test_register_implementation(registry):
    """Test registering an implementation."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["method1"], private_methods=[],
    )
    registry.register_contract(contract)
    impl = MockModule()
    registry.register_implementation("test", impl)
    assert "test" in registry._implementations


def test_register_implementation_without_contract(registry):
    """Test that implementation requires a contract."""
    impl = MockModule()
    with pytest.raises(ValueError, match="No contract"):
        registry.register_implementation("test", impl)


def test_validate_implementation_success(registry):
    """Test successful implementation validation."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["public_method", "unmarked_method"], private_methods=[],
    )
    registry.register_contract(contract)
    impl = MockModule()
    registry.register_implementation("test", impl)
    assert registry.validate_implementation("test")


def test_validate_implementation_missing_method(registry):
    """Test validation fails when method is missing."""
    contract = SimpleModuleContract(
        module_name="test",
        version="1.0.0",
        public_methods=["nonexistent"], private_methods=[],
    )
    registry.register_contract(contract)
    impl = MockModule()
    registry.register_implementation("test", impl)
    assert not registry.validate_implementation("test")
    violations = registry.get_violations()
    assert any("Missing public method" in v for v in violations)


def test_validate_all_implementations(registry):
    """Test validating all registered implementations."""
    contract1 = SimpleModuleContract(
        module_name="module1",
        version="1.0.0",
        public_methods=["public_method"], private_methods=[],
    )
    contract2 = SimpleModuleContract(
        module_name="module2",
        version="1.0.0",
        public_methods=["get_data"], private_methods=[],
    )
    registry.register_contract(contract1)
    registry.register_contract(contract2)
    registry.register_implementation("module1", MockModule())
    registry.register_implementation("module2", AnotherModule())

    assert registry.validate_all()


def test_clear_violations(registry):
    """Test clearing violation log."""
    registry._violations.append("violation1")
    registry._violations.append("violation2")
    registry.clear_violations()
    assert len(registry.get_violations()) == 0


# ─────────────────────────────────────────────────────────────────────────
# TEST: CONTRACT ANALYZER
# ─────────────────────────────────────────────────────────────────────────


def test_analyze_class():
    """Test analyzing a class for contract."""
    contract = ContractAnalyzer.analyze_class(MockModule)
    assert contract.module_name == "MockModule"
    assert "public_method" in contract.public_methods
    assert "private_method" in contract.private_methods
    assert "unmarked_method" in contract.public_methods  # Default is public


def test_analyze_module():
    """Test analyzing a module for contract."""
    import core.modularization.module_contracts as mod
    contract = ContractAnalyzer.analyze_module("module_contracts", mod)
    assert contract.module_name == "module_contracts"
    # Should find exported public classes/functions
    assert len(contract.public_methods) > 0


# ─────────────────────────────────────────────────────────────────────────
# TEST: VIOLATION DETECTOR
# ─────────────────────────────────────────────────────────────────────────


def test_check_method_call_allowed(registry):
    """Test checking allowed method calls."""
    contract = SimpleModuleContract(
        module_name="service",
        version="1.0.0",
        public_methods=["get_data"], private_methods=[],
    )
    registry.register_contract(contract)
    detector = ViolationDetector(registry)
    assert detector.check_method_call("caller", "service", "get_data")


def test_check_method_call_private(registry):
    """Test that private method calls are detected."""
    contract = SimpleModuleContract(
        module_name="service",
        version="1.0.0",
        public_methods=["get_data"],
        private_methods=["_internal"],
    )
    registry.register_contract(contract)
    detector = ViolationDetector(registry)
    assert not detector.check_method_call("caller", "service", "_internal")
    violations = detector.get_violations()
    assert len(violations) == 1
    assert violations[0].reason.startswith("Method _internal is not public")


def test_check_method_call_unknown_contract(registry):
    """Test that unknown contracts are assumed allowed."""
    detector = ViolationDetector(registry)
    # No contract registered, should assume allowed
    assert detector.check_method_call("caller", "unknown", "method")


def test_violation_properties():
    """Test ContractViolation properties."""
    violation = ContractViolation(
        caller_module="caller",
        callee_module="callee",
        method="method",
        reason="method is private",
    )
    assert violation.caller_module == "caller"
    assert violation.callee_module == "callee"
    assert violation.method == "method"
    assert violation.reason == "method is private"


# ─────────────────────────────────────────────────────────────────────────
# TEST: GLOBAL REGISTRY
# ─────────────────────────────────────────────────────────────────────────


def test_global_registry_singleton(cleanup):
    """Test that global registry is a singleton."""
    reg1 = get_global_registry()
    reg2 = get_global_registry()
    assert reg1 is reg2


def test_reset_global_registry(cleanup):
    """Test resetting global registry."""
    reg1 = get_global_registry()
    reset_global_registry()
    reg2 = get_global_registry()
    assert reg1 is not reg2
