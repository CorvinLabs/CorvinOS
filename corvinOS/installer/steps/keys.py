"""API key management: read, validate, and persist keys to service.env."""
from __future__ import annotations

import getpass
import os
import re
import subprocess
import sys
from pathlib import Path

from corvinOS.shared.paths import voice_config_dir

# SSOT: resolve through voice_config_dir() so the installer writes keys to the
# exact dir the console engines page + bridge_manager + voice STT/TTS scripts
# read from (VOICE_CONFIG_DIR → XDG_CONFIG_HOME → ~/.config). A prior hardcoded
# Path.home()/".config" diverged from the console's APPDATA/XDG resolver on
# Windows and under a custom XDG_CONFIG_HOME (path-audit 2026-07-06).
ENV_FILE = voice_config_dir() / "service.env"


# ── Public API ─────────────────────────────────────────────────────────────

def load_existing_keys() -> dict[str, str]:
    """Read current keys from service.env. Only returns non-empty ASCII values."""
    result: dict[str, str] = {}
    if not ENV_FILE.is_file() or ENV_FILE.stat().st_size == 0:
        return result
    for line in ENV_FILE.read_text().splitlines():
        if "=" not in line or line.startswith("#"):
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip()
        # Reject non-ASCII (corrupted) values
        if val and _is_printable_ascii(val):
            result[key] = val
    return result


def prompt_openai_key(existing: str = "", interactive: bool = True) -> str:
    """Ask for the OpenAI API key with live validation. Returns the key or ''."""
    if existing and existing.startswith("sk-"):
        masked = existing[:7] + "…" + existing[-4:]
        print(f"✓ OPENAI_API_KEY already set ({masked}) — keeping")
        return existing

    if not interactive:
        print("  No OPENAI_API_KEY — TTS and Whisper voice notes will be disabled.")
        return ""

    print()
    print("  OpenAI API key — for TTS, Whisper, and voice notes. (optional)")
    print("  Get one at https://platform.openai.com/api-keys")
    print()

    while True:
        key = getpass.getpass("  OPENAI_API_KEY (ENTER to skip): ").strip()
        if not key:
            print("⚠ No OpenAI key — TTS/Whisper will be disabled.")
            return ""

        if not key.startswith("sk-"):
            print("  ⚠ OpenAI keys start with 'sk-'. Try again.")
            continue

        print("  Validating key against api.openai.com …")
        rc = _validate_openai_key(key)
        if rc == 0:
            print("✓ OpenAI key looks good.")
            return key
        if rc == 1:
            print("  ⚠ Key rejected (HTTP 401/403) — wrong key or revoked?")
            retry = input("  Try again? [Y/n]: ").strip().lower() or "y"
            if retry.startswith("n"):
                print("  Continuing with unvalidated key.")
                return key
        else:
            print("  ⚠ Cannot reach api.openai.com (offline/proxy). Accepting as-is.")
            return key


def prompt_anthropic_key(existing: str = "", interactive: bool = True) -> str:
    """Ask for the Anthropic API key. Optional — falls back to claude CLI."""
    if existing:
        masked = existing[:7] + "…" + existing[-4:]
        print(f"✓ ANTHROPIC_API_KEY already set ({masked}) — keeping")
        return existing

    if not interactive:
        return ""

    print()
    print("  Anthropic API key — optional, used by the long-reply summarizer.")
    print("  Without it the summarizer uses the 'claude' CLI, which works fine.")
    key = getpass.getpass("  ANTHROPIC_API_KEY (ENTER to skip): ").strip()
    return key


def save_keys(
    openai_key: str,
    anthropic_key: str,
    extra: dict[str, str] | None = None,
    repo_root: Path | None = None,
) -> None:
    """Write keys to service.env (mode 0600 from the moment of creation —
    never transiently world/group-readable). Optionally also to repo .env."""
    ENV_FILE.parent.mkdir(parents=True, exist_ok=True)
    _ensure_restrictive_mode(ENV_FILE)
    _upsert(ENV_FILE, "OPENAI_API_KEY", openai_key)
    if anthropic_key:
        _upsert(ENV_FILE, "ANTHROPIC_API_KEY", anthropic_key)
    if extra:
        for k, v in extra.items():
            if v:
                _upsert(ENV_FILE, k, v)
    ENV_FILE.chmod(0o600)
    print(f"✓ Keys saved to {ENV_FILE}")

    # Also write to repo .env (optional, for local dev)
    if repo_root is not None:
        try:
            import shutil
            repo_env = repo_root / ".env"
            example = repo_root / ".env.example"
            if not repo_env.exists() and example.exists():
                shutil.copy(example, repo_env)
            if repo_env.exists():
                _ensure_restrictive_mode(repo_env)
                _upsert(repo_env, "OPENAI_API_KEY", openai_key)
                if anthropic_key:
                    _upsert(repo_env, "ANTHROPIC_API_KEY", anthropic_key)
                repo_env.chmod(0o600)
                print(f"✓ Keys also saved to {repo_env}")
        except Exception:
            pass  # repo .env is optional


# ── internals ──────────────────────────────────────────────────────────────

def _validate_openai_key(key: str) -> int:
    """Return 0=ok, 1=rejected, 2=network error."""
    try:
        result = subprocess.run(
            [
                # os.devnull → NUL on Windows, /dev/null on POSIX. A hardcoded
                # /dev/null made curl fail to open the output path on Windows, so
                # _validate_openai_key always returned 2 (network error) and a
                # wrong key was silently "accepted as-is" (path-audit 2026-07-06 F8).
                "curl", "-sSo", os.devnull, "-w", "%{http_code}",
                "--max-time", "8",
                "-H", f"Authorization: Bearer {key}",
                "https://api.openai.com/v1/models",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        http = result.stdout.strip()
        if http == "200":
            return 0
        if http in ("401", "403"):
            return 1
        return 2
    except Exception:
        return 2


def _is_printable_ascii(s: str) -> bool:
    return bool(re.match(r"^[\x20-\x7E]+$", s))


def _ensure_restrictive_mode(path: Path) -> None:
    """Create `path` at 0600 if absent, or narrow an existing file's mode to
    0600, BEFORE any secret content is written into it.

    Previously the file was created via a bare `path.touch()` (inheriting
    the process's default umask — often world/group-readable) and only
    chmod'd 0600 AFTER the full multi-key write sequence completed, leaving
    plaintext API keys on disk at a permissive mode for the entire prompt/
    validate/write duration (adversarial review finding).
    """
    if sys.platform == "win32":
        # No POSIX mode bits on NTFS — but the file must still EXIST after
        # this call: _upsert() reads it unconditionally next, and the old
        # early-return crashed a fresh Windows install with FileNotFoundError
        # on the very first key write (adversarial review finding).
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        return
    if not path.exists():
        fd = os.open(str(path), os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
    else:
        path.chmod(0o600)


def _upsert(path: Path, key: str, value: str) -> None:
    """Set or replace KEY=VALUE in an env file, atomically."""
    _ensure_restrictive_mode(path)
    existing = [l for l in path.read_text().splitlines() if not l.startswith(f"{key}=")]
    existing.append(f"{key}={value}")
    path.write_text("\n".join(existing) + "\n")
