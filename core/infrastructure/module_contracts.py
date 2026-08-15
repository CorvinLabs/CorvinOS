"""Module Contracts — ADR-0331

Validate module interface contracts on load. Invalid modules crash (fail-closed).
"""

from __future__ import annotations

import inspect
from typing import Any, Set, Callable


class ContractValidationError(Exception):
    """Raised when module contract validation fails."""

    def __init__(self, message: str, module_name: str = None):
        self.message = message
        self.module_name = module_name
        super().__init__(message)


class ModuleContract:
    """Validate module interface contracts on load.

    Enforces both interface contracts (required exports) and version compatibility.
    """

    def __init__(self, required_exports: Set[str] = None, min_version: str = None):
        """Initialize contract.

        Args:
            required_exports: Set of required exported symbols
            min_version: Minimum required module version (semantic versioning, e.g., "1.0.0")
        """
        self.required_exports = required_exports or set()
        self.min_version = min_version

    def validate(self, module: Any) -> bool:
        """Validate module contract.

        Args:
            module: Module to validate

        Returns:
            True if valid

        Raises:
            ContractValidationError: If contract fails (fail-closed)
        """
        module_name = getattr(module, "__name__", "unknown")

        # Check required exports exist
        for export in self.required_exports:
            if not hasattr(module, export):
                raise ContractValidationError(
                    f"Missing required export: {export}",
                    module_name=module_name,
                )

        # Check version compatibility if required
        if self.min_version:
            module_version = getattr(module, "__version__", None)
            if not module_version:
                raise ContractValidationError(
                    f"Module missing __version__ attribute (required: >= {self.min_version})",
                    module_name=module_name,
                )

            # Simple semantic versioning comparison (X.Y.Z format)
            if not self._version_satisfies(module_version, self.min_version):
                raise ContractValidationError(
                    f"Module version {module_version} does not meet requirement >= {self.min_version}",
                    module_name=module_name,
                )

        return True

    @staticmethod
    def _version_satisfies(actual: str, required: str) -> bool:
        """Check if actual version satisfies required minimum version.

        Args:
            actual: Module's actual version (e.g., "1.2.3")
            required: Required minimum version (e.g., "1.0.0")

        Returns:
            True if actual >= required
        """
        try:
            actual_parts = tuple(map(int, actual.split('.')))
            required_parts = tuple(map(int, required.split('.')))
            return actual_parts >= required_parts
        except (ValueError, AttributeError):
            # If parsing fails, fail-closed: reject unknown format
            return False

    def validate_on_import(self, module: Any) -> None:
        """Validate module and crash if invalid (fail-closed, no recovery).

        Args:
            module: Module to validate
        """
        try:
            self.validate(module)
        except ContractValidationError as e:
            # Fail-closed: crash immediately, no recovery
            import sys
            sys.exit(1)

    @staticmethod
    def create_for_module(module: Any) -> ModuleContract:
        """Create contract from module's declared exports."""
        if hasattr(module, "__all__"):
            required_exports = set(module.__all__)
        else:
            # Infer public exports (non-private symbols)
            required_exports = {
                name for name in dir(module)
                if not name.startswith("_")
            }

        return ModuleContract(required_exports=required_exports)
