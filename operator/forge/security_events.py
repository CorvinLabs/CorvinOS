"""Forge security events module - stub for compatibility."""

def log_event(event_type, data=None):
    """Log a security event."""
    pass

def emit_security_event(event_type, **kwargs):
    """Emit a security event."""
    pass

__all__ = ['log_event', 'emit_security_event']
