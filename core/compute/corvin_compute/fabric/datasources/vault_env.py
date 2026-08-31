"""Vault integration for DataSource secret injection (ADR-0026 Section D).

check_vault_keys_present: reads mode-0600 JSON, checks key PRESENCE only.
get_vault_env_for_bwrap: returns {key: value} for bwrap env injection ONLY.

IMPORTANT: check_vault_keys_present NEVER returns secret values.
get_vault_env_for_bwrap is ONLY called at bwrap spawn time.
"""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path


class MissingSecret(KeyError):
    """Raised when a required secret key is absent from the vault."""


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_vault(vault_path: Path) -> dict[str, str]:
    """Read vault JSON file, enforcing mode 0600."""
    if not vault_path.exists():
        raise MissingSecret(f"Vault file not found: {vault_path}")

    file_stat = vault_path.stat()
    mode = stat.S_IMODE(file_stat.st_mode)
    if mode != 0o600:
        raise PermissionError(
            f"Vault file {vault_path} has mode {oct(mode)}, expected 0o600. "
            "Fix with: chmod 600 <vault_path>"
        )

    return json.loads(vault_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_vault_keys_present(
    secret_keys: list[str],
    vault_path: Path,
) -> None:
    """Verify that every key in secret_keys exists in the vault.

    NEVER reads the values — only checks key presence.
    Raises MissingSecret if any key is absent.
    """
    vault_data = _read_vault(vault_path)
    missing = [k for k in secret_keys if k not in vault_data]
    if missing:
        raise MissingSecret(
            f"Required secret key(s) not found in vault: {missing}"
        )


def get_vault_env_for_bwrap(
    secret_keys: list[str],
    vault_path: Path,
) -> dict[str, str]:
    """Return {key: value} dict for bwrap env injection.

    This function is ONLY called at bwrap spawn time, not in the MCP or
    adapter process. The returned dict is passed directly to bwrap's
    --setenv arguments.
    """
    vault_data = _read_vault(vault_path)
    result: dict[str, str] = {}
    missing: list[str] = []
    for key in secret_keys:
        if key not in vault_data:
            missing.append(key)
        else:
            result[key] = vault_data[key]
    if missing:
        raise MissingSecret(
            f"Required secret key(s) not found in vault: {missing}"
        )
    return result


__all__ = [
    "MissingSecret",
    "check_vault_keys_present",
    "get_vault_env_for_bwrap",
]
