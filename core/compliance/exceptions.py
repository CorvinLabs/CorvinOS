"""Compliance exceptions."""


class ComplianceError(Exception):
    """Base compliance exception — fail-closed."""
    pass


class AuditChainError(ComplianceError):
    """Audit chain integrity violation."""
    pass


class TripwireError(ComplianceError):
    """Boot tripwire failed — platform shut down."""
    pass


class ConsentDeniedError(ComplianceError):
    """Consent not granted — operation denied."""
    pass
