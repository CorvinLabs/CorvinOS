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
    """Validate module interface contracts on load."""

    def __init__(self, required_exports: Set[str] = None):
        """Initialize contract.

        Args:
            required_exports: Set of required exported symbols
        """
        self.required_exports = required_exports or set()

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

        return True

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
