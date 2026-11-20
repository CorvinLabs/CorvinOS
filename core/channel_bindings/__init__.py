"""App-bound channels (ADR-2333): a chat that belongs to exactly one plugin.

A bound chat delivers its messages to the plugin's handler and to nothing else - no model turn, no delegation, no
background task. This module is the registry of such chats. It is runtime data of the tenant, never repository content:

    <corvin_home>/tenants/<tid>/global/channel_bindings.json      (mode 0600)

Writers: the plugin lifecycle (claim on enable, suspend on disable, release on uninstall, reconcile at boot) and the
bridge adapter (confirm, failure counting, notices). Readers: the bridge adapter on every inbox message.

Rules (ADR-2333 Decisions 1-3, 5, 7, 8):
  * one record per ``(channel, chat_id)``; chat ids are matched EXACTLY (no patterns); one owner plugin per chat;
  * statuses ``pending`` (claimed, chat still behaves as before) -> ``active`` -> ``suspended``; ``release`` deletes;
  * a claim is ``active`` at once only for a fresh chat (no chat profile, no session history), else ``pending``
    until the owner confirms (``/bind confirm``);
  * a chat whose profile opens it to everyone (``audience: all``) or shows observers a transcript cannot be claimed;
  * every state change writes its audit record FIRST; if that write fails nothing changes;
  * a tampered or unreadable registry: :func:`resolve_strict` raises, and the bridge DROPS a message whose chat id
    appears in the file rather than handing it to the OS turn (drop on doubt); :func:`resolve` answers "unbound".

The handler contract (Decision 5) is built here too, so the adapter and the tests share one implementation.
"""
from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

log = logging.getLogger("corvin.channel_bindings")

REGISTRY_NAME = "channel_bindings.json"
KEY_NAME = "channel_bindings.key"
VERSION = 1
STATUSES = ("pending", "active", "suspended")
MAX_FAILURES = 3
NOTICE_INTERVAL_S = 600
HANDLER_TIMEOUT_S = 15
DESCRIBE_TIMEOUT_S = 10
MAX_TEXT_BYTES = 4096
MAX_REPLY_CHARS = 1900
MAX_OUTPUT_BYTES = 65536

CHANNEL_RE = re.compile(r"^[a-z][a-z0-9_-]{1,31}$")
#: Channels a plugin may bind in this version. Discord chat ids are plain numbers, so the session directories and the
#: ledger use the id unchanged; channels whose ids get rewritten for directory names (Telegram groups, WhatsApp JIDs,
#: e-mail) need the history check to know those names first.
BINDABLE_CHANNELS = frozenset({"discord"})
CHAT_ID_RE = re.compile(r"^[0-9A-Za-z_.:@-]{1,128}$")
PLUGIN_ID_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
ENV_NAME_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
#: The environment the bridge itself provides to a handler (never copied from the caller's environment).
PROVIDED_ENV = ("CORVIN_BRIDGE_SENDER", "CORVIN_BRIDGE_OWNER", "CORVIN_BRIDGE_CHANNEL", "CORVIN_BRIDGE_MSG_ID")
#: Names a manifest may never let through: they would hand the handler the operator's credentials or loader hooks.
DENIED_ENV_RE = re.compile(r"(TOKEN|SECRET|PASSW|API_KEY|_KEY$|KEY_ID|PRIVATE|CREDENTIAL|DATABASE_URL|DSN$|_PAT$|"
                           r"AUTH|COOKIE|^SSH_|^LD_|^DYLD_|^PYTHONSTARTUP$|^PYTHONHOME$)")

DEFAULT_NOTICES = {
    "media": "This channel belongs to an app: only text messages are passed to it.",
    "side": "This channel belongs to an app; that command does not apply here.",
    "suspended": "The app for this channel is not available right now; your message was not stored.",
    "failed": "The app did not answer; your message may not have been stored.",
}


class BindingError(ValueError):
    """A claim or change was refused. The message names the reason, never a chat id or a text."""


# ── paths ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def _tenant_global(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_home  # noqa: PLC0415 - validates the tenant id
    return tenant_home(tenant_id) / "global"


def registry_path(tenant_id: str) -> Path:
    return _tenant_global(tenant_id) / REGISTRY_NAME


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def bridge_settings_file(channel: str) -> Optional[Path]:
    """The bridge settings file for ``channel`` - the same order as ``bridges/shared/paths.resolve_bridge_settings_file``
    (``ADAPTER_BRIDGES_DIR`` override, then ``<corvin_home>/bridges/<channel>``, then the legacy in-repo file)."""
    if not CHANNEL_RE.match(channel or ""):
        return None
    override = os.environ.get("ADAPTER_BRIDGES_DIR")
    if override:
        return Path(override) / channel / "settings.json"
    from core.paths.tenant import corvin_home  # noqa: PLC0415
    canonical = corvin_home() / "bridges" / channel / "settings.json"
    if canonical.exists():
        return canonical
    return _repo_root() / "corvin_operator" / "bridges" / channel / "settings.json"


def chat_profile(channel: str, chat_id: str) -> dict:
    """The EFFECTIVE profile of the chat, the way the daemon reads it: the ``default`` entry overlaid by the chat's
    own entry. Unreadable settings count as an empty profile."""
    p = bridge_settings_file(channel)
    try:
        data = json.loads(p.read_text(encoding="utf-8")) if p else {}
    except (OSError, ValueError):
        return {}
    profiles = data.get("chat_profiles") if isinstance(data, dict) else None
    if not isinstance(profiles, dict):
        return {}
    out: dict = {}
    for key in ("default", chat_id):
        prof = profiles.get(key)
        if isinstance(prof, dict):
            out.update(prof)
    return out


def _chat_own_profile(channel: str, chat_id: str) -> dict:
    """Only the chat's own entry (for "has this chat been configured", not for what applies to it)."""
    p = bridge_settings_file(channel)
    if p is None or not p.exists():
        return {}                                           # no settings at all: nothing configured for the chat
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"unreadable": True}                         # cannot tell: not fresh (a confirmation is cheap)
    profiles = data.get("chat_profiles") if isinstance(data, dict) else None
    prof = profiles.get(chat_id) if isinstance(profiles, dict) else None
    return prof if isinstance(prof, dict) else {}


def chat_is_open(prof: dict) -> bool:
    """Open to others: everyone may write, or observers see a transcript (``observer_visibility: off`` is closed)."""
    return prof.get("audience") == "all" or prof.get("observer_visibility") == "transcript"


def chat_has_history(tenant_id: str, channel: str, chat_id: str) -> bool:
    """The chat has been an OS conversation before: a session ledger, or ANY session directory for it (current,
    voice, legacy root, or a hashed/suffixed workdir name). Unknown counts as history (a confirmation is cheap)."""
    from core.paths.tenant import corvin_home, tenant_home  # noqa: PLC0415
    th, ch, safe = tenant_home(tenant_id), corvin_home(), _safe(chat_id)
    try:
        for led in (th / "session_ledger" / channel / safe / "ledger.jsonl",
                    th / "voice" / "session_ledger" / channel / safe / "ledger.jsonl"):
            if led.is_file() and led.stat().st_size > 0:
                return True
        roots = [th / "sessions", th / "voice" / "sessions", ch / "sessions"]
        if os.environ.get("XDG_CACHE_HOME"):                # the adapter's legacy sessions root
            roots.append(Path(os.environ["XDG_CACHE_HOME"]) / "corvin-voice" / "sessions")
        for base in roots:
            for sub in (base / channel, base / "voice" / channel):
                if (sub / safe).exists() or (sub.is_dir() and any(sub.glob(f"{safe}*"))):
                    return True
        return False
    except OSError:
        return True


def _safe(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.@:-]", "_", str(s))[:128]


# ── storage ───────────────────────────────────────────────────────────────────────────────────────────────────────


@contextlib.contextmanager
def _locked(tenant_id: str) -> Iterator[Path]:
    path = registry_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock = path.with_suffix(".lock")
    fd = os.open(lock, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    msvcrt = None
    try:
        try:
            import fcntl  # noqa: PLC0415
            fcntl.flock(fd, fcntl.LOCK_EX)
        except ImportError:                                 # Windows: msvcrt locks the first byte (retries ~10 s)
            import msvcrt  # type: ignore  # noqa: PLC0415
            msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
        yield path
    finally:
        if msvcrt is not None:
            with contextlib.suppress(OSError):
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        os.close(fd)


def _read(path: Path) -> dict:
    """The registry, or an empty one when absent. A file that exists but does not parse as a registry raises
    BindingError: callers that write refuse, readers resolve nothing (fail closed)."""
    if path.is_symlink():
        raise BindingError("the registry path is a symlink")
    if not path.exists():
        return {"version": VERSION, "bindings": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise BindingError("the registry cannot be read") from None
    if not isinstance(data, dict) or data.get("version") != VERSION or not isinstance(data.get("bindings"), list):
        raise BindingError("the registry has an unknown shape")
    for b in data["bindings"]:
        if not (isinstance(b, dict) and CHANNEL_RE.match(str(b.get("channel", "")))
                and CHAT_ID_RE.match(str(b.get("chat_id", ""))) and b.get("status") in STATUSES
                and PLUGIN_ID_RE.match(str(b.get("plugin_id", "")))):
            raise BindingError("the registry holds a malformed binding")
    return data


def _write(path: Path, data: dict) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".channel_bindings.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)


# ── audit ─────────────────────────────────────────────────────────────────────────────────────────────────────────

AuditFn = Callable[[str, dict], None]


def default_audit(tenant_id: str) -> AuditFn:
    """Writes to the tenant's hash-chained audit chain through the one writer (``forge.security_events``)."""
    def _write_event(event: str, details: dict) -> None:
        forge_dir = str(_repo_root() / "corvin_operator" / "forge")
        if forge_dir not in sys.path:
            sys.path.append(forge_dir)
        from forge.security_events import write_event  # type: ignore  # noqa: PLC0415
        from core.paths.tenant import tenant_audit_chain  # noqa: PLC0415
        import inspect  # noqa: PLC0415
        kwargs = {}
        if "tenant_id" in inspect.signature(write_event).parameters:
            # a writer that can tag a record for a tenant other than the process tenant (the multi-tenant console)
            kwargs["tenant_id"] = tenant_id
        write_event(tenant_audit_chain(tenant_id), event, details={"tenant_id": tenant_id, **details}, **kwargs)
    return _write_event


def _audit_first(audit: Optional[AuditFn], tenant_id: str, event: str, details: dict) -> None:
    fn = audit or default_audit(tenant_id)
    try:
        fn(event, details)
    except Exception as exc:  # noqa: BLE001 - whatever the writer raises: the change must not happen unrecorded
        raise BindingError(f"the audit record could not be written ({type(exc).__name__})") from None


# ── reads ─────────────────────────────────────────────────────────────────────────────────────────────────────────


def plugin_enabled(tenant_id: str, plugin_id: str) -> bool:
    """Is the owning plugin enabled right now? Read from the tenant's plugin registry, so a binding whose suspend failed
    during a disable still never reaches a disabled plugin's handler. No registry file (no plugin system on this
    install, e.g. a bare bridge) counts as enabled; a registry that exists but cannot be read, or that has no enabled
    record for the plugin, counts as disabled (drop on doubt)."""
    from core.paths.tenant import tenant_home  # noqa: PLC0415
    reg = tenant_home(tenant_id) / "plugins" / "registry.yaml"
    if not reg.exists():
        return True
    try:
        import yaml  # noqa: PLC0415
        doc = yaml.safe_load(reg.read_text(encoding="utf-8")) or {}
        plugins = (doc.get("plugins") or (doc.get("spec") or {}).get("plugins") or {}) if isinstance(doc, dict) else {}
        rec = plugins.get(plugin_id) if isinstance(plugins, dict) else None
        return bool(isinstance(rec, dict) and rec.get("enabled") is True)
    except Exception:  # noqa: BLE001
        return False


def list_bindings(tenant_id: str) -> list[dict]:
    try:
        return [dict(b) for b in _read(registry_path(tenant_id))["bindings"]]
    except BindingError:
        return []


def resolve_strict(tenant_id: str, channel: str, chat_id: str, *, any_channel: bool = False) -> Optional[dict]:
    """Like :func:`resolve`, but an unreadable registry RAISES BindingError instead of answering "unbound": the bridge
    must not hand a possibly-bound chat's text to the OS turn because the file is damaged (ADR-2333 Decision 6).
    ``any_channel`` matches the chat id alone (envelopes that carry no channel)."""
    if not chat_id or not CHAT_ID_RE.match(str(chat_id)):
        return None
    data = _read(registry_path(tenant_id))
    for b in data["bindings"]:
        if b["chat_id"] == str(chat_id) and (any_channel or b["channel"] == channel):
            return dict(b)
    return None


def resolve(tenant_id: str, channel: str, chat_id: str) -> Optional[dict]:
    """The binding of this chat, or None. An unreadable registry binds nothing here - readers that route messages use
    :func:`resolve_strict` instead and drop on doubt."""
    if not channel or not chat_id or not CHAT_ID_RE.match(str(chat_id)):
        return None
    try:
        data = _read(registry_path(tenant_id))
    except BindingError as exc:
        log.warning("channel bindings unreadable (%s): no chat is treated as bound", exc)
        return None
    for b in data["bindings"]:
        if b["channel"] == channel and b["chat_id"] == str(chat_id):
            return dict(b)
    return None


# ── declarations (from a plugin manifest) ──────────────────────────────────────────────────────────────────────────


def validate_declaration(decl: Any, root: Path) -> dict:
    """A manifest's ``bridge_channel`` -> the normalised dict stored on the plugin record. Raises BindingError.

    Shape: ``{channel, describe: {argv}, handler: {argv, env_allow?, pythonpath?}}``. ``argv[0]`` may be ``python``
    (the bridge's own interpreter); every other element that looks like a path must stay inside ``root``."""
    if not isinstance(decl, dict):
        raise BindingError("bridge_channel must be a mapping")
    channel = str(decl.get("channel", ""))
    if not CHANNEL_RE.match(channel):
        raise BindingError("bridge_channel.channel is not a channel name")
    if channel not in BINDABLE_CHANNELS:
        raise BindingError(f"bridge_channel.channel {channel!r} cannot be bound in this version")
    root = Path(root).resolve()
    if not root.is_dir():
        raise BindingError("the plugin directory does not exist")

    def _argv(spec: Any, where: str) -> list[str]:
        argv = spec.get("argv") if isinstance(spec, dict) else None
        if not isinstance(argv, list) or not argv or not all(isinstance(a, str) and a and "\x00" not in a and len(a) <= 200
                                                              for a in argv) or len(argv) > 24:
            raise BindingError(f"bridge_channel.{where}.argv must be a short list of strings")
        for i, a in enumerate(argv):
            if i == 0 and a in ("python", "python3"):
                continue
            if "/" in a or "\\" in a or a.startswith("~"):
                p = (root / a).resolve()
                if os.path.isabs(a) or root not in (p, *p.parents):
                    raise BindingError(f"bridge_channel.{where}.argv leaves the plugin directory")
        return list(argv)

    handler = decl.get("handler") if isinstance(decl.get("handler"), dict) else {}
    env_allow = handler.get("env_allow") or []
    if not isinstance(env_allow, list) or not all(isinstance(n, str) and ENV_NAME_RE.match(n) for n in env_allow):
        raise BindingError("bridge_channel.handler.env_allow must list environment variable names")
    if any(DENIED_ENV_RE.search(n) for n in env_allow):
        raise BindingError("bridge_channel.handler.env_allow names a credential or loader variable")
    pythonpath = handler.get("pythonpath") or []
    if not isinstance(pythonpath, list) or not all(isinstance(p, str) for p in pythonpath):
        raise BindingError("bridge_channel.handler.pythonpath must be a list of relative directories")
    for p in pythonpath:
        q = (root / p).resolve()
        if os.path.isabs(p) or root not in (q, *q.parents):
            raise BindingError("bridge_channel.handler.pythonpath leaves the plugin directory")
    return {
        "channel": channel, "root": str(root),
        "describe": {"argv": _argv(decl.get("describe"), "describe")},
        "handler": {"argv": _argv(handler, "handler"), "env_allow": sorted(set(env_allow)), "pythonpath": list(pythonpath)},
    }


def _command(decl: dict, which: str, extra_env: Optional[dict] = None) -> tuple[list[str], dict, str]:
    """argv, environment and cwd for running the plugin's ``describe`` or ``handler`` command. The environment is
    CLEARED: only the declared names (copied from this process) plus what the bridge provides."""
    spec = decl["handler"] if which == "handler" else decl["describe"]
    argv = list(spec["argv"])
    if argv[0] in ("python", "python3"):
        argv[0] = sys.executable
    root = decl["root"]
    env: dict[str, str] = {}
    for name in decl["handler"].get("env_allow", []):
        if name in os.environ:
            env[name] = os.environ[name]
    if decl["handler"].get("pythonpath"):
        env["PYTHONPATH"] = os.pathsep.join(str(Path(root) / p) for p in decl["handler"]["pythonpath"])
    if "CORVIN_FORGE_PATH" in decl["handler"].get("env_allow", []) and "CORVIN_FORGE_PATH" not in env:
        env["CORVIN_FORGE_PATH"] = str(_repo_root() / "corvin_operator" / "forge")
    env.setdefault("PATH", os.environ.get("PATH", "/usr/bin:/bin"))
    env["PYTHONIOENCODING"] = "utf-8"
    env.update(extra_env or {})
    return argv, env, root


def describe(decl: dict, tenant_id: str) -> dict:
    """Ask the plugin which chat it serves: ``{"chat_id": ..., "notices": {...}}``. Raises BindingError."""
    argv, env, cwd = _command(decl, "describe", {"CORVIN_TENANT_ID": tenant_id})
    try:
        r = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, timeout=DESCRIBE_TIMEOUT_S, check=False)
    except (OSError, subprocess.TimeoutExpired):
        raise BindingError("the plugin's describe command did not run") from None
    if r.returncode != 0:
        raise BindingError("the plugin serves no chat right now (describe exited non-zero)")
    try:
        doc = json.loads(r.stdout[:MAX_OUTPUT_BYTES].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BindingError("the plugin's describe output is not JSON") from None
    chat_id = str(doc.get("chat_id", "")) if isinstance(doc, dict) else ""
    if not CHAT_ID_RE.match(chat_id):
        raise BindingError("the plugin's describe output names no valid chat id")
    notices = doc.get("notices") if isinstance(doc.get("notices"), dict) else {}
    clean = {k: str(v)[:300] for k, v in notices.items() if k in DEFAULT_NOTICES and isinstance(v, str) and v.strip()}
    return {"chat_id": chat_id, "notices": clean}


# ── writes ────────────────────────────────────────────────────────────────────────────────────────────────────────


def _now() -> float:
    return time.time()


def claim(tenant_id: str, *, plugin_id: str, decl: dict, chat_id: str, notices: Optional[dict] = None,
          claimed_by: str = "plugin_manifest", audit: Optional[AuditFn] = None) -> dict:
    """Register ``chat_id`` as bound to ``plugin_id``. Idempotent for the same owner (refreshes the handler and
    notices, keeps the status). Refused (BindingError, audited) when another plugin owns the chat or the chat is open
    to everyone or shows observers a transcript."""
    if not PLUGIN_ID_RE.match(plugin_id or ""):
        raise BindingError("invalid plugin id")
    channel = decl.get("channel", "")
    if not CHANNEL_RE.match(channel) or not CHAT_ID_RE.match(str(chat_id or "")):
        raise BindingError("invalid channel or chat id")
    prof = chat_profile(channel, chat_id)
    if chat_is_open(prof):
        _audit_soft(audit, tenant_id, "channel_binding.refused", {"plugin_id": plugin_id, "channel": channel,
                                                                  "reason": "chat_open_to_others"})
        raise BindingError("this chat is open to others or shows observers a transcript; it cannot belong to an app")
    with _locked(tenant_id) as path:
        data = _read(path)
        for b in data["bindings"]:
            if b["channel"] == channel and b["chat_id"] == str(chat_id):
                if b["plugin_id"] != plugin_id:
                    _audit_soft(audit, tenant_id, "channel_binding.refused", {"plugin_id": plugin_id, "channel": channel,
                                                                              "reason": "owned_by_another_plugin"})
                    raise BindingError("this chat already belongs to another plugin")
                if b.get("decl") != decl:                   # what will RUN changed: recorded before it is stored
                    _audit_first(audit, tenant_id, "channel_binding.claimed",
                                 {"binding_id": b["binding_id"], "plugin_id": plugin_id, "channel": channel,
                                  "reason": "handler_declaration_changed"})
                b["decl"] = decl
                b["notices"] = dict(notices or {})
                b["last_seen_at"] = _now()
                _write(path, data)
                return dict(b)
        fresh = not _chat_own_profile(channel, str(chat_id)) and not chat_has_history(tenant_id, channel, str(chat_id))
        rec = {
            "binding_id": uuid.uuid4().hex[:16], "channel": channel, "chat_id": str(chat_id), "plugin_id": plugin_id,
            "status": "active" if fresh else "pending", "mode": "exclusive", "claimed_by": claimed_by,
            "claimed_at": _now(), "last_seen_at": _now(), "failures": 0, "last_notice_at": 0.0,
            "data_class": "personal", "decl": decl, "notices": dict(notices or {}),
        }
        _audit_first(audit, tenant_id, "channel_binding.claimed",
                     {"binding_id": rec["binding_id"], "plugin_id": plugin_id, "channel": channel,
                      "reason": "fresh_chat" if fresh else "needs_confirmation"})
        data["bindings"].append(rec)
        _write(path, data)
        return dict(rec)


def _audit_soft(audit: Optional[AuditFn], tenant_id: str, event: str, details: dict) -> None:
    """For refusals: the refusal stands whether or not its record could be written."""
    try:
        (audit or default_audit(tenant_id))(event, details)
    except Exception as exc:  # noqa: BLE001
        log.warning("channel binding audit (%s) failed: %s", event, type(exc).__name__)


def _update(tenant_id: str, match: Callable[[dict], bool], change: Callable[[dict], Optional[str]],
            audit: Optional[AuditFn], *, remove: bool = False) -> list[dict]:
    """Apply ``change`` to every matching binding; ``change`` returns the audit event (or None for no record). Each
    audited change is written to the file right after its record, so a later failing audit never leaves an earlier
    record describing a change that was not stored."""
    out = []
    with _locked(tenant_id) as path:
        data = _read(path)
        i = 0
        while i < len(data["bindings"]):
            b = data["bindings"][i]
            if not match(b):
                i += 1
                continue
            cand = dict(b)
            event = change(cand)
            if event:
                _audit_first(audit, tenant_id, event, {"binding_id": cand["binding_id"], "plugin_id": cand["plugin_id"],
                                                       "channel": cand["channel"], "reason": str(cand.get("reason", ""))[:60]})
            out.append(dict(cand))
            if remove:
                data["bindings"].pop(i)
            else:
                data["bindings"][i] = cand
                i += 1
            if event or remove or cand != b:
                _write(path, data)
    return out


def _chat_still_closed(b: dict) -> None:
    """A chat opened to everyone or to observers after the claim can no longer be (re)activated."""
    if chat_is_open(chat_profile(b["channel"], b["chat_id"])):
        raise BindingError("this chat is now open to others or shows observers a transcript; it cannot be activated")


def confirm(tenant_id: str, binding_id: str, *, audit: Optional[AuditFn] = None) -> Optional[dict]:
    """``pending -> active`` (the owner said so). Returns the binding, or None when nothing was pending."""
    changed = []

    def change(b: dict) -> Optional[str]:
        if b["status"] != "pending":
            return None
        _chat_still_closed(b)
        b["status"], b["reason"], b["failures"] = "active", "confirmed", 0
        changed.append(True)
        return "channel_binding.activated"
    hits = _update(tenant_id, lambda b: b["binding_id"] == binding_id, change, audit)
    return hits[0] if hits and changed else None


def reactivate(tenant_id: str, binding_id: str, *, audit: Optional[AuditFn] = None) -> Optional[dict]:
    """``suspended -> active`` on the owner's or operator's explicit decision - ONLY for a binding the bridge suspended
    after handler failures. A binding suspended because its plugin is disabled (or orphaned) comes back with the plugin,
    never from the chat."""
    changed = []

    def change(b: dict) -> Optional[str]:
        if b["status"] != "suspended" or b.get("reason") != "handler_failures":
            return None
        _chat_still_closed(b)
        b["status"], b["reason"], b["failures"] = "active", "resumed_by_owner", 0
        changed.append(True)
        return "channel_binding.activated"
    hits = _update(tenant_id, lambda b: b["binding_id"] == binding_id, change, audit)
    return hits[0] if hits and changed else None


def suspend(tenant_id: str, *, binding_id: str = "", plugin_id: str = "", reason: str = "",
            audit: Optional[AuditFn] = None) -> list[dict]:
    def change(b: dict) -> Optional[str]:
        if b["status"] == "suspended":
            return None
        b["status"], b["reason"] = "suspended", reason or "suspended"
        return "channel_binding.suspended"
    return _update(tenant_id, lambda b: (binding_id and b["binding_id"] == binding_id) or
                   (plugin_id and b["plugin_id"] == plugin_id), change, audit)


def resume(tenant_id: str, *, plugin_id: str, audit: Optional[AuditFn] = None) -> list[dict]:
    """A suspended binding of a re-enabled plugin goes back to where it was before (active; a never-confirmed one
    stays pending - re-enabling is not a confirmation)."""
    def change(b: dict) -> Optional[str]:
        if b["status"] != "suspended":
            return None
        b["status"] = "active" if b.get("was") == "active" else "pending"
        b["failures"], b["reason"] = 0, "resumed"
        # every status change is recorded first - also suspended -> pending
        return "channel_binding.activated" if b["status"] == "active" else "channel_binding.claimed"
    return _update(tenant_id, lambda b: b["plugin_id"] == plugin_id, change, audit)


def release(tenant_id: str, *, binding_id: str = "", plugin_id: str = "", reason: str = "released",
            audit: Optional[AuditFn] = None) -> list[dict]:
    """Delete the binding(s): the chat is an ordinary chat again."""
    def change(b: dict) -> str:
        b["reason"] = reason
        return "channel_binding.released"
    return _update(tenant_id, lambda b: (binding_id and b["binding_id"] == binding_id) or
                   (plugin_id and b["plugin_id"] == plugin_id), change, audit, remove=True)


def record_result(tenant_id: str, binding_id: str, ok: bool, *, audit: Optional[AuditFn] = None) -> Optional[dict]:
    """Count consecutive handler failures; the MAX_FAILURES-th suspends the binding (audited)."""
    def change(b: dict) -> Optional[str]:
        if ok:
            b["failures"] = 0
            return None
        b["failures"] = int(b.get("failures", 0)) + 1
        if b["failures"] >= MAX_FAILURES and b["status"] == "active":
            b["was"], b["status"], b["reason"] = "active", "suspended", "handler_failures"
            return "channel_binding.suspended"
        return None
    hits = _update(tenant_id, lambda b: b["binding_id"] == binding_id, change, audit)
    return hits[0] if hits else None


def take_notice_slot(tenant_id: str, binding_id: str) -> bool:
    """True at most once per NOTICE_INTERVAL_S per binding (so a suspended chat is not answered on every message)."""
    granted = []

    def change(b: dict) -> None:
        if _now() - float(b.get("last_notice_at") or 0) >= NOTICE_INTERVAL_S:
            b["last_notice_at"] = _now()
            granted.append(True)
        return None
    _update(tenant_id, lambda b: b["binding_id"] == binding_id, change, None)
    return bool(granted)


def mark_was(tenant_id: str, plugin_id: str) -> None:
    """Remember each binding's status before a suspension caused by disabling the plugin."""
    def change(b: dict) -> None:
        if b["status"] != "suspended":
            b["was"] = b["status"]
        return None
    _update(tenant_id, lambda b: b["plugin_id"] == plugin_id, change, None)


# ── sender token and handler call ──────────────────────────────────────────────────────────────────────────────────


def sender_token(tenant_id: str, uid: str) -> str:
    """``HMAC-SHA256(per-tenant secret, uid)`` truncated to 16 hex. Stable per tenant, not guessable from a platform
    id (unlike the unsalted fingerprint in log lines)."""
    key_path = _tenant_global(tenant_id) / KEY_NAME
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.is_symlink():
        raise BindingError("the sender-token key path is a symlink")
    try:
        key = key_path.read_bytes()
    except FileNotFoundError:
        key = secrets.token_bytes(32)
        fd, tmp = tempfile.mkstemp(dir=str(key_path.parent), prefix=".channel_bindings.key.")
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(key)
                fh.flush()
                os.fsync(fh.fileno())
            os.chmod(tmp, 0o600)
            try:
                os.link(tmp, key_path)                      # atomic and exclusive: a reader never sees a half key
            except FileExistsError:
                key = key_path.read_bytes()
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
    if len(key) < 32:
        raise BindingError("the sender-token key is damaged")
    return hmac.new(key, str(uid).encode("utf-8"), hashlib.sha256).hexdigest()[:16]


def call_handler(binding: dict, tenant_id: str, *, text: str, sender: str, msg_id: str) -> tuple[bool, str]:
    """Run the plugin's handler for one message. Returns ``(ok, reply)``. The reply target is never taken from the
    handler: the caller sends ``reply`` to the chat the message came from."""
    data = text.encode("utf-8")
    if len(data) > MAX_TEXT_BYTES:
        return True, "This message is too long for the app (limit 4 kB); it was not stored."
    decl = binding["decl"]
    argv, env, cwd = _command(decl, "handler", {
        "CORVIN_TENANT_ID": tenant_id, "CORVIN_BRIDGE_SENDER": sender_token(tenant_id, sender),
        "CORVIN_BRIDGE_OWNER": "1", "CORVIN_BRIDGE_CHANNEL": binding["channel"],
        "CORVIN_BRIDGE_MSG_ID": re.sub(r"[^A-Za-z0-9_.-]", "", str(msg_id))[:64],
    })
    try:
        r = subprocess.run(argv, input=data, cwd=cwd, env=env, capture_output=True, timeout=HANDLER_TIMEOUT_S, check=False)
    except subprocess.TimeoutExpired:
        return False, ""
    except OSError:
        return False, ""
    if r.returncode != 0:
        return False, ""
    try:
        doc = json.loads(r.stdout[:MAX_OUTPUT_BYTES].decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return False, ""
    reply = doc.get("text") if isinstance(doc, dict) else None
    if not isinstance(reply, str) or not reply.strip():
        return False, ""
    return True, reply.strip()[:MAX_REPLY_CHARS]


def notice(binding: Optional[dict], kind: str) -> str:
    own = (binding or {}).get("notices") or {}
    return str(own.get(kind) or DEFAULT_NOTICES[kind])
