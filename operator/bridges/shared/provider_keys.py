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
    instead of the logical key name, going through the same
    env-then-service.env precedence chain as ``resolve_key``.

    For callers that only know a provider's declared ``credential_env`` (e.g.
    ADR-0181's engine_model_registry.yaml providers) and would otherwise be
    tempted to read ``os.environ`` directly — which misses a key an operator
    just saved through the console while the bridge daemon is still running,
    since only ``resolve_key``/this function re-read service.env live."""
    key_name = _ENV_VAR_TO_KEY.get(env_var)
    if key_name is None:
        return None
    return resolve_key(key_name)


def resolve_key(key_name: str) -> str | None:
    """Resolve *key_name* (e.g. "openai_api_key", "stt_openai_api_key",
    "custom_stripe") through the single canonical precedence chain: every
    candidate (dedicated name first, then general/legacy names) is checked
    against the process env before any is checked against service.env —
    an explicit env-var override always beats anything in a file,
    regardless of how specific the file's key is. Returns the plaintext
    value, or None if not configured anywhere."""
    candidates = _candidates_for(key_name)
    if candidates is None:
        return None

    for name in candidates:
        value = (os.environ.get(name) or "").strip()
        if value:
            return value

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
