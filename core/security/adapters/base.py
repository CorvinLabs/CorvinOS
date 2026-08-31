"""Base adapter classes."""

from dataclasses import dataclass


@dataclass
class SecurityPipelineError:
    """Standard error format across all adapters (Finding #11)."""
    error_code: str
    message: str
    decision_hash: str
    severity: str = "error"
    details: dict = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}
