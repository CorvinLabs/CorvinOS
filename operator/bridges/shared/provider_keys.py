"""provider_keys.py — canonical provider-key resolver. Single source of truth.

Named provider_keys, not secrets, deliberately: operator/bridges/shared is on
sys.path for adapter.py and other modules in this tree, and a module named
`secrets.py` here would shadow the Python stdlib `secrets` module for anyone
importing it unqualified (caught in review: adapter.py's own
`secrets.token_hex(8)` call broke under this exact collision during testing).

path-audit-class bug (2026-07-10): the same logical value (e.g. "the OpenAI
key used for STT") was independently resolved by say.py, stt/openai_whisper.py,
console byok.py, and console setup.py — four copies, three different
precedence orders, two different candidate-file lists (some checked `.env`
AND `service.env`, some only `service.env`). BYOK's own write path
(operator/agent/byok.py) wrote into a *fifth*, completely disconnected store
(the vault) that none of the four readers ever consulted — so a key saved
through the BYOK UI silently vanished.

This module is the ONE place that:
  - defines the canonical env-var name per logical key
  - defines the ONE precedence order (process env → service.env file →
    legacy aliases, for backward compat with pre-consolidation installs)
  - defines the ONE candidate file (service.env — .env is retired, nothing
    writes to it anymore)
  - provides both `resolve_key`/`key_present` (read) and `write_key` (write),
    so BYOK, the installer, and console settings all land in the same place.

Standalone scripts that must stay import-independent for portability
(say.py, stt/openai_whisper.py) keep their own private copies of this
logic, but those copies MUST stay byte-identical to this module — see the
parity guard in tests/test_secrets_ssot.py (same pattern as
tests/test_voice_config_ssot.py for the config-dir SSOT).
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_FORGE_TOP = HERE.parent.parent / "forge"
if _FORGE_TOP.is_dir() and str(_FORGE_TOP) not in sys.path:
    sys.path.insert(0, str(_FORGE_TOP))

try:
    from forge.paths import voice_config_dir as _forge_voice_config_dir  # type: ignore
except Exception:  # noqa: BLE001
    _forge_voice_config_dir = None  # type: ignore[assignment]


def voice_config_dir() -> Path:
    """SSOT for the corvin-voice config dir. Delegates to forge.paths when
    importable; falls back to the same VOICE_CONFIG_DIR → XDG_CONFIG_HOME →
    ~/.config rule otherwise (mirrors forge.paths.voice_config_dir())."""
    if _forge_voice_config_dir is not None:
        return _forge_voice_config_dir()
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
    return base / "corvin-voice"


SERVICE_ENV_FILENAME = "service.env"

_ENV_LINE_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")

# Canonical env-var name per logical key. This is the ONLY name anything
# writes going forward.
CANONICAL_ENV_VAR: dict[str, str] = {
    "anthropic_api_key": "ANTHROPIC_API_KEY",
    "openai_api_key": "OPENAI_API_KEY",
    "tts_openai_api_key": "CORVIN_TTS_OPENAI_KEY",
    "stt_openai_api_key": "CORVIN_STT_OPENAI_KEY",
    "stt_local_whisper_api_key": "CORVIN_STT_LOCAL_WHISPER_KEY",
    # ADR-0181 provider routing — names MUST match the `credential_env`
    # fields in operator/bundle/config-templates/engine_model_registry.yaml
    # (openrouter / ollama_cloud providers) exactly, or a saved key silently
    # never matches what the engine-spawn code looks up.
    "openrouter_api_key": "OPENROUTER_API_KEY",
    "ollama_api_key": "OLLAMA_API_KEY",
}

# Reverse lookup for resolve_by_env_var — only the canonical (dedicated)
# name round-trips; a general key reached only via _PARENT_KEY fallback
# has no single canonical env var of its own to be looked up BY.
_ENV_VAR_TO_KEY: dict[str, str] = {v: k for k, v in CANONICAL_ENV_VAR.items()}

# Checked ONLY after every dedicated/canonical name comes up empty. Never
# written to by anything post-consolidation — kept so a pre-existing
# install's env/file keeps working without a forced migration step.
# Aliases are attached to the *general* key only — a legacy `OPENAI_APIKEY`
# was always meant as "some OpenAI key", never a TTS/STT-specific spelling.
_LEGACY_ALIASES: dict[str, list[str]] = {
    "anthropic_api_key": ["ANTHROPIC_APIKEY"],
    "openai_api_key": ["OPENAI_APIKEY"],
}

# A logical key with no value of its own falls back to a more general key —
# e.g. no CORVIN_TTS_OPENAI_KEY configured just means "use whatever OpenAI
# key is generally configured." The parent's own candidates (including its
# legacy aliases) are appended, so the specificity order is preserved:
# dedicated name, then every general-key candidate in its own priority order.
_PARENT_KEY: dict[str, str] = {
    "tts_openai_api_key": "openai_api_key",
    "stt_openai_api_key": "openai_api_key",
}


def custom_env_var(slug: str) -> str:
    return f"CORVIN_CUSTOM_{slug.upper()}"


def _candidates_for(key_name: str) -> list[str] | None:
    if key_name.startswith("custom_"):
        return [custom_env_var(key_name[len("custom_"):])]
    env_var = CANONICAL_ENV_VAR.get(key_name)
    if env_var is None:
        return None
    chain = [env_var, *_LEGACY_ALIASES.get(key_name, [])]
    parent = _PARENT_KEY.get(key_name)
    if parent:
        parent_chain = _candidates_for(parent) or []
        chain.extend(parent_chain)
    return chain


def _clean_env_value(value: str) -> str:
    """Normalise a dotenv value: strip a trailing ` # comment`, then
    surrounding whitespace and matching quotes."""
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    value = value.split(" #", 1)[0].split("\t#", 1)[0].strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    return value


def _load_from_file(env_var: str, path: Path) -> str | None:
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE_RE.match(line)
        if not m or m.group(1) != env_var:
            continue
        value = _clean_env_value(m.group(2))
        if value:
            return value
    return None


def service_env_path() -> Path:
    return voice_config_dir() / SERVICE_ENV_FILENAME


def resolve_by_env_var(env_var: str) -> str | None:
    """Resolve a value by the CANONICAL env-var name (e.g. "OPENROUTER_API_KEY")
    instead of the logical key name, going through the same multi-source
    precedence chain: process env → secrets.enc → service.env.

    For callers that only know a provider's declared ``credential_env`` (e.g.
    ADR-0181's engine_model_registry.yaml providers) and would otherwise be
    tempted to read ``os.environ`` directly — which misses a key an operator
    just saved through the console while the bridge daemon is still running,
    since only ``resolve_key``/this function re-read service.env live.

    Phase 1b: Encrypted secrets (secrets.enc) are now checked between process
    env and service.env, providing at-rest encryption for stored credentials.

    ``env_var`` need not be one of the small set of names registered in
    ``CANONICAL_ENV_VAR`` — a provider can declare any ``credential_env`` name
    in its own config. When it isn't registered there is no logical key to
    route through ``resolve_key``, so this falls back to the same
    env-then-secrets.enc-then-service.env chain for the literal name itself
    (adversarial review, 2026-07-14: an earlier version returned None here,
    silently losing key resolution for any provider outside the hardcoded set
    even though the env var was genuinely set)."""
    key_name = _ENV_VAR_TO_KEY.get(env_var)
    if key_name is not None:
        return resolve_key(key_name)

    # Standard precedence: process env → secrets.enc → service.env
    value = (os.environ.get(env_var) or "").strip()
    if value:
        return value

    # Check encrypted secrets store
    try:
        store = SecretsStore()
        secret = store.load_secret(env_var)
        if secret:
            return secret
    except Exception as e:
        # Secrets store not available or unreadable — continue to service.env
        pass

    return _load_from_file(env_var, service_env_path())


def resolve_key(key_name: str) -> str | None:
    """Resolve *key_name* (e.g. "openai_api_key", "stt_openai_api_key",
    "custom_stripe") through the single canonical precedence chain:
    process env → secrets.enc → service.env.

    Every candidate (dedicated name first, then general/legacy names) is checked
    against the process env before any is checked against secrets.enc or
    service.env — an explicit env-var override always beats anything in a file,
    regardless of how specific the file's key is.

    Phase 1b: Encrypted secrets (secrets.enc) are now checked between process
    env and service.env, providing at-rest encryption for stored credentials.

    Returns the plaintext value, or None if not configured anywhere.
    """
    candidates = _candidates_for(key_name)
    if candidates is None:
        return None

    # Check process environment first (highest priority)
    for name in candidates:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value

    # Check encrypted secrets store (Phase 1b)
    try:
        store = SecretsStore()
        for name in candidates:
            secret = store.load_secret(name)
            if secret:
                return secret
    except Exception:
        # Secrets store not available or unreadable — continue to service.env
        pass

    # Check service.env file (lowest priority)
    service_env = service_env_path()
    for name in candidates:
        value = _load_from_file(name, service_env)
        if value:
            return value

    return None


def key_present(key_name: str) -> bool:
    return resolve_key(key_name) is not None


def write_key(key_name: str, value: str, *, path_override: Path | None = None) -> None:
    """Write *value* into service.env under the canonical env-var name for
    *key_name* — the ONE place BYOK / installer / console settings all
    write to, so a key can never end up in a store nothing reads back.

    *path_override* lets callers (tests, explicit-vault_dir-style overrides)
    target an isolated file instead of the real
    ~/.config/corvin-voice/service.env — mirrors the vault_dir parameter
    operator/agent/byok.py's vault-write path already has, for the same
    reason: a live-service-mutating test run is a real incident class, not
    a hypothetical (path-audit 2026-07-06, WA-22)."""
    candidates = _candidates_for(key_name)
    if candidates is None:
        raise ValueError(f"unknown key_name: {key_name!r}")
    env_var = candidates[0]

    path = path_override if path_override is not None else service_env_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    if path.exists():
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []

    replaced = False
    out: list[str] = []
    for raw in lines:
        m = _ENV_LINE_RE.match(raw.strip())
        if m and m.group(1) == env_var:
            out.append(f"{env_var}={value}")
            replaced = True
        else:
            out.append(raw)
    if not replaced:
        out.append(f"{env_var}={value}")

    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(out) + "\n", encoding="utf-8")
    try:
        tmp.chmod(0o600)
    except OSError:
        pass
    tmp.replace(path)
    try:
        path.chmod(0o600)
    except OSError:
        pass


# ── Phase 1b: Encrypted secrets management ────────────────────────────────────
#
# SecretsStore provides tenant-scoped encryption of provider keys using Fernet.
# Secrets are stored in ~/.corvin/tenants/<tenant_id>/global/secrets.enc
# and can be accessed via load_secret/save_secret or through resolve_by_env_var.
#
# Master encryption key is stored in ~/.corvin/tenants/<tenant_id>/keys/tenant_master.key
# with 0o600 permissions.

import base64
import json
import logging
from datetime import datetime

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError:
    Fernet = None  # type: ignore
    InvalidToken = None  # type: ignore

_log = logging.getLogger(__name__)


class SecretsStore:
    """Manage encrypted secrets in tenant/global/secrets.enc.

    Provides single-tenant storage for API keys and other sensitive data
    using Fernet (AES-128-CBC) encryption with a per-tenant master key.

    Secrets are accessed via load_secret/save_secret or integrated into
    the provider_keys resolver chain (resolve_by_env_var precedence:
    env var → secrets.enc → service.env).
    """

    def __init__(self, tenant_id: str | None = None):
        """Initialize SecretsStore for a tenant.

        Args:
            tenant_id: Tenant identifier (default: "_default"). Must pass
                       validation in forge.paths._validate_tenant_id.
        """
        if Fernet is None:
            raise RuntimeError(
                "cryptography library required for SecretsStore — "
                "install with: uv pip install 'cryptography>=42'"
            )

        try:
            from forge.paths import tenant_home, _resolve_tenant_id
        except ImportError:
            # Fallback for portable use (e.g., in standalone scripts)
            from pathlib import Path
            _resolve_tenant_id = lambda tid: tid or "_default"  # noqa: E731
            def tenant_home(tid=None):
                base = Path.home() / ".corvin"
                return base / "tenants" / _resolve_tenant_id(tid)

        self.tenant_id = _resolve_tenant_id(tenant_id)
        self.tenant_base = tenant_home(self.tenant_id)
        self.secrets_path = self.tenant_base / "global" / "secrets.enc"
        self.keys_dir = self.tenant_base / "keys"
        self.master_key_path = self.keys_dir / "tenant_master.key"

    def _ensure_master_key(self) -> bytes:
        """Get or create tenant master key.

        Returns the 32-byte Fernet key. On first call, generates a new key
        and writes it to tenant_master.key (mode 0o600, readable by owner only).

        Raises RuntimeError if key creation fails.
        """
        self.keys_dir.mkdir(parents=True, exist_ok=True)

        if self.master_key_path.exists():
            return self.master_key_path.read_bytes()

        # Generate new master key
        key = Fernet.generate_key()
        try:
            self.master_key_path.write_bytes(key)
        except OSError as e:
            raise RuntimeError(
                f"failed to write master key to {self.master_key_path}: {e}"
            )
        # Restrict permissions (Unix-only; chmod on Windows is a no-op or error → ignore)
        try:
            self.master_key_path.chmod(0o600)
        except OSError:
            pass  # Windows or filesystem that doesn't support chmod; continue

        _log.info(f"SecretsStore: generated new master key for tenant {self.tenant_id}")
        return key

    def encrypt_secrets(self, secrets_dict: dict) -> dict:
        """Encrypt secrets dict and write to secrets.enc.

        Args:
            secrets_dict: Dictionary of {key: value} pairs to encrypt.

        Returns:
            Envelope dict with version, timestamp, algorithm, and encrypted payload.

        Raises:
            ValueError: If encryption fails.
        """
        try:
            master_key = self._ensure_master_key()
            cipher = Fernet(master_key)

            # Serialize and encrypt
            plaintext = json.dumps(secrets_dict).encode("utf-8")
            ciphertext = cipher.encrypt(plaintext)

            # Wrap in versioned envelope for future key rotation
            envelope = {
                "version": "1.0",
                "encrypted_at": datetime.utcnow().isoformat() + "Z",
                "algorithm": "AES-128-CBC (Fernet)",
                "key_id": f"tenant_master_{self.tenant_id}",
                "payload": base64.b64encode(ciphertext).decode("ascii"),
            }

            # Write with secure permissions
            self.secrets_path.parent.mkdir(parents=True, exist_ok=True)
            self.secrets_path.write_text(json.dumps(envelope, indent=2), encoding="utf-8")
            try:
                self.secrets_path.chmod(0o600)
            except OSError:
                pass  # Permission change failed, but write succeeded

            _log.debug(
                f"SecretsStore: encrypted {len(secrets_dict)} secrets "
                f"to {self.secrets_path}"
            )
            return envelope
        except Exception as e:
            if isinstance(e, ValueError):
                raise
            raise ValueError(f"encryption failed: {e}")

    def decrypt_secrets(self) -> dict:
        """Decrypt and load secrets from secrets.enc.

        Returns an empty dict if secrets.enc does not exist.

        Returns:
            Dictionary of {key: value} pairs from the encrypted store.

        Raises:
            ValueError: If the key doesn't match or file is corrupted.
        """
        if not self.secrets_path.exists():
            return {}

        try:
            master_key = self._ensure_master_key()
            cipher = Fernet(master_key)

            # Load envelope
            envelope = json.loads(self.secrets_path.read_text(encoding="utf-8"))

            # Decrypt
            ciphertext = base64.b64decode(envelope["payload"])
            plaintext = cipher.decrypt(ciphertext)
            secrets = json.loads(plaintext)

            _log.debug(
                f"SecretsStore: decrypted {len(secrets)} secrets "
                f"from {self.secrets_path}"
            )
            return secrets
        except InvalidToken:
            raise ValueError(
                f"failed to decrypt secrets — key mismatch or corrupted file: "
                f"{self.secrets_path}"
            )
        except json.JSONDecodeError as e:
            raise ValueError(f"invalid secrets.enc format: {e}")
        except Exception as e:
            raise ValueError(f"failed to load secrets: {e}")

    def load_secret(self, key: str, default: str | None = None) -> str | None:
        """Load a single secret value by key.

        Args:
            key: Secret key name (e.g., "ANTHROPIC_API_KEY").
            default: Returned if key is not found or decryption fails.

        Returns:
            The decrypted secret value, or default if not found/error.
        """
        try:
            secrets = self.decrypt_secrets()
            return secrets.get(key, default)
        except Exception as e:
            _log.debug(f"SecretsStore: could not load secret {key}: {e}")
            return default

    def save_secret(self, key: str, value: str) -> None:
        """Save or update a single secret value.

        Args:
            key: Secret key name (e.g., "ANTHROPIC_API_KEY").
            value: Plaintext value to encrypt and store.

        Raises:
            ValueError: If encryption or write fails.
        """
        try:
            secrets = self.decrypt_secrets()
            secrets[key] = value
            self.encrypt_secrets(secrets)
            _log.debug(f"SecretsStore: saved secret {key} to tenant {self.tenant_id}")
        except Exception as e:
            _log.error(f"SecretsStore: failed to save secret {key}: {e}")
            raise

    def delete_secret(self, key: str) -> bool:
        """Delete a secret by key.

        Args:
            key: Secret key name to delete.

        Returns:
            True if the key was found and deleted, False if key did not exist.

        Raises:
            ValueError: If decryption or write fails.
        """
        try:
            secrets = self.decrypt_secrets()
            if key not in secrets:
                return False
            del secrets[key]
            if secrets:
                self.encrypt_secrets(secrets)
            else:
                # Delete the file if no secrets remain
                self.secrets_path.unlink(missing_ok=True)
            _log.debug(f"SecretsStore: deleted secret {key}")
            return True
        except Exception as e:
            _log.error(f"SecretsStore: failed to delete secret {key}: {e}")
            raise

    def migrate_from_env(self, env_file: Path | None = None) -> dict:
        """Migrate secrets from ~/.corvin/.env to tenant secrets.enc.

        Reads KEY=VALUE pairs from the legacy .env file and encrypts them
        into the tenant's secrets.enc. The original .env file is moved to
        .env.backup.

        Args:
            env_file: Path to .env file (default: ~/.corvin/.env).

        Returns:
            Dictionary of secrets that were migrated (may be empty if file
            doesn't exist or is empty).

        Raises:
            ValueError: If encryption or backup fails.
        """
        if env_file is None:
            env_file = Path.home() / ".corvin" / ".env"

        if not env_file.exists():
            _log.debug(f"SecretsStore: no .env file found at {env_file}")
            return {}

        # Parse .env
        secrets = {}
        try:
            content = env_file.read_text(encoding="utf-8", errors="replace")
            for line in content.split("\n"):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip()
                # Strip quotes if present
                if value and value[0] in ('"', "'") and value[0] == value[-1]:
                    value = value[1:-1]
                if key:
                    secrets[key] = value
        except OSError as e:
            raise ValueError(f"failed to read .env file: {e}")

        if not secrets:
            _log.debug(f"SecretsStore: no secrets found in {env_file}")
            return {}

        # Encrypt and save
        self.encrypt_secrets(secrets)

        # Backup old file
        backup = env_file.with_suffix(".env.backup")
        try:
            env_file.replace(backup)
        except OSError as e:
            raise ValueError(f"failed to backup .env to {backup}: {e}")

        _log.info(
            f"SecretsStore: migrated {len(secrets)} secrets from .env to "
            f"secrets.enc (backup: {backup})"
        )

        return secrets

    def list_secrets(self) -> list[str]:
        """List all secret keys (not values) in the store.

        Returns:
            List of secret key names, or empty list if store is empty or unreadable.
        """
        try:
            secrets = self.decrypt_secrets()
            return sorted(secrets.keys())
        except Exception as e:
            _log.debug(f"SecretsStore: could not list secrets: {e}")
            return []
