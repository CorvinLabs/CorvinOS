"""Fail-closed tenant, session, and channel ID validation.

Centralizes validation to prevent path traversal, escape attempts, and
cross-tenant data access. Used by all subsystems that accept tenant_id,
session_id, or channel parameters.

GDPR Art. 5 (integrity) + ADR-0007 (multi-tenant axis) + ADR-0250 (tenant scope).
"""

import re
from typing import Optional


# Tenant ID: lowercase alphanumeric + underscore, hyphen allowed after the
# first character, max 63 chars. This is the SAME pattern as the canonical
# resolver ``forge.tenants.validate_tenant_id`` (CLAUDE.md § Multi-tenant Axis).
# Until 2026-09-06 this module rejected hyphens while forge accepted them, so a
# tenant such as ``acme-corp`` could be created and audited by the core but
# was refused by every learning/skills subsystem (adversarial review F21).
TENANT_ID_REGEX = r"^[a-z0-9_][a-z0-9_-]{0,62}$"
RESERVED_TENANT_NAMES = frozenset({
    ".", "..", "global", "bridges",
    # Admin/system names
    "root", "admin", "system", "service", "operator",
    "localhost", "local", "test", "internal", "reserved",
})

# Session ID: max 128 chars, ONE path segment. Allows UUIDs, snowflake ids and
# chat ids as the bridges deliver them - including a leading hyphen, which is
# how Telegram numbers groups and supergroups (``-1001234567890``). A ``/`` is
# never part of it (``discord/123`` is channel + session, two segments), and
# neither is a leading dot, so ``.`` and ``..`` cannot be named.
SESSION_ID_MAX_LEN = 128
SESSION_ID_REGEX = r"[A-Za-z0-9_-][A-Za-z0-9_.@+=-]{0,127}"

# Channel ID: alphanumeric + underscores. Max 64 chars.
# [PSEUDOCODE] Examples: "discord", "slack", "teams", "telegram"
CHANNEL_ID_REGEX = r"^[a-z0-9_]{1,64}$"


def validate_tenant_id(tenant_id: str) -> str:
    """Validate tenant ID (fail-closed, GDPR Art. 5 integrity).

    Ensures tenant_id cannot be used for path traversal or escape attempts.
    Rejects reserved names and invalid characters.

    Args:
        tenant_id: Tenant identifier to validate

    Returns:
        The validated tenant_id (pass-through if valid)

    Raises:
        ValueError: If tenant_id is invalid (empty, contains path chars,
                    exceeds length, or is a reserved name)
    """
    # Type check
    if not isinstance(tenant_id, str):
        raise ValueError(f"tenant_id must be string, got {type(tenant_id).__name__}")

    # Empty/whitespace check
    if not tenant_id or len(tenant_id.strip()) == 0:
        raise ValueError("tenant_id cannot be empty or whitespace-only")

    # Path traversal check (fail-closed)
    if ".." in tenant_id or "/" in tenant_id or "\\" in tenant_id:
        raise ValueError(
            f"tenant_id contains path traversal characters: {tenant_id!r}"
        )

    # Format check: lowercase alphanumeric + underscore (+ hyphen after the
    # first char), 1-63 chars — identical to forge.tenants.
    # fullmatch: ``re.match`` with ``$`` also matched before a final newline, so
    # ``"acme\n"`` passed and named a different directory (review 2026-11-05, LRN-15).
    if not re.fullmatch(TENANT_ID_REGEX, tenant_id):
        raise ValueError(
            f"tenant_id contains invalid characters: {tenant_id!r}. "
            f"Only lowercase alphanumeric, underscore and hyphen (not leading) allowed (1-63 chars)."
        )

    # Reserved names check (fail-closed)
    if tenant_id in RESERVED_TENANT_NAMES:
        raise ValueError(
            f"tenant_id is reserved: {tenant_id!r}. "
            f"Reserved: {', '.join(sorted(RESERVED_TENANT_NAMES))}"
        )

    return tenant_id


def validate_session_id(session_id: str) -> str:
    """Validate session ID (fail-closed).

    Ensures session_id is a reasonable length and non-empty.
    Accepts UUIDs, snowflake IDs, and channel-specific session IDs.

    Args:
        session_id: Session identifier to validate

    Returns:
        The validated session_id (pass-through if valid)

    Raises:
        ValueError: If session_id is invalid (empty, exceeds max length,
                    contains null bytes or path traversal)
    """
    # Type check
    if not isinstance(session_id, str):
        raise ValueError(
            f"session_id must be string, got {type(session_id).__name__}"
        )

    # Empty/whitespace check
    if not session_id or len(session_id.strip()) == 0:
        raise ValueError("session_id cannot be empty or whitespace-only")

    # Length check (fail-closed)
    if len(session_id) > SESSION_ID_MAX_LEN:
        raise ValueError(
            f"session_id exceeds maximum length ({SESSION_ID_MAX_LEN}): {session_id!r}"
        )

    # No path traversal (fail-closed)
    if ".." in session_id or "/" in session_id or "\\" in session_id:
        raise ValueError(
            f"session_id contains path traversal characters: {session_id!r}"
        )

    # No null bytes (fail-closed)
    if "\x00" in session_id:
        raise ValueError(
            f"session_id contains null byte: {session_id!r}"
        )

    # Shape (fail-closed). ``fullmatch`` over a positive character class: the
    # checks above were a denylist, so ``"."`` (the directory itself), a
    # trailing newline, control characters, ``:`` (NTFS streams / drive
    # prefixes) and shell metacharacters all passed and reached
    # ``tenant_session_dir``. Accepted: UUIDs, snowflake ids and
    # channel-style ids built from letters, digits, ``_ . - @ + =`` — not
    # dots only and not a leading dot. A leading hyphen IS accepted: Telegram
    # group chat ids are negative numbers (``-100...``); refusing them cut
    # every Telegram group off from its session directory.
    if not re.fullmatch(SESSION_ID_REGEX, session_id) or not session_id.strip("."):
        raise ValueError(
            f"session_id contains invalid characters: {session_id!r}"
        )

    return session_id


def validate_channel_id(channel: str) -> str:
    """Validate channel/bridge ID (fail-closed).

    Ensures channel is alphanumeric with underscores, used for bridge channels
    like "discord", "slack", "telegram", etc.

    Args:
        channel: Channel/bridge identifier to validate

    Returns:
        The validated channel (pass-through if valid)

    Raises:
        ValueError: If channel is invalid (empty, contains invalid chars,
                    exceeds length)
    """
    # Type check
    if not isinstance(channel, str):
        raise ValueError(f"channel must be string, got {type(channel).__name__}")

    # Empty/whitespace check
    if not channel or len(channel.strip()) == 0:
        raise ValueError("channel cannot be empty or whitespace-only")

    # Format check: lowercase alphanumeric + underscores, 1-64 chars
    # fullmatch: ``re.match`` with ``$`` also matches before a final newline.
    if not re.fullmatch(CHANNEL_ID_REGEX, channel):
        raise ValueError(
            f"channel contains invalid characters: {channel!r}. "
            f"Only lowercase alphanumeric and underscores allowed (1-64 chars)."
        )

    return channel


__all__ = [
    "validate_tenant_id",
    "validate_session_id",
    "validate_channel_id",
    "TENANT_ID_REGEX",
    "CHANNEL_ID_REGEX",
    "SESSION_ID_REGEX",
    "SESSION_ID_MAX_LEN",
    "RESERVED_TENANT_NAMES",
]
