"""engine_switch.py — Layer 29 companion: per-chat delegation engine preference.

Lets the owner of a chat switch which worker engine the orchestrator
persona (Layer 29) prefers when it delegates work via the
``mcp__corvin_delegate__delegate_*`` tools. The Bridge OS-Schicht
(Claude Code) keeps EVERY comfort feature (/btw, skill-inject,
forge-MCP, recall, …). Only the worker the OS delegates TO changes.

Slash-command flow (in_chat_commands.js, owner-only):

* ``/engine``                  — show current preference + supported engines
* ``/engine claude``           — pin claude_code as worker default
* ``/engine codex``            — pin codex_cli
* ``/engine opencode``         — pin opencode (default model: ollama/qwen3:8b)
* ``/engine cloud``            — pin opencode + ollama-cloud/qwen3-coder-next
* ``/engine hermes``           — pin hermes (fully-local via Ollama, zero egress, CONFIDENTIAL-capable)
* ``/engine hermes-fast``      — pin hermes + hermes-fast model alias (qwen3:1.7b)
* ``/engine off``              — clear the override; orchestrator decides freely

Storage
-------
Single JSON file per (channel, chat) at::

    <corvin_home>/global/engine_pref/<safe_channel>__<safe_chat>.json

Shape::

    {
      "engine":  "opencode",
      "model":   "ollama-cloud/qwen3-coder-next",
      "set_at":  1778204770.0,
      "set_by_uid": "<platform-uid>",
      "channel": "discord"
    }

Pattern mirrors ``consent.py`` / ``disclosure.py``: single JSON file per
(channel, chat), atomic-replace via ``.tmp`` + ``.lock``, mtime
hot-reload via fresh read per call (no in-process cache — preference
flips are rare enough that the syscall cost is negligible).

Audit
-----
Every set / clear emits ``engine.pref_switched`` into the unified
hash chain at ``<corvin_home>/global/forge/audit.jsonl`` — same
chain forge / skill-forge / path-gate / consent / roles use. One
``voice-audit verify`` covers all of it.

Allow-list (mirrors L23 / L25 / L28 / L29 metadata-only contract):
``channel``, ``chat_key``, ``uid``, ``action`` (``set``/``cleared``),
``engine``, ``model``. NEVER the prompt, the worker output, any
delegation result, or user-typed free text.

Wiring
------
The adapter's ``_build_spawn_env`` reads ``current(...)`` per inbox
message and, when set, injects two env-vars into the OS-turn
subprocess: ``CORVIN_DELEGATE_PREF_ENGINE`` and (optionally)
``CORVIN_DELEGATE_PREF_MODEL``. The orchestrator persona's brief
(``operator/cowork/personas/orchestrator.json::append_system``)
tells the OS-turn to honour these when picking which
``mcp__corvin_delegate__delegate_*`` tool to call.

Worker engines themselves stay unchanged — they're called with the
operator's tool-arg model, just as before. The switch only routes
WHICH engine the OS delegates to.

Cost contract
-------------
This module is pure Python + stdlib. NO ``import anthropic``, NO
subprocess spawn. CI lint
(``test_engine_switch.py::test_no_anthropic_sdk_import``) walks the
AST and rejects forbidden imports.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import re
import time
from pathlib import Path
from typing import Any, Callable

# ── Engine catalog ────────────────────────────────────────────────────
#
# The mapping is curated: each KEY is a slash-command alias the user
# types, each VALUE is the engine_id (matching the Layer-22
# WorkerEngine.name) plus an optional default model when the user
# didn't pin one explicitly.
#
# Adding a new engine: append an entry here AND register the
# corresponding ``mcp__corvin_delegate__delegate_<engine>`` tool
# under ``core/delegate/corvin_delegate/mcp_server.py``.
# Aliases (multiple keys mapping to the same engine_id) are fine —
# the lookup is case-insensitive.

ENGINE_ALIASES: dict[str, dict[str, str | None]] = {
    # canonical
    "claude":        {"engine": "claude_code", "model": None},
    "claude_code":   {"engine": "claude_code", "model": None},
    "claude-code":   {"engine": "claude_code", "model": None},
    # codex
    "codex":         {"engine": "codex_cli",   "model": None},
    "codex_cli":     {"engine": "codex_cli",   "model": None},
    "codex-cli":     {"engine": "codex_cli",   "model": None},
    # opencode (local Ollama default)
    "opencode":      {"engine": "opencode",    "model": "ollama/qwen3:8b"},
    "ollama":        {"engine": "opencode",    "model": "ollama/qwen3:8b"},
    "local":         {"engine": "opencode",    "model": "ollama/qwen3:8b"},
    # opencode (cloud-backed via ollama-cloud provider config)
    "cloud":         {"engine": "opencode",    "model": "ollama-cloud/qwen3-coder-next"},
    "ollama-cloud":  {"engine": "opencode",    "model": "ollama-cloud/qwen3-coder-next"},
    "opencode-cloud": {"engine": "opencode",   "model": "ollama-cloud/qwen3-coder-next"},
    # hermes — fully-local Ollama HTTP, zero egress, CONFIDENTIAL-capable (ADR-0066/0067)
    # /engine hermes → orchestrator delegates via delegate_hermes
    "hermes":           {"engine": "hermes",   "model": None},
    "hermes-fast":      {"engine": "hermes",   "model": "hermes-fast"},
    "hermes-balanced":  {"engine": "hermes",   "model": "hermes-balanced"},
    "hermes-capable":   {"engine": "hermes",   "model": "hermes-capable"},
    "hermes-large":     {"engine": "hermes",   "model": "hermes-large"},
    "local-hermes":     {"engine": "hermes",   "model": None},
    # copilot — GitHub Copilot CLI (ADR-0071). Zero incremental cost for
    # GitHub Copilot Business/Enterprise licensees. Requires `copilot` binary
    # and authentication via `copilot auth login` or GH_TOKEN env.
    # /engine copilot → orchestrator delegates via delegate_copilot
    "copilot":          {"engine": "copilot",  "model": None},
    "copilot-shell":    {"engine": "copilot",  "model": "shell"},
    "copilot-git":      {"engine": "copilot",  "model": "git"},
    "copilot-gh":       {"engine": "copilot",  "model": "gh"},
    "gh-copilot":       {"engine": "copilot",  "model": None},
}

VALID_ENGINES: tuple[str, ...] = ("claude_code", "codex_cli", "opencode", "hermes", "copilot")

# Engine-id charset: lowercase alnum + underscore. Mirror of the
# bundle ``ENGINE_ALIASES`` values; the validator rejects anything else
# so a malformed on-disk file can't smuggle an unknown engine into the
# OS-turn's env.
_ENGINE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,31}$")

# Model charset: anchor / no whitespace / no path-traversal / max 128
# chars. Matches what opencode / claude / codex accept as a model arg.
_MODEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,127}$")


# ── Path resolution (mirror of consent._corvin_home) ────────────────

def _corvin_home() -> Path:
    """Phase-1 strangler-fig — CORVIN_HOME canonical, CORVIN_HOME alias.
    On-disk ``.corvin`` preferred, ``.corvinOS`` legacy fallback.
    """
    env = os.environ.get("CORVIN_HOME") or os.environ.get("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            new = parent / ".corvin"
            legacy = parent / ".corvinOS"
            if new.is_dir():
                return new
            if legacy.is_dir():
                return legacy
            return new
    new_default = Path.home() / ".corvin"
    legacy_default = Path.home() / ".corvinOS"
    if not new_default.is_dir() and legacy_default.is_dir():
        return legacy_default
    return new_default


def _safe_component(s: str) -> str:
    """Mirror of adapter._safe_id — alnum-only, length-capped."""
    return "".join(ch if ch.isalnum() else "_" for ch in s)[:64] or "anon"


def _store_path(channel: str, chat_key: str, *,
                tenant_id: str | None = None) -> Path:
    safe_channel = _safe_component(channel or "unknown")
    safe_chat = _safe_component(str(chat_key) if chat_key is not None else "anon")
    home = _corvin_home()
    if tenant_id is None:
        base = home / "global" / "engine_pref"
    else:
        base = home / "tenants" / tenant_id / "global" / "engine_pref"
    return base / f"{safe_channel}__{safe_chat}.json"


def _audit_path(*, tenant_id: str | None = None) -> Path:
    home = _corvin_home()
    if tenant_id is None:
        return home / "global" / "forge" / "audit.jsonl"
    return home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"


# ── Audit (mirror of consent._audit) ─────────────────────────────────

# Per-event allow-list — anything else raises ValueError. Mirrors the
# Layer-29 metadata-only audit-allow-list pattern; the regression test
# walks the chain and fails if any key outside this set lands.
_AUDIT_ALLOWED: frozenset[str] = frozenset({
    "channel", "chat_key", "uid", "action", "engine", "model",
})


def _validate_audit_details(details: dict[str, Any]) -> None:
    for k in details:
        if k not in _AUDIT_ALLOWED:
            raise ValueError(
                f"engine_switch audit detail '{k}' not in allow-list "
                f"{sorted(_AUDIT_ALLOWED)}"
            )


def _audit(*, channel: str, chat_key: str, uid: str,
           action: str, engine: str | None, model: str | None) -> None:
    """Best-effort audit write — silent on failure.

    Mirrors the optional-import pattern in ``consent.py``: when forge
    isn't on the path (standalone tests), the call quietly no-ops; in
    the production bridge process forge is always reachable.
    """
    try:
        import sys
        here = Path(__file__).resolve()
        repo = None
        for parent in here.parents:
            if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
                repo = parent
                break
        if repo is not None:
            forge_pkg = repo / "operator" / "forge"
            if str(forge_pkg) not in sys.path:
                sys.path.insert(0, str(forge_pkg))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    body: dict[str, Any] = {
        "channel": channel,
        "chat_key": chat_key,
        "uid": uid,
        "action": action,
    }
    if engine is not None:
        body["engine"] = engine
    if model is not None:
        body["model"] = model
    _validate_audit_details(body)  # structural defence; raises on smuggled keys
    try:
        write_event(_audit_path(), "engine.pref_switched", details=body)
    except Exception:
        pass


# ── Store I/O ────────────────────────────────────────────────────────

def _load_store(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        return data if isinstance(data, dict) else None
    except (OSError, json.JSONDecodeError):
        return None


def _save_store(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(path.suffix + ".lock")
    fd = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp, path)
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


def _clear_store(path: Path) -> bool:
    """Best-effort delete. Returns True if a file was removed."""
    if not path.exists():
        return False
    try:
        path.unlink()
        return True
    except OSError:
        return False


# ── Alias resolution ─────────────────────────────────────────────────

def resolve_alias(token: str) -> dict[str, str | None] | None:
    """Turn a user-typed engine alias into ``{engine, model}`` or None.

    Returns None on unknown / empty input. Caller is expected to render
    a usage hint with the supported alias keys.
    """
    if not token:
        return None
    key = token.strip().lower()
    spec = ENGINE_ALIASES.get(key)
    if spec is None:
        return None
    # Return a shallow copy so the caller can mutate without poisoning
    # the bundle table.
    return {"engine": spec["engine"], "model": spec["model"]}


def supported_aliases() -> list[str]:
    """Sorted, de-duplicated list of canonical user-facing aliases."""
    # Curated short-list; the full ENGINE_ALIASES table has back-compat
    # spellings (claude_code / claude-code) the user shouldn't have to
    # type. The /engine help block shows these.
    return ["claude", "codex", "opencode", "cloud", "hermes", "hermes-fast"]


# ── Public API ───────────────────────────────────────────────────────

def current(channel: str, chat_key: str, *,
            tenant_id: str | None = None) -> dict[str, str] | None:
    """Read the current engine preference for ``(channel, chat_key)``.

    Returns ``{"engine": "<id>", "model": "<id>"|""}`` when a valid
    preference is on disk, ``None`` otherwise. A malformed on-disk file
    is treated as absent (returns None) — the operator's ``/engine``
    will overwrite it on the next set.

    Hot-reload: this function ALWAYS reads from disk; there is no
    in-process cache. A slash-command write becomes visible to the
    next inbox message without any restart.
    """
    path = _store_path(channel, chat_key, tenant_id=tenant_id)
    data = _load_store(path)
    if not data:
        return None
    engine = data.get("engine")
    model = data.get("model")
    if not isinstance(engine, str) or not _ENGINE_ID_RE.match(engine):
        return None
    if engine not in VALID_ENGINES:
        return None
    out: dict[str, str] = {"engine": engine}
    if isinstance(model, str) and model and _MODEL_RE.match(model):
        out["model"] = model
    else:
        out["model"] = ""
    return out


def set_preference(channel: str, chat_key: str, *,
                   engine: str, model: str | None = None,
                   uid: str = "",
                   tenant_id: str | None = None) -> dict[str, str]:
    """Persist a preference and audit. Returns the stored shape.

    Raises ``ValueError`` on invalid ``engine`` / ``model``. Mirror of
    the strict-validate pattern at consent.py — a typo at the slash-
    command edge surfaces immediately rather than silently writing a
    malformed file the next ``current()`` would just ignore.
    """
    if not isinstance(engine, str) or engine not in VALID_ENGINES:
        raise ValueError(
            f"engine={engine!r} not in {VALID_ENGINES}"
        )
    if model is not None:
        if not isinstance(model, str) or not _MODEL_RE.match(model):
            raise ValueError(f"model={model!r} fails shape check {_MODEL_RE.pattern}")
    path = _store_path(channel, chat_key, tenant_id=tenant_id)
    rec: dict[str, Any] = {
        "engine": engine,
        "model": model or "",
        "set_at": time.time(),
        "set_by_uid": str(uid or ""),
        "channel": channel or "",
    }
    _save_store(path, rec)
    _audit(channel=channel, chat_key=chat_key, uid=str(uid or ""),
           action="set", engine=engine, model=model)
    return {"engine": engine, "model": model or ""}


def clear_preference(channel: str, chat_key: str, *,
                     uid: str = "",
                     tenant_id: str | None = None) -> bool:
    """Drop any pinned preference. Returns True if a file was removed."""
    path = _store_path(channel, chat_key, tenant_id=tenant_id)
    removed = _clear_store(path)
    if removed:
        _audit(channel=channel, chat_key=chat_key, uid=str(uid or ""),
               action="cleared", engine=None, model=None)
    return removed


# ── Env-injection helper (consumed by adapter._build_spawn_env) ──────

def env_overlay(channel: str, chat_key: str, *,
                tenant_id: str | None = None) -> dict[str, str]:
    """Return the env-var keys to overlay onto the OS-turn subprocess.

    ``CORVIN_DELEGATE_PREF_ENGINE`` is set iff a valid preference is on
    disk; ``CORVIN_DELEGATE_PREF_MODEL`` is set iff the preference
    carries an explicit model. An empty dict means "no preference, the
    orchestrator decides freely".

    This is the single integration point with adapter.py — keeping it
    pure (no side effects, no logging) lets the adapter call it on
    every inbox message without measurable overhead.
    """
    pref = current(channel, chat_key, tenant_id=tenant_id)
    if not pref:
        return {}
    overlay: dict[str, str] = {"CORVIN_DELEGATE_PREF_ENGINE": pref["engine"]}
    if pref.get("model"):
        overlay["CORVIN_DELEGATE_PREF_MODEL"] = pref["model"]
    return overlay


# ── CLI ──────────────────────────────────────────────────────────────
#
# `python -m engine_switch <subcmd> ...` — the JS dispatcher in
# in_chat_commands.js shells out to this CLI so the JS layer never
# has to re-implement the validation / audit code path.

_USAGE = """\
engine_switch.py — per-chat worker-engine preference (Layer 29 companion).

Usage:
  python3 engine_switch.py show     <channel> <chat_key>
  python3 engine_switch.py set      <channel> <chat_key> <alias> [--uid UID]
  python3 engine_switch.py clear    <channel> <chat_key> [--uid UID]
  python3 engine_switch.py aliases

Aliases:
  claude   → claude_code (no model override)
  codex    → codex_cli (no model override)
  opencode → opencode + ollama/qwen3:8b
  cloud    → opencode + ollama-cloud/qwen3-coder-next
"""


def _fmt_pref(pref: dict[str, str] | None) -> str:
    if not pref:
        return "(no preference — orchestrator decides freely)"
    engine = pref.get("engine", "?")
    model = pref.get("model", "")
    if model:
        return f"engine={engine}  model={model}"
    return f"engine={engine}  (no model pin)"


def main(argv: list[str] | None = None) -> int:
    import argparse
    argv = list(argv if argv is not None else os.sys.argv[1:])
    p = argparse.ArgumentParser(prog="engine_switch.py", add_help=False)
    p.add_argument("subcommand", nargs="?", default="show")
    p.add_argument("rest", nargs=argparse.REMAINDER)
    p.add_argument("-h", "--help", action="store_true", dest="help")
    args, _ = p.parse_known_args(argv)
    if args.help or args.subcommand in ("help", "-h", "--help"):
        print(_USAGE)
        return 0
    cmd = args.subcommand
    rest = args.rest

    if cmd == "aliases":
        for a in supported_aliases():
            spec = resolve_alias(a)
            if spec:
                m = spec.get("model") or "(no model pin)"
                print(f"{a:9s} → engine={spec['engine']:11s} model={m}")
        return 0

    if cmd == "show":
        if len(rest) < 2:
            print(_USAGE)
            return 2
        channel, chat_key = rest[0], rest[1]
        pref = current(channel, chat_key)
        print(_fmt_pref(pref))
        return 0

    if cmd == "set":
        if len(rest) < 3:
            print(_USAGE)
            return 2
        channel, chat_key, alias = rest[0], rest[1], rest[2]
        uid = ""
        if "--uid" in rest:
            idx = rest.index("--uid")
            if idx + 1 < len(rest):
                uid = rest[idx + 1]
        spec = resolve_alias(alias)
        if spec is None:
            print(f"unknown engine alias: {alias!r}")
            print(f"supported: {', '.join(supported_aliases())}")
            return 2
        try:
            stored = set_preference(
                channel, chat_key,
                engine=spec["engine"] or "",
                model=spec.get("model"),
                uid=uid,
            )
        except ValueError as e:
            print(f"error: {e}")
            return 2
        print(f"set: engine={stored['engine']}  model={stored['model'] or '(none)'}")
        return 0

    if cmd == "clear":
        if len(rest) < 2:
            print(_USAGE)
            return 2
        channel, chat_key = rest[0], rest[1]
        uid = ""
        if "--uid" in rest:
            idx = rest.index("--uid")
            if idx + 1 < len(rest):
                uid = rest[idx + 1]
        removed = clear_preference(channel, chat_key, uid=uid)
        print("cleared" if removed else "no preference set")
        return 0

    print(_USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
