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


# -- per-tenant vaults (review 2026-11-19, R3-NEW-3) ------------------------
#
# The file above is HOST-global: one JSON for every tenant of the process. A
# datasource manifest of tenant B naming ``secrets: ["PGPASSWORD"]`` therefore
# received tenant A's plaintext. Each tenant now has its own vault next to it:
#
#     <vault dir>/tenants/<tenant_id>/secrets.json
#
# It lives under the same directory on purpose: the L10 path gate already
# refuses writes to any ``secrets.json`` below the vault directory, and the
# location is outside every worker cwd (``<tenant_home>/sessions/...``).
#
# Tenant file format (the host-global file keeps its flat ``{NAME: value}``)::
#
#     {"version": 1,
#      "secrets":  {"PGPASSWORD": "..."},
#      "bindings": {"PGPASSWORD": "db.internal"}}
#
# ``bindings`` ties a secret to the ONE target it may be sent to. It is set by
# the writer (``set_secret(..., bind_to=...)``) or, when the writer did not
# name one, by the first resolution (trust on first use). A later request for
# the same name with another target is refused - a connection re-registered
# with ``host: attacker.example`` does not get the password.
#
# Migration: the host-global file stays readable for the PROCESS tenant only
# (``CORVIN_TENANT_ID`` or ``_default``), never for another tenant, and a
# tenant entry always wins over it.

_TENANT_VAULT_VERSION = 1


class SecretBindingError(VaultError):
    """A secret was requested for a target other than the one it is bound to."""


def process_tenant_id() -> str:
    """The tenant this process runs as (``CORVIN_TENANT_ID`` or ``_default``)."""
    from .paths import _resolve_tenant_id  # noqa: PLC0415

    return _resolve_tenant_id(None)


def _checked_tenant(tenant_id: str) -> str:
    from .paths import _validate_tenant_id  # noqa: PLC0415

    try:
        return _validate_tenant_id(tenant_id)
    except ValueError as exc:
        raise VaultError(f"invalid tenant id for the secret vault: {exc}") from exc


def tenant_vault_path(tenant_id: str) -> Path:
    """``<vault dir>/tenants/<tenant_id>/secrets.json`` (no filesystem side effect)."""
    return default_vault_path().parent / "tenants" / _checked_tenant(tenant_id) / "secrets.json"


def normalise_binding(target: str | None) -> str:
    """Canonical form of a binding target (a host name, lower case, no trailing dot)."""
    if not isinstance(target, str):
        return ""
    return target.strip().lower().rstrip(".")[:255]


class _VaultLock:
    """Exclusive advisory lock for one vault directory (read-modify-write)."""

    def __init__(self, directory: Path) -> None:
        self._dir = directory
        self._fd: int | None = None

    def __enter__(self) -> "_VaultLock":
        self._dir.mkdir(parents=True, exist_ok=True)
        try:
            self._dir.chmod(0o700)
        except OSError:
            pass
        flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
        self._fd = os.open(str(self._dir / ".secrets.lock"), flags, 0o600)
        try:
            import fcntl  # noqa: PLC0415

            fcntl.flock(self._fd, fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - Windows without the shim
            pass
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is None:
            return
        try:
            import fcntl  # noqa: PLC0415

            fcntl.flock(self._fd, fcntl.LOCK_UN)
        except Exception:  # noqa: BLE001
            pass
        os.close(self._fd)
        self._fd = None


def _atomic_write_json(path: Path, payload: dict) -> None:
    import tempfile  # noqa: PLC0415

    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".secrets.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
        try:
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _read_tenant_file(path: Path) -> tuple[dict[str, str], dict[str, str]]:
    """``(secrets, bindings)`` of a tenant vault file; both empty when absent."""
    if not path.exists():
        return {}, {}
    if path.is_symlink():
        raise VaultError(f"vault {path} is a symlink")
    _check_mode(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise VaultError(f"vault {path} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("secrets", {}), dict) \
            or not isinstance(raw.get("bindings", {}), dict):
        raise VaultError(f"vault {path} is not a tenant vault object")
    secrets: dict[str, str] = {}
    for k, v in raw.get("secrets", {}).items():
        if not is_valid_key(k) or not isinstance(v, str):
            raise VaultError(f"vault {path} contains an invalid entry {k!r}")
        secrets[k] = v
    bindings = {
        k: normalise_binding(v) for k, v in raw.get("bindings", {}).items()
        if is_valid_key(k) and isinstance(v, str) and normalise_binding(v)
    }
    return secrets, bindings


def _write_tenant_file(path: Path, secrets: dict[str, str], bindings: dict[str, str]) -> None:
    _atomic_write_json(path, {
        "version": _TENANT_VAULT_VERSION,
        "secrets": dict(sorted(secrets.items())),
        "bindings": dict(sorted(bindings.items())),
    })


def _legacy_for(tenant_id: str) -> dict[str, str]:
    """The host-global vault, for the process tenant only; ``{}`` for any other."""
    if tenant_id != process_tenant_id():
        return {}
    return load_vault(default_vault_path())


def load_tenant_vault(tenant_id: str) -> dict[str, str]:
    """``{name: value}`` visible to *tenant_id*: its own vault, plus - for the
    process tenant only - the host-global one (a tenant entry wins)."""
    tid = _checked_tenant(tenant_id)
    own, _ = _read_tenant_file(tenant_vault_path(tid))
    merged = dict(_legacy_for(tid))
    merged.update(own)
    return merged


def _resolve_for_tenant(
    refs: list[str], tenant_id: str, bind_to: str | None,
) -> tuple[dict[str, str], list[str]]:
    tid = _checked_tenant(tenant_id)
    path = tenant_vault_path(tid)
    target = normalise_binding(bind_to)
    with _VaultLock(path.parent):
        own, bindings = _read_tenant_file(path)
        visible = dict(_legacy_for(tid))
        visible.update(own)
        resolved: dict[str, str] = {}
        missing: list[str] = []
        new_bindings = dict(bindings)
        for r in refs:
            if r not in visible:
                missing.append(r)
                continue
            if target:
                bound = bindings.get(r)
                if bound and bound != target:
                    raise SecretBindingError(
                        f"secret {r!r} is bound to another target and is not released "
                        f"for {target!r}; store the credential again to re-bind it")
                if not bound:
                    new_bindings[r] = target  # trust on first use
            resolved[r] = visible[r]
        if new_bindings != bindings and not missing:
            _write_tenant_file(path, own, new_bindings)
    return resolved, missing


def secret_binding(key: str, *, tenant_id: str) -> str:
    """The target *key* is bound to in *tenant_id*'s vault (``""`` when unbound)."""
    try:
        _, bindings = _read_tenant_file(tenant_vault_path(tenant_id))
    except VaultError:
        return ""
    return bindings.get(key, "")


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
        raw = json.loads(p.read_text(encoding="utf-8"))
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
    tenant_id: str | None = None,
    bind_to: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Look up *refs* in the vault.

    With ``tenant_id`` the lookup is confined to that tenant's vault (plus the
    host-global file for the process tenant only) and, when ``bind_to`` names a
    target, to secrets bound to that target (``SecretBindingError`` otherwise;
    an unbound secret is bound on first use). Without ``tenant_id`` it reads
    the host-global file - the forge runner's tool-secret path, which is
    governed by the persona ACL, not by a tenant.

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
    if tenant_id is not None and vault_path is None:
        return _resolve_for_tenant(list(refs), tenant_id, bind_to)
    vault = load_vault(vault_path)
    resolved: dict[str, str] = {}
    missing: list[str] = []
    for r in refs:
        if r in vault:
            resolved[r] = vault[r]
        else:
            missing.append(r)
    return resolved, missing


def set_secret(
    key: str,
    value: str,
    *,
    vault_path: Path | None = None,
    tenant_id: str | None = None,
    bind_to: str | None = None,
) -> Path:
    """Store ``value`` under ``key`` in the vault (create/replace, atomic, mode 0600).

    The writer half of :func:`load_vault`: the vault is what the compute runner and
    the forge runner read, so a credential saved here is the one a datasource-bound
    run actually receives. Existing entries are kept; a vault that is group/world
    readable or malformed is refused (``VaultError``) rather than rewritten.
    The read-modify-write runs under an exclusive lock, so two concurrent writers
    cannot drop each other's entry. Returns the vault path.

    With ``tenant_id`` the value goes into that tenant's own vault and can never
    replace another tenant's (or the host-global) entry. Storing a value resets
    the secret's target binding to ``bind_to`` (or to "unbound, bind on first use").
    """
    if not is_valid_key(key):
        raise SecretRefError(f"secret name {key!r} is not a valid env-var name")
    if not isinstance(value, str) or not value:
        raise VaultError("secret value must be a non-empty string")
    if tenant_id is not None and vault_path is None:
        p = tenant_vault_path(tenant_id)
        with _VaultLock(p.parent):
            secrets, bindings = _read_tenant_file(p)
            secrets[key] = value
            target = normalise_binding(bind_to)
            if target:
                bindings[key] = target
            else:
                bindings.pop(key, None)
            _write_tenant_file(p, secrets, bindings)
        return p
    p = vault_path or default_vault_path()
    with _VaultLock(p.parent):
        current = load_vault(p) if p.exists() else {}
        current[key] = value
        _atomic_write_json(p, current)
    return p


def secret_names(*, vault_path: Path | None = None, tenant_id: str | None = None) -> list[str]:
    """Names (never values) of the secrets in the vault; ``[]`` when there is none.

    With ``tenant_id``: the names visible to that tenant only."""
    try:
        if tenant_id is not None and vault_path is None:
            return sorted(load_tenant_vault(tenant_id))
        return sorted(load_vault(vault_path))
    except VaultError:
        return []


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
