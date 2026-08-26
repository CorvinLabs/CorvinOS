"""Module contract system for enforcing public/private interfaces.

Phase 4.5 Modularization: Contracts define module boundaries, enforce API contracts,
and prevent cross-module violations.
"""

import inspect
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Type
from abc import ABC, abstractmethod

import logging

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# API MARKERS (decorator-based public/private API declaration)
# ─────────────────────────────────────────────────────────────────────────


def PublicAPI(func: Callable) -> Callable:
    """Mark a method or function as part of the public API."""
    func._is_public_api = True
    return func


def PrivateAPI(func: Callable) -> Callable:
    """Mark a method or function as internal (not part of public API)."""
    func._is_public_api = False
    return func


def is_public_api(obj: Any) -> bool:
    """Check if object is marked as public API."""
    return getattr(obj, "_is_public_api", None) is True


def is_private_api(obj: Any) -> bool:
    """Check if object is marked as private API."""
    return getattr(obj, "_is_public_api", None) is False


# ─────────────────────────────────────────────────────────────────────────
# MODULE CONTRACT (defines module interface and constraints)
# ─────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class MethodSignature:
    """Signature of a contract method."""

    name: str
    params: Dict[str, str]  # param_name -> type_hint_str
    return_type: str
    is_async: bool


@dataclass(frozen=True)
class ModuleContract(ABC):
    """Base class for module contracts."""

    module_name: str
    version: str
    public_methods: Dict[str, MethodSignature] = field(default_factory=dict)
    private_methods: Set[str] = field(default_factory=set)
    dependencies: List[str] = field(default_factory=list)

    @abstractmethod
    def validate(self) -> bool:
        """Validate contract compliance."""
        pass


@dataclass
class SimpleModuleContract:
    """Simple contract for module API validation."""

    module_name: str
    version: str
    public_methods: List[str]
    private_methods: List[str]
    dependencies: List[str] = field(default_factory=list)

    def __post_init__(self):
        """Validate contract on creation."""
        if not self.module_name:
            raise ValueError("module_name is required")
        if not self.version:
            raise ValueError("version is required")

    def has_public_method(self, name: str) -> bool:
        """Check if method is public."""
        return name in self.public_methods

    def has_private_method(self, name: str) -> bool:
        """Check if method is private."""
        return name in self.private_methods

    def is_dependency_satisfied(self, dep_module: str) -> bool:
        """Check if dependency is listed."""
        return dep_module in self.dependencies


# ─────────────────────────────────────────────────────────────────────────
# CONTRACT REGISTRY (validates implementations against contracts)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ContractRegistry:
    """Central registry for module contracts."""

    _contracts: Dict[str, SimpleModuleContract] = field(default_factory=dict)
    _implementations: Dict[str, Any] = field(default_factory=dict)
    _violations: List[str] = field(default_factory=list)

    def register_contract(self, contract: SimpleModuleContract):
        """Register a module contract."""
        if contract.module_name in self._contracts:
            raise ValueError(
                f"Contract for {contract.module_name} already registered"
            )
        self._contracts[contract.module_name] = contract
        logger.info(f"Registered contract for {contract.module_name} v{contract.version}")

    def register_implementation(self, module_name: str, impl: Any):
        """Register a module implementation."""
        if module_name not in self._contracts:
            raise ValueError(f"No contract found for {module_name}")
        self._implementations[module_name] = impl
        logger.info(f"Registered implementation for {module_name}")

    def validate_implementation(self, module_name: str) -> bool:
        """Validate that implementation matches contract."""
        if module_name not in self._contracts:
            raise ValueError(f"No contract for {module_name}")
        if module_name not in self._implementations:
            raise ValueError(f"No implementation for {module_name}")

        contract = self._contracts[module_name]
        impl = self._implementations[module_name]

        violations = []

        # Check public methods exist
        for method_name in contract.public_methods:
            if not hasattr(impl, method_name):
                violations.append(
                    f"Missing public method: {module_name}.{method_name}"
                )
            else:
                method = getattr(impl, method_name)
                if not callable(method):
                    violations.append(
                        f"Public method not callable: {module_name}.{method_name}"
                    )

        # Check private methods don't leak
        impl_methods = set(
            name for name in dir(impl) if not name.startswith("_") and callable(getattr(impl, name))
        )
        for method_name in impl_methods:
            if method_name not in contract.public_methods:
                # It's not in public methods, so it should be private
                if not is_private_api(getattr(impl, method_name, None)):
                    logger.warning(
                        f"Method {module_name}.{method_name} not marked as public or private"
                    )

        # Check dependencies
        for dep in contract.dependencies:
            if dep not in self._implementations:
                violations.append(f"Missing dependency: {module_name} -> {dep}")

        if violations:
            self._violations.extend(violations)
            for v in violations:
                logger.error(f"Contract violation: {v}")
            return False

        logger.info(f"Contract validation passed for {module_name}")
        return True

    def validate_all(self) -> bool:
        """Validate all registered implementations."""
        valid = True
        for module_name in self._contracts.keys():
            if module_name not in self._implementations:
                logger.warning(f"No implementation for contract {module_name}")
                valid = False
            else:
                if not self.validate_implementation(module_name):
                    valid = False
        return valid

    def get_violations(self) -> List[str]:
        """Get all contract violations."""
        return self._violations.copy()

    def clear_violations(self):
        """Clear violation log."""
        self._violations.clear()


# ─────────────────────────────────────────────────────────────────────────
# CONTRACT ANALYZER (introspects implementations)
# ─────────────────────────────────────────────────────────────────────────


class ContractAnalyzer:
    """Analyzes implementations to infer contracts."""

    @staticmethod
    def analyze_class(cls: Type) -> SimpleModuleContract:
        """Analyze a class and extract contract."""
        public_methods = []
        private_methods = []

        for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
            if name.startswith("_"):
                continue

            if is_public_api(method):
                public_methods.append(name)
            elif is_private_api(method):
                private_methods.append(name)
            else:
                # Default: treat as public
                public_methods.append(name)

        return SimpleModuleContract(
            module_name=cls.__name__,
            version=getattr(cls, "__version__", "1.0.0"),
            public_methods=public_methods,
            private_methods=private_methods,
        )

    @staticmethod
    def analyze_module(module_name: str, module_obj: Any) -> SimpleModuleContract:
        """Analyze a module and extract contract."""
        public_methods = []
        private_methods = []

        for name in dir(module_obj):
            if name.startswith("_"):
                continue

            obj = getattr(module_obj, name)
            if not callable(obj):
                continue

            if is_public_api(obj):
                public_methods.append(name)
            elif is_private_api(obj):
                private_methods.append(name)
            else:
                # Default: treat as public if exported
                public_methods.append(name)

        return SimpleModuleContract(
            module_name=module_name,
            version=getattr(module_obj, "__version__", "1.0.0"),
            public_methods=public_methods,
            private_methods=private_methods,
        )


# ─────────────────────────────────────────────────────────────────────────
# VIOLATION DETECTOR (checks cross-module calls)
# ─────────────────────────────────────────────────────────────────────────


@dataclass
class ContractViolation:
    """A contract violation."""

    caller_module: str
    callee_module: str
    method: str
    reason: str


class ViolationDetector:
    """Detects contract violations at runtime."""

    def __init__(self, registry: ContractRegistry):
        self.registry = registry
        self._violations: List[ContractViolation] = []

    def check_method_call(
        self, caller_module: str, callee_module: str, method: str
    ) -> bool:
        """Check if a cross-module method call is allowed."""
        if callee_module not in self.registry._contracts:
            # No contract, assume allowed
            return True

        contract = self.registry._contracts[callee_module]

        # Check if method is public
        if not contract.has_public_method(method):
            violation = ContractViolation(
                caller_module=caller_module,
                callee_module=callee_module,
                method=method,
                reason=f"Method {method} is not public in {callee_module}",
            )
            self._violations.append(violation)
            logger.error(f"Contract violation: {violation.reason}")
            return False

        return True

    def get_violations(self) -> List[ContractViolation]:
        """Get detected violations."""
        return self._violations.copy()


# ─────────────────────────────────────────────────────────────────────────
# GLOBAL REGISTRY (singleton)
# ─────────────────────────────────────────────────────────────────────────


_global_registry: Optional[ContractRegistry] = None


def get_global_registry() -> ContractRegistry:
    """Get or create global contract registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = ContractRegistry()
    return _global_registry


def reset_global_registry():
    """Reset global registry (for testing)."""
    global _global_registry
    _global_registry = None
