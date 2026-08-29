"""Subprocess environment hardening (ADR-0423 Phase 2, Gap 6).

Prepares safe, reproducible environments for subprocess calls.
Ensures CORVIN_HOME, CORVIN_TENANT_ID, and other critical vars are explicitly set.
Whitelists env vars to prevent PII/secret leakage.

Used by: workflow runners, bridge handlers, external tool invocations
"""

from __future__ import annotations

import logging
import os
from typing import Dict, Optional

from .path_resolver import resolve_corvin_home, resolve_tenant_id

logger = logging.getLogger(__name__)

# Whitelist of env vars to preserve from parent environment
_WHITELIST_PATTERNS = {
    # Standard path vars
    "PATH",
    "PYTHONPATH",
    "LD_LIBRARY_PATH",
    "DYLD_LIBRARY_PATH",  # macOS
    # Python/tool config
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "PYTHONHASHSEED",
    # Locale/encoding
    "LANG",
    "LC_ALL",
    "LC_COLLATE",
    "LC_CTYPE",
    "LC_MESSAGES",
    "LC_MONETARY",
    "LC_NUMERIC",
    "LC_TIME",
    # Timezone
    "TZ",
    # Platform detection
    "CI",
    "GITHUB_ACTIONS",
    # HTTP/network (no secrets via proxy vars)
    "http_proxy",
    "https_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "no_proxy",
    "NO_PROXY",
    # Git (safe config only)
    "GIT_AUTHOR_NAME",
    "GIT_AUTHOR_EMAIL",
    "GIT_COMMITTER_NAME",
    "GIT_COMMITTER_EMAIL",
    # Shell
    "SHELL",
    "TERM",
}


def prepare_subprocess_env(
    tenant_id: Optional[str] = None,
    additional_vars: Optional[Dict[str, str]] = None,
) -> Dict[str, str]:
    """Prepare a safe environment for subprocess execution.

    Builds env dict with:
    - Explicit CORVIN_HOME (fail-closed if unset)
    - Explicit CORVIN_TENANT_ID
    - Whitelisted parent env vars (path, locale, config only)
    - Additional vars (caller-provided, validated)

    Args:
        tenant_id: Tenant ID (or resolved from env/default)
        additional_vars: Extra env vars to include (validated for PII)

    Returns:
        Dict suitable for subprocess.Popen(..., env=...)

    Raises:
        ValueError: If CORVIN_HOME cannot be resolved
        SecurityError: If additional_vars contain suspected PII/secrets
    """
    tenant = resolve_tenant_id(tenant_id)
    corvin_home = resolve_corvin_home()

    # Build base env with only whitelisted vars
    env: Dict[str, str] = {}

    # Copy whitelisted vars from parent
    for key, value in os.environ.items():
        if key in _WHITELIST_PATTERNS:
            env[key] = value

    # Set critical Corvin vars explicitly (always override)
    env["CORVIN_HOME"] = str(corvin_home)
    env["CORVIN_TENANT_ID"] = tenant

    # Add caller-provided vars (after validation)
    if additional_vars:
        for key, value in additional_vars.items():
            if not isinstance(key, str) or not isinstance(value, str):
                raise ValueError(
                    f"additional_vars must be Dict[str, str], got {type(key)}, {type(value)}"
                )
            if _is_suspected_pii(key, value):
                raise ValueError(
                    f"Suspected PII/secret in additional_vars[{key!r}]: {value[:20]}..."
                )
            env[key] = value

    logger.debug(
        f"Prepared subprocess env: CORVIN_HOME={corvin_home}, "
        f"CORVIN_TENANT_ID={tenant}, {len(env)} total vars"
    )

    return env


def _is_suspected_pii(key: str, value: str) -> bool:
    """Heuristic check for suspected PII/secrets in env values.

    Checks for:
    - API keys / tokens (length, patterns)
    - Passwords
    - SSH keys
    - Bearer tokens
    - AWS/Cloud credentials

    Args:
        key: Env var name
        value: Env var value

    Returns:
        True if suspected PII/secret, False otherwise
    """
    key_lower = key.lower()
    value_lower = value.lower()

    # Check key patterns
    suspicious_key_patterns = {
        "password",
        "token",
        "secret",
        "key",
        "aws_",
        "gcp_",
        "azure_",
        "api_key",
        "private_key",
        "ssh_key",
        "oauth",
        "bearer",
    }

    for pattern in suspicious_key_patterns:
        if pattern in key_lower:
            return True

    # Check value patterns for obvious markers
    if value.startswith("-----BEGIN"):  # SSH/PEM keys
        return True
    if value.startswith("sk_") or value.startswith("pk_"):  # Stripe-like keys
        return True
    if value.startswith("Bearer ") or value.startswith("bearer "):
        return True
    if len(value) > 100 and not any(c == " " for c in value):  # Suspicious long strings
        return True

    return False
