"""BYOK (Bring-Your-Own-Key) decryption + vault write pipeline.

Receives an RSA-OAEP-SHA256 ciphertext from the Management API,
decrypts it with the instance private key, and writes the plaintext
to the L16 vault AND to ~/.config/corvin-voice/service.env (WA-22 —
the vault's write path was previously disconnected from every actual
runtime reader; service.env is the single source of truth every
provider-key consumer resolves through, see
operator/bridges/shared/provider_keys.py).

Key name rules (per ADR-0047):
  Allowed:  anthropic_api_key, openai_api_key, stt_openai_api_key,
            stt_local_whisper_api_key, openrouter_api_key, ollama_api_key,
            custom_<slug> (slug: [a-z0-9_-], ≤32)
  Forbidden names containing: audit, vault, path_gate, policy, license
"""
from __future__ import annotations

import os
import re
import sys
import time
from pathlib import Path
from typing import Any

from .keypair import decrypt_oaep

_KNOWN_KEY_NAMES = frozenset({
    "anthropic_api_key",
    "openai_api_key",
    "stt_openai_api_key",
    "stt_local_whisper_api_key",
    # ADR-0181 provider routing: openrouter/ollama_cloud both need a
    # credential_env the console can actually let an operator set (was
    # previously only settable by hand-editing service.env).
    "openrouter_api_key",
    "ollama_api_key",
})

_FORBIDDEN_SUBSTRINGS = ("audit", "vault", "path_gate", "policy", "license")
_CUSTOM_SLUG_RE = re.compile(r"^custom_[a-z0-9_-]{1,32}$")
_KNOWN_RE = re.compile(r"^[a-z][a-z0-9_]{0,39}$")

# WA-21: nothing in this pipeline ever checked that a decrypted value looks
# like the provider's real key format — any non-empty string, including
# browser/password-manager autofill garbage silently dropped into the
# console's "paste a new key" fields (autoComplete="off" is well known to be
# ignored by Chromium/most password managers for type="password" inputs),
# was accepted and stored as though it were a working credential, with no
# feedback that it plainly wasn't. Only the well-known keys with a stable,
# documented prefix are checked; custom_<slug> and stt_local_whisper_api_key
# (no fixed format) are intentionally exempt.
_SHAPE_PATTERNS: dict[str, re.Pattern[str]] = {
    "anthropic_api_key": re.compile(r"^sk-ant-"),
    "openai_api_key": re.compile(r"^sk-"),
    "stt_openai_api_key": re.compile(r"^sk-"),
    "openrouter_api_key": re.compile(r"^sk-or-"),
    # No shape check for ollama_api_key: Ollama Cloud key formatting isn't a
    # documented, stable prefix the way sk-*/sk-ant-*/sk-or-* are.
}


def _check_key_shape(key_name: str, plaintext: str) -> None:
    """Raise ValueError when a well-known key's value doesn't match its
    provider's documented prefix — never blocks custom/unrecognised names."""
    pattern = _SHAPE_PATTERNS.get(key_name)
    if pattern is None:
        return
    if not pattern.match(plaintext.strip()):
        expected = pattern.pattern.lstrip("^")
        raise ValueError(
            f"{key_name} does not look like a valid key "
            f"(expected it to start with {expected!r})"
        )


def validate_key_name(key_name: str) -> None:
    """Raise ValueError for names that violate ADR-0047 key-name rules."""
    if not isinstance(key_name, str) or not key_name:
        raise ValueError("key_name must be a non-empty string")

    lower = key_name.lower()
    for sub in _FORBIDDEN_SUBSTRINGS:
        if sub in lower:
            raise ValueError(
                f"key_name {key_name!r} contains reserved substring {sub!r}"
            )

    if key_name in _KNOWN_KEY_NAMES:
        return
    if _CUSTOM_SLUG_RE.match(key_name):
        return
    raise ValueError(
        f"key_name {key_name!r} is not in the allowed set and is not a "
        f"valid custom_<slug> name (custom_ prefix + [a-z0-9_-], ≤32 chars)"
    )


def _vault_set(key_name: str, value: str, *, vault_dir: Path | None = None) -> None:
    """Write *value* to the L16 vault under *key_name*.

    When *vault_dir* is specified (tests / explicit override), writes
    directly to that directory without touching the global vault.py
    module-level state.  Otherwise delegates to vault.py's set_item().
    """
    import json
    import shutil

    if vault_dir is not None:
        # Direct write path — used for tests and explicit vault_dir overrides.
        vault_subdir = vault_dir / "vault"
        vault_subdir.mkdir(parents=True, exist_ok=True)
        try:
            vault_subdir.chmod(0o700)
        except OSError:
            pass
        item_path = vault_subdir / f"{key_name}.json"
        payload = json.dumps({"name": key_name, "kind": "api_key", "value": value})
        tmp = item_path.with_suffix(".json.tmp")
        tmp.write_text(payload)
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        shutil.move(str(tmp), str(item_path))

        # Update a minimal INDEX.json so vault.py list_items() can discover it.
        index_path = vault_subdir.parent / "vault" / ".." / "INDEX.json"
        # Keep index alongside vault files.
        index_file = vault_subdir / "INDEX.json"
        try:
            existing = json.loads(index_file.read_text()) if index_file.exists() else []
        except (json.JSONDecodeError, OSError):
            existing = []
        existing = [e for e in existing if e.get("name") != key_name]
        existing.append({"name": key_name, "kind": "api_key", "tags": ["byok"],
                         "encrypted": False, "auto_unlock": True})
        existing.sort(key=lambda e: e.get("name", ""))
        index_file.write_text(json.dumps(existing, indent=2))
        try:
            index_file.chmod(0o600)
        except OSError:
            pass
        return

    # Default path: use vault.py.
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "operator").is_dir():
            shared = parent / "operator" / "bridges" / "shared"
            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            break

    import vault as _vault  # type: ignore
    _vault.set_item(
        key_name,
        value,
        kind="api_key",
        tags=["byok"],
        encrypted=False,
        auto_unlock=True,
    )


def _write_service_env(
    key_name: str, value: str, *, service_env_path: Path | None = None,
) -> None:
    """WA-22: write *value* into service.env — the ONE file say.py,
    stt/openai_whisper.py, and every other consumer actually reads at
    runtime. Before this, apply_byok_secret() only wrote into the vault
    (above), which nothing reads back for provider keys — a key saved
    through the BYOK UI silently never took effect anywhere. Raises on
    failure rather than degrading silently: if this write doesn't land,
    the "single source of truth" file is now stale and every consumer
    will keep resolving the old value (or none).

    *service_env_path*, when given, is an isolated override (tests) — see
    provider_keys.write_key's path_override for why this parameter exists
    at all: a prior version of this function had no override and a test
    run silently overwrote the real ~/.config/corvin-voice/service.env,
    clobbering a working CORVIN_STT_OPENAI_KEY with test fixture garbage.
    """
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / "operator").is_dir():
            shared = parent / "operator" / "bridges" / "shared"
            if str(shared) not in sys.path:
                sys.path.insert(0, str(shared))
            break

    import provider_keys  # type: ignore
    provider_keys.write_key(key_name, value, path_override=service_env_path)


def apply_byok_secret(
    key_name: str,
    ciphertext_b64: str,
    *,
    agent_dir: Path | None = None,
    vault_dir: Path | None = None,
    service_env_path: Path | None = None,
    tenant_id: str | None = None,
    updated_by: str = "unknown",
) -> dict[str, Any]:
    """Decrypt *ciphertext_b64* and write the result to the L16 vault and
    to service.env (see _write_service_env).

    Returns a dict with ``{key_name, rotated_at, last4}`` where last4 is
    the last 4 chars of the plaintext (informational; opt-in).

    Emits ``vault.secret_rotated`` audit event.

    *service_env_path*: test-only override, see _write_service_env. When
    None (production default), writes to the real
    ~/.config/corvin-voice/service.env.
    """
    validate_key_name(key_name)

    plaintext = decrypt_oaep(
        ciphertext_b64,
        agent_dir=agent_dir,
        tenant_id=tenant_id,
    )

    _check_key_shape(key_name, plaintext)

    _vault_set(key_name, plaintext, vault_dir=vault_dir)
    _write_service_env(key_name, plaintext, service_env_path=service_env_path)

    rotated_at = time.time()
    last4 = plaintext[-4:] if len(plaintext) >= 4 else "****"

    _emit_vault_rotated(key_name, tenant_id=tenant_id)

    return {
        "key_name": key_name,
        "rotated_at": rotated_at,
        "last4": last4,
    }


def _emit_vault_rotated(key_name: str, *, tenant_id: str | None) -> None:
    """Best-effort audit emit.  Never raises."""
    try:
        from .audit import agent_event
        agent_event(
            "vault.secret_rotated",
            tenant_id=tenant_id or os.environ.get("CORVIN_TENANT_ID", "_default"),
            details={"key_name": key_name},
        )
    except Exception:  # noqa: BLE001
        pass
