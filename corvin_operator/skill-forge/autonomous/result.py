"""Validation result dataclass — shared across all validation layers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ValidationResult:
    """Immutable validation result per layer."""
    layer: int  # 1, 2, 3, ...
    passed: bool
    errors: list[str] = field(default_factory=list)  # What failed (fail-closed)
    warnings: list[str] = field(default_factory=list)  # What to watch
    metrics: dict[str, Any] = field(default_factory=dict)  # coverage %, test count, latency, etc.
    duration_ms: float = 0.0  # How long validation took

    def __str__(self) -> str:
        status = "✅ PASS" if self.passed else "❌ FAIL"
        msg = f"Layer {self.layer}: {status}"
        if self.metrics:
            metrics_str = ", ".join(f"{k}={v}" for k, v in self.metrics.items())
            msg += f" ({metrics_str})"
        if self.errors:
            msg += f" — Errors: {'; '.join(self.errors[:1])}"  # First error only
        if self.warnings and self.passed:
            msg += f" — Warnings: {'; '.join(self.warnings[:1])}"  # First warning only
        return msg
