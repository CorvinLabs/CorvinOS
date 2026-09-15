"""personal_tools.py — user's own forged tools (Layer 27).

Personal tools are Forge tools that live in user-scope under the
reserved ``me.*`` namespace. They survive every session reset, are
auto-injected as a discovery block into every Bridge turn, and the user
saves / removes them explicitly via slash-commands. They pair with the
Layer-26 user_style memory: together they form the user's "shell
loadout" — knowledge plus actions.

Key contracts
-------------
- **Storage**: ``<corvin_home>/global/forge/tools/me.<name>.py`` for
  the body, ``<corvin_home>/global/forge/registry.json`` for the
  manifest entry. Namespace ``me.`` is reserved — only this module
  writes there.
- **No grade-gate**: ``save_from_scope`` skips the regular Forge
  promotion ladder (which requires grades). The user's explicit
  ``/tool save`` is the sanctioned bypass; linter / sandbox /
  path-gate / audit stay active for normal tool execution.
- **Audit-first**: ``tool.user_saved`` / ``tool.user_removed`` write
  BEFORE the on-disk mutation so a partial failure leaves a trail.
- **Path-gate compatibility**: writes happen via direct Python file-
  IO from this module (operator-trusted, mirror of how forge MCP
  itself writes there). Path-gate continues to block the LLM from
  bypassing this module via Bash / Write / Edit.
- **Tenant-isolated**: paths resolve through ``forge.paths`` so the
  ADR-0007 tenant axis is honored.

Cost contract
-------------
This module MUST NOT import the Anthropic SDK. It is a pure data
layer plus a small CLI. The "LLM-suggest after non-trivial forge"
behaviour is handled in the persona's append_system prompt, not
here.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import threading
import time
from dataclasses import dataclass, asdict, field
from pathlib import Path
from typing import Any, Callable

# ── Audit hash chain (best-effort import) ──────────────────────────────────
_audit_writer: Callable[..., Any] | None = None
try:
    _HERE = Path(__file__).resolve().parent
    _FORGE_TOP = _HERE.parent.parent / "forge"
    if _FORGE_TOP.is_dir() and str(_FORGE_TOP) not in sys.path:
        sys.path.insert(0, str(_FORGE_TOP))
    from forge.security_events import write_event as _audit_writer  # type: ignore  # noqa: E402
except Exception:  # noqa: BLE001
    _audit_writer = None


# ── Tunables ───────────────────────────────────────────────────────────────

NAMESPACE = "me."
INJECT_HEADING = "## Your personal tools (always available)"
MAX_INJECT_TOOLS = 15
PERSONAL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,40}$")
STALE_DAYS = 90        # listed with "(unused 90+d)" hint, never auto-deleted
UNSTABLE_ERROR_RATE = 0.5   # listed with "(unstable)" if errors > this
USAGE_LOOKBACK_DAYS = 30


# ── Storage layout ─────────────────────────────────────────────────────────

def _corvin_home(*, corvin_home: Path | None = None) -> Path:
    if corvin_home is not None:
        return Path(corvin_home)
    env = os.environ.get("CORVIN_HOME")
    if env:
        return Path(env)
    try:
        from forge.paths import corvin_home  # type: ignore  # noqa: PLC0415
        return Path(corvin_home())
    except Exception:  # noqa: BLE001
        return Path.home() / ".corvin"


def _user_forge_dir(*, corvin_home: Path | None = None) -> Path:
    return _corvin_home(corvin_home=corvin_home) / "global" / "forge"


def _registry_path(*, corvin_home: Path | None = None) -> Path:
    return _user_forge_dir(corvin_home=corvin_home) / "registry.json"


def _tools_dir(*, corvin_home: Path | None = None) -> Path:
    return _user_forge_dir(corvin_home=corvin_home) / "tools"


def _audit_path(*, corvin_home: Path | None = None) -> Path:
    return _user_forge_dir(corvin_home=corvin_home) / "audit.jsonl"


_STORE_LOCK = threading.RLock()


def _read_registry(p: Path) -> dict[str, Any]:
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_registry(p: Path, data: dict[str, Any]) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
    tmp.replace(p)
    try:
        os.chmod(p, 0o600)
    except OSError:
        pass


def _audit(event_type: str, *, corvin_home: Path | None = None,
           details: dict | None = None) -> None:
    if _audit_writer is None:
        return
    try:
        _audit_writer(
            _audit_path(corvin_home=corvin_home),
            event_type, tool="personal_tools",
            details=details or {},
        )
    except Exception:  # noqa: BLE001
        pass


# ── Errors ─────────────────────────────────────────────────────────────────

class PersonalToolError(Exception):
    """Base error class for personal_tools."""


class InvalidPersonalName(PersonalToolError):
    pass


class ToolNotFound(PersonalToolError):
    pass


class ToolAlreadyExists(PersonalToolError):
    pass


# ── Validation ─────────────────────────────────────────────────────────────

def validate_personal_name(name: str) -> str:
    """Validate the bare personal name (without ``me.`` prefix).

    The full on-disk name is ``me.<name>`` — callers may pass either
    the bare form or the prefixed form. Returns the full form.
    """
    if not isinstance(name, str):
        raise InvalidPersonalName(f"name must be str, got {type(name).__name__}")
    bare = name[len(NAMESPACE):] if name.startswith(NAMESPACE) else name
    if not PERSONAL_NAME_RE.match(bare):
        raise InvalidPersonalName(
            f"invalid personal name {bare!r} — must match {PERSONAL_NAME_RE.pattern}"
        )
    return NAMESPACE + bare


# ── Public API ─────────────────────────────────────────────────────────────

@dataclass
class PersonalTool:
    name: str                          # full name including ``me.`` prefix
    description: str
    runtime: str = "python"
    impl_path: str = ""                # absolute path
    sha256: str = ""
    created_at: float = field(default_factory=time.time)
    saved_from_scope: str = ""         # task / session / project (origin)
    call_count: int = 0
    last_used_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_registry_entry(cls, entry: dict[str, Any]) -> "PersonalTool":
        return cls(
            name=entry.get("name", ""),
            description=entry.get("description", ""),
            runtime=entry.get("runtime", "python"),
            impl_path=entry.get("impl_path", ""),
            sha256=entry.get("sha256", ""),
            created_at=float(entry.get("created_at") or 0.0),
            saved_from_scope=entry.get("saved_from_scope", ""),
            call_count=int(entry.get("call_count") or 0),
            last_used_at=(float(entry["last_used_at"])
                          if entry.get("last_used_at") is not None else None),
        )


def list_personal(*, corvin_home: Path | None = None) -> list[PersonalTool]:
    """All personal tools (entries in user-scope registry whose name
    starts with ``me.``)."""
    reg = _read_registry(_registry_path(corvin_home=corvin_home))
    out: list[PersonalTool] = []
    for name, entry in reg.items():
        if not isinstance(name, str) or not name.startswith(NAMESPACE):
            continue
        if not isinstance(entry, dict):
            continue
        # Ensure the dict carries the canonical name field so
        # PersonalTool.from_registry_entry has a consistent input.
        entry = {**entry, "name": name}
        out.append(PersonalTool.from_registry_entry(entry))
    out.sort(key=lambda t: ((t.last_used_at or 0.0), t.created_at), reverse=True)
    return out


def get_personal(name: str, *, corvin_home: Path | None = None) -> PersonalTool | None:
    full = validate_personal_name(name)
    for t in list_personal(corvin_home=corvin_home):
        if t.name == full:
            return t
    return None


def save_from_body(personal_name: str, *,
                   description: str,
                   impl_text: str,
                   runtime: str = "python",
                   saved_from_scope: str = "manual",
                   corvin_home: Path | None = None,
                   overwrite: bool = False,
                   now: float | None = None) -> PersonalTool:
    """Write a personal tool from raw body text.

    The most general entry point — both ``save_from_scope`` (copying
    an existing forged tool) and direct CLI-driven creation funnel
    through here so the audit trail and registry-update logic stay in
    one place.
    """
    full = validate_personal_name(personal_name)
    if now is None:
        now = time.time()
    if not isinstance(impl_text, str) or not impl_text.strip():
        raise PersonalToolError("impl_text must be non-empty")
    desc = (description or "").strip()
    if not desc:
        raise PersonalToolError("description required")

    with _STORE_LOCK:
        reg_path = _registry_path(corvin_home=corvin_home)
        tools_dir = _tools_dir(corvin_home=corvin_home)
        tools_dir.mkdir(parents=True, exist_ok=True)
        reg = _read_registry(reg_path)
        if full in reg and not overwrite:
            raise ToolAlreadyExists(f"{full!r} already exists; pass overwrite=True")

        # Audit-first (mirror of session_reset audit-then-mutate).
        _audit("tool.user_saved", corvin_home=corvin_home, details={
            "name":             full,
            "saved_from_scope": saved_from_scope,
            "overwrite":        bool(full in reg),
        })

        # Body.
        body_path = tools_dir / f"{full}.py"
        body_path.write_text(impl_text)
        try:
            os.chmod(body_path, 0o600)
        except OSError:
            pass

        # SHA + registry entry.
        import hashlib as _h
        sha = _h.sha256(impl_text.encode("utf-8")).hexdigest()[:16]
        reg[full] = {
            "name":             full,
            "description":      desc,
            "input_schema":     {"type": "object", "properties": {}},
            "runtime":          runtime,
            "impl_path":        str(body_path),
            "scope":            "user",
            "created_at":       now,
            "sha256":           sha,
            "call_count":       int(reg.get(full, {}).get("call_count") or 0),
            "promoted":         True,
            "saved_from_scope": saved_from_scope,
            "meta":             {"personal": True},
        }
        _write_registry(reg_path, reg)
        return PersonalTool.from_registry_entry(reg[full])


def save_from_scope(source_name: str, personal_name: str | None = None,
                    *, source_scope: str = "task",
                    chat_key: str | None = None,
                    corvin_home: Path | None = None,
                    overwrite: bool = False) -> PersonalTool:
    """Copy a forged tool from a lower scope to the personal namespace.

    Reads the source manifest + body from
    ``<corvin_home>/<scope-root>/forge/`` (currently only ``task`` and
    ``session`` scopes are supported — ``project``/``user`` already
    survive resets so they don't need re-saving).

    The personal_name defaults to the source_name's last namespace
    component (``code.poke_api`` → ``poke_api``).
    """
    if source_scope not in ("task", "session"):
        raise PersonalToolError(
            f"source_scope must be task|session, got {source_scope!r}"
        )

    src_forge = _scope_forge_dir(
        corvin_home=corvin_home, scope=source_scope, chat_key=chat_key,
    )
    src_reg = _read_registry(src_forge / "registry.json")
    if source_name not in src_reg:
        raise ToolNotFound(
            f"{source_name!r} not found in {source_scope}-scope registry"
        )
    src_entry = src_reg[source_name]
    src_impl = Path(src_entry.get("impl_path") or "")
    if not src_impl.exists():
        # Fall back to the conventional layout in case the manifest
        # impl_path is stale.
        src_impl = src_forge / "tools" / f"{source_name}.py"
        if not src_impl.exists():
            raise ToolNotFound(
                f"impl body missing for {source_name!r} "
                f"(expected at {src_entry.get('impl_path')})"
            )
    body = src_impl.read_text()

    if personal_name is None:
        # last component of dotted name
        personal_name = source_name.split(".")[-1]

    return save_from_body(
        personal_name,
        description=src_entry.get("description") or f"saved from {source_scope}",
        impl_text=body,
        runtime=src_entry.get("runtime") or "python",
        saved_from_scope=source_scope,
        corvin_home=corvin_home,
        overwrite=overwrite,
    )


def remove(personal_name: str, *,
           corvin_home: Path | None = None) -> bool:
    """Delete a personal tool. Returns True iff it existed."""
    full = validate_personal_name(personal_name)
    with _STORE_LOCK:
        reg_path = _registry_path(corvin_home=corvin_home)
        reg = _read_registry(reg_path)
        if full not in reg:
            return False

        _audit("tool.user_removed", corvin_home=corvin_home, details={
            "name": full,
        })

        impl_path = reg[full].get("impl_path") or ""
        if impl_path:
            try:
                Path(impl_path).unlink(missing_ok=True)
            except OSError:
                pass
        # Fallback canonical path
        canonical = _tools_dir(corvin_home=corvin_home) / f"{full}.py"
        try:
            canonical.unlink(missing_ok=True)
        except OSError:
            pass

        del reg[full]
        _write_registry(reg_path, reg)
        return True


# ── Scope-root resolution (task/session) for save_from_scope ──────────────

def _scope_forge_dir(*, corvin_home: Path | None,
                     scope: str, chat_key: str | None) -> Path:
    """Resolve the forge dir for a given scope.

    ``user`` scope is the canonical ``<corvin_home>/global/forge/``.
    ``project`` scope is repo-local (``<repo>/.corvin/forge/``) — not
    used here because project-scope tools already survive resets.
    ``task`` and ``session`` resolve via ``forge.paths`` if available;
    otherwise fall back to a per-chat sandbox path mirror.
    """
    home = _corvin_home(corvin_home=corvin_home)
    if scope == "user":
        return home / "global" / "forge"
    if scope == "session" and chat_key:
        # Mirror of operator/bridges/shared/paths.py::session-scope.
        # Sessions live under <corvin_home>/sessions/<chat_key>/forge/
        return home / "sessions" / str(chat_key) / "forge"
    if scope == "task":
        # Task-scope under <corvin_home>/tasks/<chat_key>/forge/
        # Real tasks include extra suffixes; tests pass an explicit
        # path via the env override.
        env = os.environ.get("CORVIN_TASK_FORGE_DIR")
        if env:
            return Path(env)
        return home / "tasks" / str(chat_key or "anon") / "forge"
    raise PersonalToolError(f"unknown scope: {scope!r}")


# ── Inject block (adapter side) ────────────────────────────────────────────

def format_inject_block(*, corvin_home: Path | None = None,
                        max_n: int = MAX_INJECT_TOOLS,
                        now: float | None = None) -> str:
    """Render the bullet block for adapter injection.

    Empty string when no personal tools exist (so adapter can simply
    do ``if block: prompt += "\\n\\n" + block``).
    """
    tools = list_personal(corvin_home=corvin_home)
    if not tools:
        return ""
    if now is None:
        now = time.time()
    if max_n > 0:
        tools = tools[:max_n]
    lines = [INJECT_HEADING, ""]
    for t in tools:
        flags = []
        if t.last_used_at is not None:
            age_d = (now - t.last_used_at) / 86400.0
            if age_d > STALE_DAYS:
                flags.append(f"unused {int(age_d)}d")
        bare = t.name[len(NAMESPACE):]
        flag_s = f" ({'; '.join(flags)})" if flags else ""
        desc = (t.description or "").strip().splitlines()[0][:120]
        lines.append(f"- `{NAMESPACE}{bare}` — {desc}{flag_s}")
    return "\n".join(lines)


# ── Operator CLI ───────────────────────────────────────────────────────────

def _cli_main(argv: list[str]) -> int:
    """``python -m personal_tools {sub}``.

    list                              JSON list of personal tools
    save <source> [--as <name>]       copy a task/session tool to me.<name>
                                      [--from task|session] [--chat-key K]
                                      [--overwrite]
    save-body <name> --description D  create a personal tool from stdin body
    rm <name>                         delete a personal tool
    show <name>                       JSON dump of one personal tool
    inject [--max N]                  print the inject block
    """
    import argparse
    p = argparse.ArgumentParser(prog="personal_tools")
    p.add_argument("--corvin-home", default=None)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list")

    s_save = sub.add_parser("save")
    s_save.add_argument("source")
    s_save.add_argument("--as", dest="alias", default=None)
    s_save.add_argument("--from", dest="src_scope", default="task",
                        choices=["task", "session"])
    s_save.add_argument("--chat-key", default=None)
    s_save.add_argument("--overwrite", action="store_true")

    s_sb = sub.add_parser("save-body")
    s_sb.add_argument("name")
    s_sb.add_argument("--description", required=True)
    s_sb.add_argument("--runtime", default="python")
    s_sb.add_argument("--overwrite", action="store_true")

    s_rm = sub.add_parser("rm")
    s_rm.add_argument("name")

    s_show = sub.add_parser("show")
    s_show.add_argument("name")

    s_inj = sub.add_parser("inject")
    s_inj.add_argument("--max", type=int, default=MAX_INJECT_TOOLS)

    args = p.parse_args(argv)
    home = Path(args.corvin_home) if args.corvin_home else None

    try:
        if args.cmd == "list":
            print(json.dumps(
                [t.to_dict() for t in list_personal(corvin_home=home)],
                indent=2, sort_keys=True,
            ))
            return 0

        if args.cmd == "save":
            t = save_from_scope(
                args.source, args.alias,
                source_scope=args.src_scope, chat_key=args.chat_key,
                corvin_home=home, overwrite=args.overwrite,
            )
            print(json.dumps({"ok": True, "tool": t.to_dict()},
                             indent=2, sort_keys=True))
            return 0

        if args.cmd == "save-body":
            body = sys.stdin.read()
            t = save_from_body(
                args.name, description=args.description,
                impl_text=body, runtime=args.runtime,
                saved_from_scope="cli-stdin",
                corvin_home=home, overwrite=args.overwrite,
            )
            print(json.dumps({"ok": True, "tool": t.to_dict()},
                             indent=2, sort_keys=True))
            return 0

        if args.cmd == "rm":
            ok = remove(args.name, corvin_home=home)
            print(json.dumps({"ok": ok, "name": args.name}))
            return 0 if ok else 1

        if args.cmd == "show":
            t = get_personal(args.name, corvin_home=home)
            if t is None:
                print(json.dumps({"ok": False, "name": args.name}))
                return 1
            print(json.dumps(t.to_dict(), indent=2, sort_keys=True))
            return 0

        if args.cmd == "inject":
            block = format_inject_block(corvin_home=home, max_n=args.max)
            print(block)
            return 0

    except InvalidPersonalName as e:
        print(json.dumps({"ok": False, "error": "invalid-name", "msg": str(e)}))
        return 1
    except ToolNotFound as e:
        print(json.dumps({"ok": False, "error": "not-found", "msg": str(e)}))
        return 1
    except ToolAlreadyExists as e:
        print(json.dumps({"ok": False, "error": "exists", "msg": str(e)}))
        return 1
    except PersonalToolError as e:
        print(json.dumps({"ok": False, "error": "personal-tool",
                          "msg": str(e)}))
        return 1

    print(json.dumps({"ok": False, "error": "unknown-command"}))
    return 1


__all__ = [
    "NAMESPACE", "INJECT_HEADING", "MAX_INJECT_TOOLS", "PERSONAL_NAME_RE",
    "PersonalTool",
    "PersonalToolError", "InvalidPersonalName",
    "ToolNotFound", "ToolAlreadyExists",
    "validate_personal_name",
    "list_personal", "get_personal",
    "save_from_body", "save_from_scope", "remove",
    "format_inject_block",
]


if __name__ == "__main__":
    raise SystemExit(_cli_main(sys.argv[1:]))
