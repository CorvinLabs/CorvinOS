"""Secret vault — capability-style secret injection for forged tools.

Layer 16 v3 — Secret-Injection.

The vault is a JSON file at ``~/.config/corvin-voice/secrets.json`` (mode
0600) that maps an env-var name to its plaintext value::

    {
      "OPENAI_API_KEY":    "sk-...",
      "ANTHROPIC_API_KEY": "sk-ant-..."
    }

A forged tool declares which keys it needs in its ``meta.secrets`` list.
The runner reads only the requested keys from the vault and merges them
into the bwrap subprocess env under the same name. The values never
appear in:

  - the input payload (so the LLM does not see them when calling the tool)
  - the tool spec on disk (only the *names* are stored)
  - the audit chain (only ``secrets_used: [<names>]``)
  - the run manifest's redacted payload copy

Threat model:

  - prevent the LLM from knowing the secret value, even when it asks the
    tool to "echo OPENAI_API_KEY"
  - prevent secret values from landing in stdout via accidental ``print(env)``
    by best-effort literal substitution in stdout/stderr at run time
  - prevent a persona from minting tools that demand keys it has not been
    explicitly authorised for, via ``Policy.persona_secret_allow``

Non-goals:

  - protect against an actively hostile tool body (a tool that opens a
    socket to exfiltrate the value bypasses the redaction; that is
    persona-sandbox + linter territory, not vault territory)
  - replace OS keyring / secret service (this is a single-file vault for
    operator convenience; an integration with libsecret is a future swap-in)
"""
from __future__ import annotations

import json
import logging
import os
import re
import stat
import sys
from pathlib import Path

log = logging.getLogger(__name__)
_WIN_MODE_CHECK_SKIPPED_WARNED = False

# Strict env-var-style key — uppercase, digits, underscores, must start
# with a letter or underscore. Same convention as POSIX env vars. Rejects
# path-traversal sequences ("../"), arbitrary punctuation, leading digits.
_KEY_RE = re.compile(r"^[A-Z_][A-Z0-9_]*$")
KEY_MAX_LEN = 64


class VaultError(RuntimeError):
    """Raised on malformed vault files, unreadable paths, etc."""


class SecretRefError(ValueError):
    """Raised when a tool spec declares an invalid secret reference."""


def is_valid_key(key: str) -> bool:
    """True if *key* matches the strict env-var pattern."""
    if not isinstance(key, str) or not key or len(key) > KEY_MAX_LEN:
        return False
    return _KEY_RE.match(key) is not None


def validate_secret_refs(refs: list[str] | None) -> list[str]:
    """Return a normalised, sorted, deduplicated list of valid keys.

    Raises ``SecretRefError`` on:
      - non-list input
      - any element that fails ``is_valid_key``
      - more than 16 distinct refs (cap to keep tool specs sane)
    """
    if refs is None:
        return []
    if not isinstance(refs, list):
        raise SecretRefError(
            f"meta.secrets must be a list, got {type(refs).__name__}"
        )
    cleaned: set[str] = set()
    for r in refs:
        if not isinstance(r, str):
            raise SecretRefError(f"secret-ref must be a string, got {r!r}")
        if not is_valid_key(r):
            raise SecretRefError(
                f"secret-ref {r!r} not a valid env-var name "
                f"(uppercase + digits + underscores, must start with a "
                f"letter/_; max {KEY_MAX_LEN} chars)"
            )
        cleaned.add(r)
    if len(cleaned) > 16:
        raise SecretRefError(
            f"meta.secrets has {len(cleaned)} entries; max 16"
        )
    return sorted(cleaned)


# -- vault location --------------------------------------------------------


def default_vault_path() -> Path:
    """Canonical vault location: ``~/.config/corvin-voice/secrets.json``.

    Override via ``CORVIN_SECRET_VAULT`` env var."""
    env = os.environ.get("CORVIN_SECRET_VAULT")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(os.path.expanduser(cfg_home)) / "corvin-voice" / "secrets.json"


# -- load + lookup ---------------------------------------------------------

def _check_mode(path: Path) -> None:
    """Refuse to load a vault that's group/world-readable.

    Symmetric to gpg's strict-mode check: a 0644 vault is a misconfiguration
    we'd rather surface than silently honour. Owner read+write only.
    """
    try:
        st = path.stat()
    except OSError as exc:
        raise VaultError(f"vault stat failed: {exc}") from exc
    mode = stat.S_IMODE(st.st_mode)
    # Allow 0o600, 0o400. Anything with group/other bits set is rejected.
    # Windows: NTFS has no POSIX group/other bits, so st_mode always looks
    # permissive there regardless of real ACLs — skip the check (this would
    # otherwise break the vault on every Windows install). Unlike POSIX, this
    # is now an unconditional bypass with no NTFS-ACL-equivalent check behind
    # it (adversarial review finding) — log once per process so a genuinely
    # shared/multi-user Windows box at least has an operator-visible signal
    # that this hardening layer is inactive there.
    if sys.platform.startswith("win"):
        global _WIN_MODE_CHECK_SKIPPED_WARNED
        if not _WIN_MODE_CHECK_SKIPPED_WARNED:
            log.warning(
                "secret_vault: file-permission hardening is inactive on Windows "
                "(NTFS has no POSIX mode bits) — on a shared/multi-user machine, "
                "restrict access to %s via NTFS ACLs (icacls) yourself.",
                path,
            )
            _WIN_MODE_CHECK_SKIPPED_WARNED = True
        return
    if mode & 0o077:
        raise VaultError(
            f"vault {path} mode {oct(mode)} too permissive "
            f"(must be 0600 or 0400). Run: chmod 600 {path}"
        )


def load_vault(path: Path | None = None) -> dict[str, str]:
    """Read the vault file and return ``{key: value}``.

    Returns ``{}`` when the file does not exist (vault is optional —
    tools without ``meta.secrets`` don't need it). Raises ``VaultError``
    on malformed JSON, wrong file mode, or non-string values.

    The returned dict is freshly read on every call — no caching, no
    mtime tracking. The cost (one stat + one read of a tiny JSON file)
    is well below the bwrap fork overhead, and "always fresh" matches
    the rest of the bridge's hot-reload convention.
    """
    p = path or default_vault_path()
    if not p.exists():
        return {}
    _check_mode(p)
    try:
        raw = json.loads(p.read_text())
    except json.JSONDecodeError as exc:
        raise VaultError(f"vault {p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise VaultError(f"vault {p} top-level must be an object")
    out: dict[str, str] = {}
    for k, v in raw.items():
        if not is_valid_key(k):
            raise VaultError(
                f"vault {p} contains invalid key {k!r} "
                f"(must match {_KEY_RE.pattern})"
            )
        if not isinstance(v, str):
            raise VaultError(
                f"vault {p} value for {k!r} must be a string, "
                f"got {type(v).__name__}"
            )
        out[k] = v
    return out


def resolve_secrets(
    refs: list[str],
    *,
    vault_path: Path | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Look up *refs* in the vault.

    Returns ``(resolved, missing)`` where:
      - ``resolved`` maps env-var-name → plaintext value for every ref
        that was found in the vault
      - ``missing`` lists the refs that were declared by the tool but
        absent from the vault

    The split lets the runner decide how to handle a missing secret:
    fail closed (refuse to run), or proceed with the env unset (the
    tool's own logic deals with it). Today the runner fails closed.
    """
    if not refs:
        return {}, []
    vault = load_vault(vault_path)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for r in refs:
        if r in vault:
            resolved[r] = vault[r]
        else:
            missing.append(r)
    return resolved, missing


# -- redaction -------------------------------------------------------------

# Anything shorter than this we don't redact — too high a false-positive
# rate for tiny strings (e.g. short passphrases would clobber every
# occurrence of the substring). 8 chars is the minimum API-key length
# we expect.
REDACT_MIN_VALUE_LEN = 8


def redact_values(text: str, values: list[str]) -> str:
    """Replace every literal occurrence of any *values* element with
    ``<redacted>``. Best-effort defensive measure for stdout/stderr — a
    well-behaved tool never emits its secrets, but a buggy ``print(env)``
    will land here, and we strip it before the bytes ever reach the LLM.

    Values shorter than ``REDACT_MIN_VALUE_LEN`` are skipped to avoid
    poisoning common substrings.

    Sorted longest-first so a value that contains another (e.g.
    ``OPENAI_API_KEY = "sk-abc-XYZ"`` and ``"abc"``) doesn't break the
    longer match.
    """
    if not values or not text:
        return text
    sortable = sorted(
        (v for v in values if isinstance(v, str)
         and len(v) >= REDACT_MIN_VALUE_LEN),
        key=len, reverse=True,
    )
    out = text
    for v in sortable:
        out = out.replace(v, "<redacted>")
    return out
