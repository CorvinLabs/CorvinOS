"""vault.py — secrets vault (Tier 3 of the memory layer).

Stores credentials, API keys, addresses, and any other piece of data that
should NEVER end up inline in a system prompt. The bridge only injects an
**inventory** into every prompt — names + kinds + tags, no values. Claude
explicitly calls a tool (the vault CLI / dispatcher path) to fetch a
specific item. Each fetch is logged with a chat-id + timestamp for audit.

Layout:

    ~/.config/corvin-voice/vault/
    ├── INDEX.json              ← list of items + per-item policy
    ├── visa_main.json          ← `auto_unlock: true`  → fetched silently
    ├── home_address.json
    └── aws_dev.json.gpg        ← `encrypted: true`    → gpg-decrypted on read

Policy per item:
  - `auto_unlock: true`  — Claude may fetch without an extra round trip.
                           Default for low-risk items (postal addresses).
  - `auto_unlock: false` — Claude must ask the user `/vault unlock <name>`
                           in chat. Unlocks last 5 minutes per item.

Encryption is OPTIONAL. By default items are plain JSON with chmod 600.
With `encrypted: true` and a working `gpg-agent`, items are stored as
`<name>.json.gpg` and decrypted on read via `gpg --decrypt`. Never hold
plaintext on disk in the encrypted path; the unlock state is purely
in-memory (session-scoped).

This module is self-contained — no dependencies beyond stdlib + gpg
binary if encryption is opted-in.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

# corvinOS path resolver — prefer the package import; fall back to a
# sys.path-injection when this module is loaded as a top-level module
# (every test_*.py does that via `sys.path.insert(0, HERE)`).
try:
    from .paths import voice_dir  # type: ignore
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from paths import voice_dir  # type: ignore


def _vault_root() -> Path:
    """Canonical BYOK-vault config root: ``<XDG_CONFIG_HOME or ~/.config>/corvin-voice``.

    XDG Base Directory spec: default to ``$HOME/.config`` when ``XDG_CONFIG_HOME``
    is unset — NOT ``voice_dir()``. vault.py is imported by BOTH the console/byok
    route (XDG set → ~/.config) AND the systemd adapter (XDG unset → previously
    voice_dir()/tenant-home), so a BYOK key stored via one was invisible to the
    other (same reader!=writer split as the voice profile). CLAUDE.md pins
    ~/.config/corvin-voice/ as the canonical voice/secret config root.

    VOICE_CONFIG_DIR (highest priority) mirrors forge.paths.voice_config_dir /
    say.py's SSOT — without it, a launcher pinning the dir moved config/models
    but left the vault behind (reader!=writer split, V-2 audit finding)."""
    override = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if override:
        return Path(os.path.expanduser(os.path.expandvars(override)))
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip() or os.path.join(
        os.path.expanduser("~"), ".config"
    )
    return Path(os.path.expanduser(xdg)) / "corvin-voice"


def _vault_cache_root() -> Path:
    """Resolve the vault unlock-state root (volatile / runtime data)."""
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime:
        return Path(runtime) / "corvin-voice"
    return voice_dir() / "cache"


VAULT_DIR = _vault_root() / "vault"
INDEX_FILE = VAULT_DIR / "INDEX.json"
LOG_FILE = _vault_root() / "vault.log"
UNLOCK_FILE = _vault_cache_root() / "vault-unlocks.json"

# Item names: lowercase, [a-z0-9_-], 1-40 chars, no path traversal.
_VALID_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]{0,40}$")

# Default unlock window in seconds.
UNLOCK_TTL = 300  # 5 minutes

# GPG recipient — defaults to the gpg-agent's default key. Override with the
# `VAULT_GPG_RECIPIENT` env var for a specific key id.
GPG_RECIPIENT_ENV = "VAULT_GPG_RECIPIENT"


# ─── helpers ───────────────────────────────────────────────────────────────

def _ensure_dirs() -> None:
    VAULT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        VAULT_DIR.chmod(0o700)
    except OSError:
        pass
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    UNLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)


def _normalise_name(name: str) -> str:
    if not name:
        raise ValueError("empty vault item name")
    n = name.strip().lower()
    if not _VALID_NAME.match(n):
        raise ValueError(
            f"invalid vault name: {name!r} (use letters, digits, _ and -; up to 40 chars)"
        )
    return n


def _item_path(name: str, *, encrypted: bool) -> Path:
    suffix = ".json.gpg" if encrypted else ".json"
    return VAULT_DIR / f"{_normalise_name(name)}{suffix}"


def _has_gpg() -> bool:
    return shutil.which("gpg") is not None


# ─── log ───────────────────────────────────────────────────────────────────

def audit(event: str, name: str, *, source: str = "?", ok: bool = True,
          extra: str = "") -> None:
    """Append a single line to the local vault log AND mirror the access onto
    the unified L16 hash chain (the one ``voice-audit verify`` covers), so
    secret access is tamper-evident. Metadata ONLY: the secret KEY name +
    event + source + ok — NEVER the secret value. Never raises."""
    try:
        _ensure_dirs()
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        status = "ok" if ok else "FAIL"
        line = f"[{ts}] {status} {event} name={name} from={source}"
        if extra:
            line += f" {extra}"
        with LOG_FILE.open("a") as f:
            f.write(line + "\n")
        try:
            LOG_FILE.chmod(0o600)
        except OSError:
            pass
    except OSError:
        pass
    # Audit gap closure (G/D): mirror onto the unified hash chain. Key NAMES
    # only — the secret value is never passed here and must never be.
    try:
        from forge.security_events import write_event as _swe  # type: ignore
        from forge.paths import corvin_home as _ch  # type: ignore
        _swe(
            _ch() / "global" / "forge" / "audit.jsonl",
            f"vault.{event}",
            severity="INFO" if ok else "WARNING",
            tool="vault", run_id="-",
            details={"name": name, "source": source, "ok": bool(ok)},
        )
    except Exception:  # noqa: BLE001 — unified-chain mirror is best-effort
        pass


def read_audit(n: int = 20) -> list[str]:
    if not LOG_FILE.exists():
        return []
    try:
        lines = LOG_FILE.read_text().splitlines()
    except OSError:
        return []
    return lines[-n:]


# ─── index ─────────────────────────────────────────────────────────────────

def _load_index() -> list[dict[str, Any]]:
    try:
        return json.loads(INDEX_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_index(items: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    tmp = INDEX_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, indent=2, ensure_ascii=False))
    shutil.move(str(tmp), str(INDEX_FILE))
    try:
        INDEX_FILE.chmod(0o600)
    except OSError:
        pass


def list_items() -> list[dict[str, Any]]:
    """Return the index entries (no values). Each: {name, kind, tags,
    encrypted, auto_unlock}."""
    return [
        {
            "name": it.get("name"),
            "kind": it.get("kind") or "secret",
            "tags": it.get("tags") or [],
            "encrypted": bool(it.get("encrypted")),
            "auto_unlock": bool(it.get("auto_unlock", True)),
        }
        for it in _load_index()
    ]


def _index_get(name: str) -> dict[str, Any] | None:
    n = _normalise_name(name)
    for it in _load_index():
        if it.get("name") == n:
            return it
    return None


# ─── unlocks ───────────────────────────────────────────────────────────────

def _load_unlocks() -> dict[str, float]:
    try:
        return json.loads(UNLOCK_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_unlocks(d: dict[str, float]) -> None:
    UNLOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    tmp = UNLOCK_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(d))
    shutil.move(str(tmp), str(UNLOCK_FILE))
    try:
        UNLOCK_FILE.chmod(0o600)
    except OSError:
        pass


def unlock(name: str, *, ttl: int = UNLOCK_TTL) -> float:
    """Open the item for `ttl` seconds. Returns the new expiry timestamp."""
    n = _normalise_name(name)
    if _index_get(n) is None:
        raise KeyError(f"no vault item named {n!r}")
    d = _load_unlocks()
    expiry = time.time() + ttl
    d[n] = expiry
    _save_unlocks(d)
    audit("unlock", n, ok=True, extra=f"ttl={ttl}s")
    return expiry


def is_unlocked(name: str) -> bool:
    n = _normalise_name(name)
    d = _load_unlocks()
    exp = d.get(n)
    if not exp:
        return False
    if exp < time.time():
        # Expired — opportunistically clean.
        del d[n]
        _save_unlocks(d)
        return False
    return True


# ─── set / get ─────────────────────────────────────────────────────────────

def set_item(name: str, value: Any, *,
             kind: str = "secret",
             tags: list[str] | None = None,
             encrypted: bool = False,
             auto_unlock: bool = True) -> dict[str, Any]:
    """Create or overwrite a vault item. `value` is JSON-serialisable.
    With encrypted=True and gpg available, the file is GPG-encrypted to
    the configured recipient (env VAULT_GPG_RECIPIENT) or the default key.
    """
    n = _normalise_name(name)
    _ensure_dirs()

    if encrypted and not _has_gpg():
        raise RuntimeError(
            "encrypted=True requested but `gpg` is not installed; "
            "remove the encryption flag or install gpg."
        )

    payload = json.dumps({"name": n, "kind": kind, "value": value},
                         ensure_ascii=False)
    p = _item_path(n, encrypted=encrypted)
    if encrypted:
        recipient = os.environ.get(GPG_RECIPIENT_ENV) or ""
        cmd = ["gpg", "--batch", "--yes", "--quiet"]
        if recipient:
            cmd += ["--recipient", recipient]
        else:
            cmd += ["--default-recipient-self"]
        cmd += ["--output", str(p), "--encrypt"]
        try:
            subprocess.run(cmd, input=payload.encode("utf-8"),
                           check=True, capture_output=True)
        except subprocess.CalledProcessError as e:
            audit("set", n, ok=False, extra=f"gpg-failed: {e.stderr[:80]!r}")
            raise RuntimeError(f"gpg encryption failed: {e.stderr.decode(errors='replace')[:200]}") from e
    else:
        # FND-20: create the temp file at 0600 FROM THE START (os.open O_EXCL)
        # so the plaintext secret never exists in a umask-default (0644) window
        # between write and the post-move chmod (which was also a swallowed race).
        tmp = p.with_suffix(p.suffix + ".tmp")
        try:
            os.unlink(str(tmp))  # clear any stale tmp so O_EXCL succeeds
        except OSError:
            pass
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(payload)
        except Exception:
            try:
                os.unlink(str(tmp))
            except OSError:
                pass
            raise
        shutil.move(str(tmp), str(p))  # same-fs rename preserves 0600
    # Belt-and-suspenders (covers a cross-fs gpg path / move-copy): re-assert
    # 0600 and do NOT silently swallow failure for a secret file.
    try:
        p.chmod(0o600)
    except OSError as _ce:
        audit("set", n, ok=False, extra=f"chmod-failed:{type(_ce).__name__}")
        raise

    # Update index.
    items = _load_index()
    items = [it for it in items if it.get("name") != n]
    items.append({
        "name": n,
        "kind": kind,
        "tags": list(tags or []),
        "encrypted": encrypted,
        "auto_unlock": auto_unlock,
    })
    items.sort(key=lambda it: it.get("name") or "")
    _save_index(items)

    # Auto-unlocked items don't need an explicit unlock state.
    if not auto_unlock:
        d = _load_unlocks()
        d.pop(n, None)
        _save_unlocks(d)

    audit("set", n, ok=True, extra=f"encrypted={encrypted} auto_unlock={auto_unlock}")
    return {"name": n, "kind": kind, "encrypted": encrypted,
            "auto_unlock": auto_unlock}


def get_item(name: str, *, source: str = "?") -> Any:
    """Return the stored value. Raises:
      - KeyError on unknown name
      - PermissionError if the item is `auto_unlock=False` and not unlocked
      - RuntimeError on gpg failure for encrypted items
    """
    n = _normalise_name(name)
    meta = _index_get(n)
    if meta is None:
        audit("get", n, source=source, ok=False, extra="missing")
        raise KeyError(f"no vault item named {n!r}")
    if not meta.get("auto_unlock", True) and not is_unlocked(n):
        audit("get", n, source=source, ok=False, extra="locked")
        raise PermissionError(
            f"item {n!r} is locked. The user must send `/vault unlock {n}` "
            f"in the chat (unlocks for {UNLOCK_TTL // 60} minutes)."
        )
    p = _item_path(n, encrypted=bool(meta.get("encrypted")))
    if meta.get("encrypted"):
        if not _has_gpg():
            audit("get", n, source=source, ok=False, extra="gpg-missing")
            raise RuntimeError("gpg binary missing; cannot decrypt item")
        try:
            r = subprocess.run(
                ["gpg", "--batch", "--quiet", "--decrypt", str(p)],
                check=True, capture_output=True,
            )
            payload = r.stdout.decode("utf-8")
        except subprocess.CalledProcessError as e:
            audit("get", n, source=source, ok=False, extra="gpg-decrypt-failed")
            raise RuntimeError(
                f"gpg decryption failed: {e.stderr.decode(errors='replace')[:200]}"
            ) from e
    else:
        try:
            # SECURITY: a plaintext secret file that drifted to group/other
            # access (umask, restore-from-backup, editor rewrite) must NOT be
            # silently served — fail-closed like the forge vault's _check_mode,
            # never return a world-readable cleartext credential.
            _mode = p.stat().st_mode & 0o777
            if _mode & 0o077:
                audit("get", n, source=source, ok=False, extra="insecure-mode")
                raise PermissionError(
                    f"vault item {n!r} file mode {oct(_mode)} is not 0600 "
                    f"(group/other access) — refusing to read plaintext secret"
                )
            payload = p.read_text()
        except FileNotFoundError:
            audit("get", n, source=source, ok=False, extra="file-missing")
            raise KeyError(f"vault item {n!r} index entry exists but file is gone")
    audit("get", n, source=source, ok=True)
    return json.loads(payload).get("value")


def forget_item(name: str) -> bool:
    n = _normalise_name(name)
    meta = _index_get(n)
    if meta is None:
        return False
    p = _item_path(n, encrypted=bool(meta.get("encrypted")))
    try:
        p.unlink()
    except FileNotFoundError:
        pass
    items = [it for it in _load_index() if it.get("name") != n]
    _save_index(items)
    d = _load_unlocks()
    d.pop(n, None)
    _save_unlocks(d)
    audit("forget", n, ok=True)
    return True


# ─── system-prompt formatting ──────────────────────────────────────────────

def for_system_prompt() -> str:
    """Render the vault inventory (NEVER values). Empty string when no
    items exist."""
    items = list_items()
    if not items:
        return ""
    lines = []
    for it in items:
        flags = []
        if it["encrypted"]:
            flags.append("encrypted")
        if not it["auto_unlock"]:
            flags.append("locked")
        flag_str = f" [{', '.join(flags)}]" if flags else ""
        tag_str = f"  tags: {', '.join(it['tags'])}" if it["tags"] else ""
        lines.append(f"  - `{it['name']}` ({it['kind']}){flag_str}{tag_str}")
    return (
        "\n\nVault inventory (Tier 3 secrets — call vault_get to fetch a value, "
        "NEVER inline a value in a reply):\n"
        "  Use the vault_cli.py / `/vault` commands to read items. Items marked\n"
        "  [locked] need an explicit `/vault unlock <name>` from the user first.\n"
        + "\n".join(lines)
    )
