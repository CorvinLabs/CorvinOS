"""
Validator Factory for Input Validation — ADR-0296

Central, pluggable validator registry with deny-by-default validation.
All user input validated before reaching business logic.
"""

from core.validators.factory import FACTORY, ValidatorFactory
from core.validators.rules import (
    validate_alphanumeric,
    validate_email,
    validate_flag_id,
    validate_non_empty_string,
    validate_peer_id,
    validate_plugin_id,
    validate_port,
    validate_string_length,
    validate_tenant_id,
    validate_url,
    validate_uuid4,
)

# Register all built-in validators
FACTORY.register("peer_id", validate_peer_id)
FACTORY.register("flag_id", validate_flag_id)
FACTORY.register("plugin_id", validate_plugin_id)
FACTORY.register("tenant_id", validate_tenant_id)
FACTORY.register("email", validate_email)
FACTORY.register("url", validate_url)
FACTORY.register("uuid4", validate_uuid4)
FACTORY.register("port", validate_port)
FACTORY.register("alphanumeric", validate_alphanumeric)
FACTORY.register("non_empty_string", validate_non_empty_string)

__all__ = [
    "FACTORY",
    "ValidatorFactory",
    "validate_peer_id",
    "validate_flag_id",
    "validate_plugin_id",
    "validate_tenant_id",
    "validate_email",
    "validate_url",
    "validate_uuid4",
    "validate_port",
    "validate_alphanumeric",
    "validate_non_empty_string",
    "validate_string_length",
]
