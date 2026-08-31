"""Security pipeline exception hierarchy."""


class PipelineExecutionError(Exception):
    """Base exception for all pipeline errors."""
    pass


class CapabilityGateError(PipelineExecutionError):
    """Raised when capability check fails (Finding #3)."""
    pass


class ValidationGateError(PipelineExecutionError):
    """Raised when input validation fails (Finding #4)."""
    pass


class PIIDetectionError(PipelineExecutionError):
    """Raised when PII is detected."""
    pass


class AuditGateError(PipelineExecutionError):
    """Raised when audit recording fails (Finding #6)."""
    pass
