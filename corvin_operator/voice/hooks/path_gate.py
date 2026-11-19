#!/usr/bin/env python3
"""path_gate.py — PreToolUse hook that protects forge / skill-forge workspaces
from direct filesystem writes.

Runs as a Claude Code PreToolUse hook on the matcher
``Write|Edit|MultiEdit|NotebookEdit|Bash|WebFetch``. Reads the JSON payload
from stdin, checks the tool's write target(s) against the protected-path
set, and:

  - exit 0      → allow the tool call
  - exit 2 + stderr message → deny the tool call (Claude Code propagates
                    the stderr text back to the model as the deny reason)

Protected paths (match if the absolute path is under one of these roots):

  <corvin_home>/**/forge/**          forge workspaces (any scope)
  <corvin_home>/**/skill-forge/**    skill-forge workspaces (any scope)
  <corvin_home>/**/audit.jsonl       unified hash-chain audit log
  <corvin_home>/**/policy.json       per-scope policy override
  <corvin_home>/**/data_policy.{yaml,yml,json}   ADR-0012 PII policy
  <corvin_home>/**/compute/**        ADR-0013 compute-worker artefact tree
  <corvin_home>/**/compute/worker.sock  ADR-0013 compute-worker Unix socket
  <corvin_home>/**/memory/**         ADR-0016 conversation recall + user-model
  <corvin_home>/**/license/**        ADR-0017 license-gate token + state
  <corvin_home>/**/instance_key.pem      ADR-0145 Ed25519 instance signing key
  <corvin_home>/**/instance_cert.jwt     ADR-0145 IBC (Instance Binding Certificate)
  <corvin_home>/**/instance_pubkey.pem   ADR-0145 Ed25519 public key (companion)
  <corvin_home>/**/packages/**       ADR-0032 installed AWPKG packages
  <repo>/corvin_operator/skill-forge/skills/dyn/**   engine-facing slot mirror
  <repo>/operator/forge/forge/policy.json     bundled default policy

Both directory shapes resolve to the same protected set.

Bash detection is the fragile part. We extract write-target paths via a
small set of regex / shlex rules covering: ``>`` / ``>>`` redirects,
``tee``, ``mv`` / ``cp`` / ``install`` / ``rsync`` last-arg, ``sed -i``,
``dd of=``, and ``python -c "open(...,'w')"``. If the command contains
``eval`` / ``exec`` or unbalanced quotes AND any reference to a protected
path component, we fail closed — denying is cheaper than missing a vector.

No bypass token is needed for the forge / skill-forge MCP servers. They
write via plain Python file-IO (``_atomic_write_text``) inside their own
subprocess, which never traverses Claude's PreToolUse tool-call gate —
the hook only sees Claude's own Write/Edit/Bash/etc. tool calls. The
MCP path therefore stays open by construction; the only thing this hook
closes is direct Claude tool calls bypassing the MCP server.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

# ADR-0141 Tier 3 — capability version, parsed (without import) by
# security_capabilities.bootstrap_core_capabilities() to register this
# out-of-process hook by file presence.
CAPABILITY_VERSION = "2.1"

# ----- path resolution ----------------------------------------------------

def _resolve_aliased_env(canonical: str, legacy: str = "") -> str | None:
    """Read canonical env var. The legacy parameter is retained for call-site
    compatibility but is no longer consulted after Phase 7 hard-cut."""
    return os.environ.get(canonical) or None


def _installed_root() -> Path | None:
    """The runtime root the installer recorded: ``corvin-install`` (step 18)
    writes ``<voice config>/installer.json`` with ``corvin_home`` — the ONE
    pointer from an install to its live root that every process of the user
    can read, including a hook that runs from Claude Code's plugin cache
    outside any checkout and without CORVIN_HOME in its environment."""
    cfg_dir = os.environ.get("VOICE_CONFIG_DIR", "").strip()
    if not cfg_dir:
        xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
        base = Path(os.path.expanduser(xdg)) if xdg else (Path.home() / ".config")
        cfg_dir = str(base / "corvin-voice")
    try:
        import json as _json
        value = _json.loads((Path(cfg_dir) / "installer.json").read_text(encoding="utf-8")).get("corvin_home")
    except (OSError, ValueError, AttributeError):
        return None
    return Path(os.path.expanduser(value)) if isinstance(value, str) and value else None


def _corvin_home() -> Path:
    """Mirror of forge.paths.corvin_home(), inlined to keep the hook
    self-contained and import-free at runtime.

    Resolution: ``CORVIN_HOME`` → the checkout this file sits in (marker
    ``.corvin_repo``) → the root the installer recorded → ``~/.corvin``.
    The former ``(parent / "plugins").is_dir()`` heuristic is gone: when the
    hook runs from the plugin cache (``~/.claude/plugins/cache/…``) that test
    matched ``~/.claude`` and the hook resolved ``~/.claude/.corvin`` — a
    third runtime root nothing else reads (fresh-system review 2026-10-21)."""
    env = _resolve_aliased_env("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    repo = _repo_root()
    if repo is not None:
        return repo / ".corvin"
    installed = _installed_root()
    if installed is not None:
        return installed
    return Path.home() / ".corvin"


def _repo_root() -> Path | None:
    """Nearest ancestor carrying the checkout marker ``.corvin_repo``, else None."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists():
            return parent
    return None


def _vault_path() -> Path:
    """Canonical secret-vault path. Mirrors
    forge.secret_vault.default_vault_path() with the same env override
    so tests that point CORVIN_SECRET_VAULT at a tempdir get coverage."""
    env = os.environ.get("CORVIN_SECRET_VAULT")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    cfg_home = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    return Path(os.path.expanduser(cfg_home)) / "corvin-voice" / "secrets.json"


_MSYS_DRIVE_RE = re.compile(r"^/([A-Za-z])(/.*)?$")


def _normalize_msys_drive(s: str) -> str:
    """On Windows, translate a Git-Bash/MSYS2-style ``/c/Users/...`` path to
    ``C:/Users/...`` before handing it to ``pathlib``.

    Windows' Bash tool commonly runs through Git-Bash/MSYS2, whose runtime
    translates ``/c/...`` to the real ``C:\\...`` path at execution time —
    but this hook is a separate Python subprocess with no MSYS runtime, so
    without this translation ``Path("/c/Users/.../forge/policy.json")``
    resolves relative to "root of the current drive" (garbage, never equal
    to the real protected path), and the write sails through undetected.
    POSIX is unaffected (a real ``/c/...`` there is a normal absolute path
    starting at the filesystem root, left untouched)."""
    if not sys.platform.startswith("win"):
        return s
    m = _MSYS_DRIVE_RE.match(s)
    if not m:
        return s
    drive, rest = m.group(1), (m.group(2) or "/")
    return f"{drive.upper()}:{rest}"


# A backslash acting as a Windows PATH SEPARATOR, i.e. one that is NOT the
# start of a POSIX escape sequence we must preserve. `\ `, `\\`, `\'`, `\"`,
# `\$` and `` \` `` are genuine shell escapes even on Windows; a backslash
# before anything else (a letter, digit, `.`, `-`, `~`, …) is a path
# separator in `C:\Users\...`.
_WIN_PATH_SEP_RE = re.compile(r"\\(?=[^\s\\'\"$`])")
# Placeholder for those separators while shlex runs: a Unicode Private Use
# Area code point, which shlex treats as an ordinary word character and which
# carries no meaning in any real command. Deliberately NOT NUL — NUL-poisoned
# paths are their own (still-open) fail-closed problem in this module, and the
# two must not become entangled. Any pre-existing sentinel in the input is
# stripped before substitution so it can never be smuggled in to forge a
# separator; dropping the character can only ever shorten a token, never turn
# a protected path into an unprotected one.
_WIN_SEP_SENTINEL = "\ue000"


def _split_tokens(seg: str) -> list[str]:
    """``shlex.split(seg, posix=True)``, but backslash-safe on Windows.

    2026-08-06, found via the boot-time self-test on a live Windows install:
    nine curated must-deny vectors (`tee`, `sed -i`, `truncate`, `rm`,
    `chmod`, `ln`, `cp -t`, …) were being ALLOWED. Root cause: every path
    extractor here tokenises with POSIX shlex, which treats ``\\`` as an
    escape character and simply DROPS it — so

        tee -a C:\\Users\\me\\.corvin\\global\\forge\\audit.jsonl

    tokenises to ``C:Usersme.corvinglobalforgeaudit.jsonl``, which resolves
    to nothing protected, and the gate lets a write to the audit chain
    through. The redirect vectors (``>``, ``>|``, ``2>``) were unaffected
    because _REDIRECT_RE matches the raw string and never sees shlex.

    Substituting the separators for a sentinel that shlex passes through
    untouched keeps quoting, whitespace splitting and token POSITIONS
    byte-identical to before — call sites depend on those positions
    (``toks[0]`` is the command name, sed -i takes the last positional).
    POSIX hosts take the original path unchanged; there a backslash really
    is an escape and paths use ``/``.

    Companion to _normalize_msys_drive(), which covers the other shape a
    Windows Bash path arrives in (Git-Bash/MSYS2 ``/c/Users/...``)."""
    if not sys.platform.startswith("win"):
        return shlex.split(seg, posix=True)
    seg = seg.replace(_WIN_SEP_SENTINEL, "")  # no smuggled separators
    toks = shlex.split(_WIN_PATH_SEP_RE.sub(_WIN_SEP_SENTINEL, seg), posix=True)
    return [t.replace(_WIN_SEP_SENTINEL, "\\") for t in toks]


def _abs(p: str | Path) -> Path:
    """Best-effort absolute resolution. Non-existent paths are fine.

    Expands ~ and $VARS FIRST (R4): a Bash redirect like
    ``: > ~/.config/corvin-voice/secrets.json`` is non-absolute as a literal
    string, so it would otherwise resolve under cwd and dodge EVERY
    protected-path check — yet the shell expands ~ at run time and truncates the
    real file. expandvars covers ``$HOME``/``${XDG_CONFIG_HOME}`` forms; command
    substitution ($(...)/backticks) is already fail-closed in _bash_targets."""
    pp = Path(_normalize_msys_drive(os.path.expanduser(os.path.expandvars(str(p)))))
    if _IS_WIN:
        pp = Path(_win_canonical_str(str(pp)))
    if not pp.is_absolute():
        pp = Path.cwd() / pp
    try:
        pp = pp.resolve(strict=False)
    except (OSError, RuntimeError):
        pass
    if _IS_WIN:
        # Case-folded (NTFS is case-insensitive): a not-yet-existing leaf such
        # as ``forge\POLICY.JSON`` keeps the caller's case through resolve(),
        # and every protected-name test below compares lowercase literals.
        pp = Path(os.path.normcase(_win_strip_aliases(_win_canonical_str(str(pp)))))
    return pp


_IS_WIN = sys.platform.startswith("win")
_WIN_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1", "0:0:0:0:0:0:0:1"})
_WIN_ADMIN_SHARE_RE = re.compile(r"^\\\\([^\\]+)\\([A-Za-z])\$(\\.*)?$")


def _win_canonical_str(s: str) -> str:
    """Windows only: rewrite the alternative spellings of a local drive path to
    the plain ``C:\\...`` form, so prefix checks against the protected roots
    see them. Without this ``\\\\?\\C:\\...\\forge\\policy.json`` (Win32
    extended-length form, kept verbatim by ``resolve()``) and
    ``\\\\localhost\\C$\\...`` (loopback admin share) both named a protected file
    yet matched no root, and the write went through. Handles ``\\\\?\\``,
    ``\\\\.\\``, ``\\\\?\\UNC\\<host>\\...`` and loopback ``<host>\\X$`` shares."""
    if s[:4] in ("\\\\?\\", "\\\\.\\"):
        rest = s[4:]
        s = "\\\\" + rest[4:] if rest[:4].upper() == "UNC\\" else rest
    m = _WIN_ADMIN_SHARE_RE.match(s)
    if m:
        host = m.group(1).lower()
        try:
            import socket  # noqa: PLC0415
            local = {socket.gethostname().lower()}
        except Exception:  # noqa: BLE001
            local = set()
        if host in _WIN_LOOPBACK_HOSTS or host.startswith("127.") or host in local:
            s = f"{m.group(2).upper()}:{m.group(3) or chr(92)}"
    return s


def _win_strip_aliases(s: str) -> str:
    """Windows only: drop what Win32 ignores when it opens a not-yet-existing
    path — trailing dots/spaces of a component (``audit.jsonl.`` creates
    ``audit.jsonl``) and an NTFS stream suffix (``policy.json::$DATA`` IS the
    file's content). ``resolve()`` canonicalises these only for existing paths."""
    drive, rest = os.path.splitdrive(s)
    out = []
    for c in rest.split("\\"):
        if c not in (".", ".."):
            c = c.split(":", 1)[0].rstrip(" .") or c
        out.append(c)
    return drive + "\\".join(out)


def _ref(p: str | Path) -> str:
    """A protected ROOT/file in the same comparison form ``_abs`` gives the
    target: unchanged ``str(p)`` on POSIX; on Windows resolved, de-prefixed and
    case-folded (``CORVIN_HOME=c:\\users\\...`` or an 8.3 short name used to
    make every ``startswith(home)`` test miss)."""
    return str(_abs(p)) if _IS_WIN else str(p)


def _is_personal_kb_runtime(parts: tuple[str, ...]) -> bool:
    """The runtime store of the personal knowledge base: ``<home>/tenants/<tid>/personal_kb/**`` or its
    device-key root ``<home>/secrets/personal_kb/**`` (ADR-2325 item 5). The SOURCE package
    ``core/personal_kb`` and ``tests/personal_kb`` are not runtime stores. Writes only: this gate does
    not see reads."""
    for i, c in enumerate(parts):
        if c != "personal_kb" or i < 1:
            continue
        if parts[i - 1] == "secrets":
            return True
        if i >= 2 and parts[i - 2] == "tenants":
            return True
    return False


def _is_ledger_store_path(path: str | Path) -> bool:
    """A path inside a ledger-class store: the session ledger (and its pre-R5
    in-workdir name), the CEL anchor store, the completion queue, the console's
    turn logs (directory or file). Never raises."""
    try:
        parts = _abs(path).parts
    except Exception:  # noqa: BLE001 — unresolvable → treat as protected
        return True
    return (any(c in parts for c in ("session_ledger", ".corvin-ledger", "cel_anchors",
                                     "pending_notifications"))
            or _is_personal_kb_runtime(parts)
            or ("web_chat" in parts and (str(path).endswith(".turns.jsonl") or "sessions" in parts)))


#: Learning state a tenant keeps directly under ``<tenant>/global/`` (not under
#: learning/ or skills/). A worker writing them forged operator CEL-stage grades
#: (a non-builtin stage became default-eligible, L07), reset the ADR-0760 epoch
#: that the ADR-2259 Free-tier savings meter counts from, or rewrote the L08
#: learner's statistics (review 2026-11-05 LRN-10). Their lock / temp siblings
#: (``<name>.lock``, ``<name>.<x>.tmp``) are covered too.
_TENANT_GLOBAL_LEARNING_FILES = (
    "ce_stage_grades.json",
    "usage_epoch.json",
    "model_confidence_stats.json",
)


def _is_tenant_global_learning_name(name: str) -> bool:
    return any(name == f or name.startswith(f + ".") or name.startswith("." + f + ".")
               for f in _TENANT_GLOBAL_LEARNING_FILES)


def _is_tenant_learning_rel(rel_parts: list[str]) -> bool:
    """``rel_parts`` (relative to the corvin home) names
    ``tenants/<tid>/{learning,skills}/…`` or one of the learning-state files in
    ``tenants/<tid>/global/`` — anchored, so a ``learning`` folder in a session
    workdir does not match."""
    if len(rel_parts) >= 3 and rel_parts[0] == "tenants" and rel_parts[2] in ("learning", "skills"):
        return True
    return (len(rel_parts) == 4 and rel_parts[0] == "tenants" and rel_parts[2] == "global"
            and _is_tenant_global_learning_name(rel_parts[3]))


def _is_tenant_learning_path(path: str | Path) -> bool:
    """A path inside a tenant's learning state / learned Skill configs. Never raises
    (unresolvable → protected). Used by the create-command branch of
    ``_bash_targets`` as well as ``is_protected_path``: that branch checks only
    the anchored stores, and without this ``patch``/``xxd -r``/``split``/``touch``
    rewrote ``rollback_state.json`` or the router config unchecked (refutation
    2026-11-05)."""
    try:
        abs_str = str(_abs(path))
        home_str = _ref(_corvin_home())
    except Exception:  # noqa: BLE001
        return True
    sep = os.sep
    if not abs_str.startswith(home_str + sep):
        return False
    return _is_tenant_learning_rel(abs_str[len(home_str):].lstrip(sep).split(sep))


#: Out-of-tree audit-chain records kept beside the anchor key
#: (``security_events``): the path-keyed chain identity, the tail anchors and
#: the per-chain MAC markers. Deleting one of them turns a replaced or truncated
#: chain back into one that verifies (review 2026-11-19 SEC-3).
_CHAIN_MARKER_DIRS = ("chain_ids", "chain_tails", "mac_active_chains",
                      # Round 2 (R2-SEC-4): the per-path chain witness (its retired
                      # generations included). Removing it re-opens the window the
                      # witness exists to close.
                      "chain_witness",
                      # ...and where `forge.chain_loss` moves a lost chain's identity
                      # record and witness: the only remaining proof of that chain.
                      "chain_ids_retired", "chain_witness_retired")
#: Files beside the anchor key that belong to the chain's integrity.
_CHAIN_KEY_FILE_PREFIXES = ("audit_anchor", "audit_mac_active")


def _anchor_key_path() -> Path:
    """Mirror of ``security_events._anchor_key_path`` (import-free hook)."""
    env = os.environ.get("CORVIN_AUDIT_ANCHOR_KEY", "").strip()
    return (Path(os.path.expanduser(env)) if env
            else Path(os.path.expanduser("~/.config/corvin-voice/audit_anchor.key")))


def _audit_key_dirs() -> list[str]:
    """Every directory that can hold the anchor key and its marker trees, in
    ``_abs`` comparison form: the configured key's parent, the default
    location, the XDG voice dir and the vault's parent."""
    out: list[str] = []
    cfg_base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    for cand in (lambda: _anchor_key_path().parent,
                 lambda: Path(os.path.expanduser("~/.config/corvin-voice")),
                 lambda: Path(os.path.expanduser(cfg_base)) / "corvin-voice",
                 lambda: _vault_path().parent):
        try:
            r = str(_abs(cand())).rstrip(os.sep)
        except Exception:  # noqa: BLE001
            continue
        if r and r not in out:
            out.append(r)
    return out


def _is_audit_integrity_path(path: str | Path) -> bool:
    """The hash-chained audit trail and the out-of-tree records that anchor it:
    any ``audit.jsonl`` (and its rotation / lock siblings) under the corvin
    home, the anchor key, the MAC sentinel, and ``chain_ids/`` /
    ``chain_tails/`` / ``mac_active_chains/`` beside the key. Same class as the
    session ledger: no command but a read may name one. Never raises
    (unresolvable -> protected)."""
    try:
        abs_p = _abs(path)
        abs_str = str(abs_p)
        home_str = _ref(_corvin_home())
    except Exception:  # noqa: BLE001
        return True
    sep = os.sep
    name = abs_p.name
    if abs_str.startswith(home_str + sep) and (name == "audit.jsonl" or name.startswith("audit.jsonl.")):
        return True
    try:
        if abs_str == str(_abs(_anchor_key_path())):
            return True
    except Exception:  # noqa: BLE001
        return True
    for d in _audit_key_dirs():
        if not abs_str.startswith(d + sep):
            continue
        rel = abs_str[len(d):].lstrip(sep).split(sep)
        if rel and rel[0] in _CHAIN_MARKER_DIRS:
            return True
        if len(rel) == 1 and rel[0].startswith(_CHAIN_KEY_FILE_PREFIXES):
            return True
    return False


def _is_strict_store_path(path: str | Path) -> bool:
    """Ledger-class OR audit-integrity path: the stores where any non-read
    command naming the path fails closed."""
    return _is_ledger_store_path(path) or _is_audit_integrity_path(path)


#: Repo-relative source files whose content IS a gate: the audit writer and its
#: allowlists, the three ``tenant_audit_chain`` mirrors (a redirected mirror
#: moves every record out of the verified chain) and the boot tripwire. A
#: worker rewriting one of them disables the mechanism without touching the
#: chain. Operator edits arrive through git (a worktree + merge), never through
#: a session's write tool (review 2026-11-19 SEC-3).
_PROTECTED_REPO_FILES = (
    ("corvin_operator", "forge", "forge", "security_events.py"),
    ("corvin_operator", "forge", "forge", "paths.py"),
    ("corvin_operator", "bridges", "shared", "paths.py"),
    ("core", "paths", "tenant.py"),
    ("core", "paths", "__init__.py"),
    ("core", "compliance", "corvin_compliance_reports", "tripwire.py"),
    # Round 3 (R3-SEC-10): the pre-spawn gate dispatcher (L34 / L35 / L44 call
    # sites) and the house-rules policy. A session that rewrites either one in
    # its own checkout switches a gate off without touching the gate itself.
    ("corvin_operator", "bridges", "shared", "spawn_gates.py"),
    ("corvin_operator", "policy", "house_rules.yaml"),
)
#: Repo-relative trees protected as a whole: the hook directory (this gate, the
#: launcher, hooks.json and the sibling gates).
_PROTECTED_REPO_TREES = (
    ("corvin_operator", "voice", "hooks"),
)


def _code_roots() -> list[Path]:
    """Checkouts whose gate sources this hook protects: the checkout the hook
    file sits in, and - when the hook runs from the plugin cache - the checkout
    that contains the live runtime root (``<repo>/.corvin``)."""
    roots: list[Path] = []
    repo = _repo_root()
    if repo is not None:
        roots.append(repo)
    try:
        parent = _corvin_home().parent
        if (parent / ".corvin_repo").exists() and parent not in roots:
            roots.append(parent)
    except Exception:  # noqa: BLE001
        pass
    return roots


def _is_gate_source_path(abs_p: Path, abs_str: str) -> bool:
    sep = os.sep
    # The directory this very file runs from (the plugin-cache copy included):
    # the gate, its launcher and hooks.json.
    try:
        self_dir = _ref(Path(__file__).resolve().parent)
        if abs_str == self_dir or abs_str.startswith(self_dir + sep):
            return True
    except Exception:  # noqa: BLE001
        return True
    for root in _code_roots():
        for rel in _PROTECTED_REPO_FILES:
            if abs_str == _ref(root.joinpath(*rel)):
                return True
        for rel in _PROTECTED_REPO_TREES:
            tree = _ref(root.joinpath(*rel))
            if abs_str == tree or abs_str.startswith(tree + sep):
                return True
    return False


def _claude_config_dirs() -> list[str]:
    """``~/.claude`` and ``CLAUDE_CONFIG_DIR`` in ``_abs`` comparison form."""
    out: list[str] = []
    cands = [Path(os.path.expanduser("~/.claude"))]
    env = os.environ.get("CLAUDE_CONFIG_DIR", "").strip()
    if env:
        cands.append(Path(os.path.expanduser(env)))
    for d in cands:
        try:
            r = str(_abs(d)).rstrip(os.sep)
        except Exception:  # noqa: BLE001
            continue
        if r and r not in out:
            out.append(r)
    return out


def _project_roots() -> list[str]:
    """Directories whose ``.claude/`` Claude Code reads as PROJECT settings for
    this session: the hook's working directory (the worker's cwd) and the
    checkout(s) whose gate sources this hook protects."""
    out: list[str] = []
    cands: list = [lambda: Path(os.getcwd())]
    cands.extend((lambda r=r: r) for r in _code_roots())
    for c in cands:
        try:
            r = str(_abs(c())).rstrip(os.sep)
        except Exception:  # noqa: BLE001
            continue
        if r and r not in out:
            out.append(r)
    return out


def _is_claude_user_settings(abs_p: Path) -> bool:
    """A Claude Code settings file that can register - or unregister - this hook.

    * ``~/.claude/settings*.json`` (or under ``CLAUDE_CONFIG_DIR``);
    * round 2 (R2-SEC-3): ``settings*.json`` in ANY ``.claude/`` directory at,
      below or above the worker's cwd and below the checkout root. Project and
      local settings (``<project>/.claude/settings.json`` /
      ``settings.local.json``) are merged over the user file: a worker that
      writes ``{"disableAllHooks": true}`` or its own ``hooks`` block there
      removes the gate for the next tool call of the next session, and only the
      user file was protected.

    A worker write there removes the gate. Never raises (unresolvable -> True)."""
    name = abs_p.name
    if not (name.startswith("settings") and name.endswith(".json")):
        return False
    try:
        parent = str(abs_p.parent).rstrip(os.sep)
        if parent in _claude_config_dirs():
            return True
        if abs_p.parent.name != ".claude":
            return False
        proj = str(abs_p.parent.parent).rstrip(os.sep) or os.sep
        sep = os.sep
        for root in _project_roots():
            if (proj == root or proj.startswith(root + sep)      # at / below cwd or checkout
                    or root.startswith(proj.rstrip(sep) + sep)):  # an ancestor of it
                return True
    except Exception:  # noqa: BLE001
        return True
    return False


def _is_claude_plugins_path(abs_str: str) -> bool:
    """``~/.claude/plugins/**`` (round 2, R2-SEC-3): the plugin cache holds the
    COPY of this hook that an installed ``voice`` plugin actually runs, its
    launcher and ``hooks.json``, plus ``installed_plugins.json`` /
    ``known_marketplaces.json`` that decide which copy is loaded."""
    sep = os.sep
    for d in _claude_config_dirs():
        tree = d + sep + "plugins"
        if abs_str == tree or abs_str.startswith(tree + sep):
            return True
    return False


def _guard_roots() -> list[str]:
    """Directories whose removal or move AS A WHOLE (or of an ancestor) takes a
    guard with it although no protected leaf is named: the anchor-key
    directories (wherever ``CORVIN_AUDIT_ANCHOR_KEY`` points - round 2,
    R2-SEC-4), the Claude config dirs and the project ``.claude/`` dirs
    (R2-SEC-3). ``rm -rf ~/.claude``, ``mv <key dir> /tmp/x`` and
    ``rm -rf <cwd>/.claude`` named nothing ``is_protected_path`` knows."""
    out: list[str] = []
    for r in _audit_key_dirs() + _claude_config_dirs() + [
            pr + os.sep + ".claude" for pr in _project_roots()]:
        r = r.rstrip(os.sep)
        if r and r not in out:
            out.append(r)
    return out


def _removes_guard_root(abs_str: str) -> bool:
    """True when *abs_str* IS a guard root or an ancestor of one."""
    sep = os.sep
    abs_str = abs_str.rstrip(sep) or sep
    for g in _guard_roots():
        if abs_str == g or g.startswith(abs_str.rstrip(sep) + sep):
            return True
    return False


def is_protected_path(path: str | Path) -> bool:
    """True if writing to *path* should be blocked."""
    if not path:
        return False
    abs_p = _abs(path)
    abs_str = str(abs_p)
    sep = os.sep

    # ADR-2102 — a chat's session ledger. The worker must never WRITE it: a
    # forged line is re-supplied into every later system prompt as "the user
    # said", and a forged /new fence or a deleted line makes the chat forget.
    # It lives OUTSIDE the worker's cwd (``session_ledger/``) since review R5,
    # so cwd-relative globs, ``$PWD`` and ``find .`` cannot reach it; the old
    # in-cwd name stays protected for ledgers not yet migrated. Wherever the
    # tree is (tenant home, legacy voice root), the component decides.
    if "session_ledger" in abs_p.parts or ".corvin-ledger" in abs_p.parts:
        return True
    # The pending-notification queue: a forged record is delivered to a chat AND
    # recorded in its session ledger as the assistant's answer (review R7-3).
    if "pending_notifications" in abs_p.parts:
        return True
    # The CEL anchor store (re-injected every turn as "always honor these",
    # review R8-CEL-2) and the console's ledger — its per-chat turn logs are
    # what the console re-supplies (R8-CEL-5).
    if "cel_anchors" in abs_p.parts:
        return True
    if "web_chat" in abs_p.parts and abs_p.name.endswith(".turns.jsonl"):
        return True
    # ADR-2325 item 5 — the encrypted personal knowledge base and its device keys. A worker write
    # could plant a ciphertext, a revocation sentinel or an index binding. Writes only.
    if _is_personal_kb_runtime(abs_p.parts):
        return True
    # Review 2026-11-19 SEC-3 - the audit chain's out-of-tree anchors, the gate
    # sources and the settings file that registers this hook.
    if _is_audit_integrity_path(abs_p):
        return True
    if _is_gate_source_path(abs_p, abs_str):
        return True
    if _is_claude_user_settings(abs_p):
        return True
    if _is_claude_plugins_path(abs_str):
        return True

    home = _corvin_home()
    home_str = _ref(home)
    if abs_str == home_str or abs_str.startswith(home_str + sep):
        rel_parts = abs_str[len(home_str):].lstrip(sep).split(sep)
        if "forge" in rel_parts or "skill-forge" in rel_parts:
            return True
        # ADR-0013 — compute-worker Tier-2 artefact tree + Unix socket.
        # The LLM must not direct-write iter logs, summary, manifest or
        # the worker.sock; the MCP path (worker daemon) is the sole
        # supported entry. The forged tool inside bwrap reads/writes
        # via Forge's runner, not via Claude's Bash/Write/Edit gate.
        if "compute" in rel_parts:
            return True
        # ADR-2092 / ADR-2281 — a tenant's learning state and learned Skill
        # configs: the routing ledger, the rollback judge's state/pending files
        # (Phase-2 readiness gates), learned thresholds, the event store, and
        # the SkillAdapter configs (e.g. os_delegation_router_config.json). A
        # worker deleting rollback_state.json cleared a tripped rollback without
        # the audited operator reset, and 500 appended ledger lines passed the
        # readiness gate (review 2026-11-05 DR-3). Only the learning code writes
        # here. Anchored at <home>/tenants/<tid>/{learning,skills}/ so a project
        # directory named "learning" inside a session workdir stays writable.
        if _is_tenant_learning_rel(rel_parts):
            return True
        # ADR-0014 — admin-UI plugin operator-token store + session
        # records + CSRF secrets. Operator-managed via the plugin's
        # CLI / auth module; an LLM-side overwrite could mint a fake
        # operator token, impersonate an existing admin session, or
        # rewrite CSRF secrets to defeat mutation-side protections.
        if "admin" in rel_parts:
            return True
        # ADR-0016 — Layer 28 memory tree (recall.db + user_model/).
        # Recall index + user-model JSON are operator-only on disk;
        # the LLM reaches them via the conversation_recall / user_model
        # Python API, the MCP recall tool, or the /recall slash-cmd.
        # Direct Write/Edit/Bash would let a forged tool poison the
        # index, fake recall hits, or rewrite the user-model spec.
        if "memory" in rel_parts:
            return True
        # ADR-0017 Phase III — license-gate tree (license.jwt + state.json).
        # Operator-only via the corvin-license CLI. An LLM-side write
        # could install a self-signed license, defeat the grace-period
        # state machine, or wipe revocation history. The pubkey is in
        # the plugin source tree (separately protected by the existing
        # forge subdir rule below).
        if "license" in rel_parts:
            return True
        # R4: the validator's authoritative token discovery (_find_token) also
        # reads <corvin_home>/global/license.key — which is NOT under a license/
        # dir, so the element-membership check above misses it. Guard it
        # explicitly (an LLM-side wipe/garble forces a free-tier downgrade / DoS
        # until the refresh daemon rewrites it — fail-safe, but still operator-only).
        if rel_parts and rel_parts[-1] == "license.key":
            return True
        # ADR-0145 M1 — instance signing key + IBC. The Ed25519 private key
        # (instance_key.pem) and the IBC JWT (instance_cert.jwt) must be
        # operator-only. An LLM-side overwrite of the private key would allow
        # identity forgery in every A2A envelope; an overwrite of the IBC would
        # let an attacker install an arbitrary (self-crafted) cert bypassing the
        # Corvin Labs RS256 trust anchor. Rotation is via `corvin instance rotate-key`.
        last = rel_parts[-1] if rel_parts else ""
        if last in ("instance_key.pem", "instance_cert.jwt", "instance_pubkey.pem"):
            return True
        # ADR-0032 — AWPKG installed-package tree. Direct writes would let
        # an LLM overwrite installed Forge tools, Skills, or Personas without
        # going through the installer's pre-extraction safety checks.
        # Only the awpkg installer (CLI / MCP tool) may write here.
        if "packages" in rel_parts:
            return True
        last = rel_parts[-1] if rel_parts else ""
        if last in ("audit.jsonl", "policy.json"):
            return True
        # ADR-0012 — data_policy.yaml / .json is operator-only. A
        # forged tool that overwrites this file could flip every PII
        # strategy to "drop" and disable snapshot redaction silently.
        # Operator edits go through their normal text editor, never
        # via the LLM's Write / Edit / Bash tool calls.
        if last in ("data_policy.yaml", "data_policy.yml", "data_policy.json"):
            return True
        # Layer 29.4a — tenant.corvin.{yaml,yml,json} pins the
        # data_residency zone + allowed/forbid_engines that gate
        # Layer-29 delegation. An LLM-side overwrite would bypass
        # that. Operator edits go through the gateway CLI / text editor.
        if last in ("tenant.corvin.yaml", "tenant.corvin.yml",
                    "tenant.corvin.json"):
            return True
        # ADR-0013 — explicit socket-path protection (redundant given
        # the "compute" subdir check above but listed explicitly so
        # the intent is scrutable to the next reader).
        if last == "worker.sock":
            return True
        # ADR-0021 Layer 31 — supply-chain operator policy + cache.
        # LLM overwrite would silently disable CVE-surveillance or
        # rewrite the frozen-baseline dep-hash set. Operator-only via
        # text editor.
        if "supply_chain" in rel_parts:
            return True
        if last in ("supply_chain.yaml", "supply_chain.yml",
                    "supply_chain.json"):
            return True

    repo = _repo_root()
    if repo is not None:
        slot = repo / "corvin_operator" / "skill-forge" / "skills" / "dyn"
        slot_str = _ref(slot)
        if abs_str == slot_str or abs_str.startswith(slot_str + sep):
            return True
        bundled_policy = _ref(repo / "corvin_operator" / "forge" / "forge" / "policy.json")
        if abs_str == bundled_policy:
            return True
        bundled_default = _ref(repo / "corvin_operator" / "forge" / "forge" / "policy.default.json")
        if abs_str == bundled_default:
            return True
        # ADR-0143 Layer 44 — house-rules acceptable-use policy + its gate module.
        # An LLM-side write to operator/policy/house_rules.yaml (weaken the rules)
        # or to operator/bridges/shared/house_rules.py (which holds the
        # EXPECTED_POLICY_SHA256 integrity anchor + the gate logic) would defeat
        # the L44 acceptable-use guarantee without touching the audit chain.
        # Operator-side edits happen via git, not an in-process write tool.
        house_rules_policy = _ref(repo / "corvin_operator" / "policy" / "house_rules.yaml")
        house_rules_mod = _ref(repo / "corvin_operator" / "bridges" / "shared" / "house_rules.py")
        if abs_str in (house_rules_policy, house_rules_mod):
            return True
        # ADR-0093 M1.3 — license plugin source tree (verifier.py, tier_flags.py,
        # and any other module under core/license/corvin_license/). An LLM-side
        # patch to these files would bypass has_flag() / _verify_ed25519() locally
        # without touching the audit chain. Block the entire subdirectory.
        license_src = repo / "core" / "license" / "corvin_license"
        license_src_str = _ref(license_src)
        if abs_str == license_src_str or abs_str.startswith(license_src_str + sep):
            return True
        # Also protect the CorvinOS-side validator and limits modules under
        # operator/license/ — patching these has the same effect.
        # ADR-0144 SEAL-SHADOW-01: the original ``.py``-only restriction left the
        # seal root-of-trust unguarded. seal_loader.py PREFERS a native extension
        # (_corvin_seal.so/.dylib/.pyd) and exec_module's it with NO integrity
        # check, and validator/attestation trust the embedded a2a_network_pubkey.pem.
        # An LLM-side drop of a malicious .so (shadowing the verifier) or a
        # rewritten .pem (forging the attestation anchor) bypasses the gate that
        # blocks the equivalent .py edit. Block code + trust-anchor suffixes.
        op_license = repo / "corvin_operator" / "license"
        op_license_str = _ref(op_license)
        if abs_str.startswith(op_license_str + sep) and abs_p.suffix in (
            ".py", ".so", ".dylib", ".pyd", ".pem",
        ):
            return True
        # ADR-0021 Layer 31 — supply-chain manifest files at the plugin
        # tree root. An LLM-side write would let a forged tool rewrite
        # dep hashes (defeating the integrity check), edit plugin
        # capability declarations (silencing the drift detector), or
        # poison the SBOM (paper-trail forgery). Operator-side updates
        # via `pip-compile --upgrade --generate-hashes`, npm tooling,
        # or `python -m corvin_gateway.cli sbom build`.
        # Supply-chain manifests live under core/ and corvin_operator/ (was plugins/ before ADR-0035)
        _in_plugin_tree = any(
            abs_str.startswith(_ref(repo / _d) + sep)
            for _d in ("core", "corvin_operator", "plugins")
        )
        if _in_plugin_tree:
            _abs_last = abs_p.name
            if _abs_last in ("requirements.txt", "package-lock.json",
                              "sbom.cdx.json", "plugin.corvin.yaml",
                              "plugin.corvin.yml"):
                return True

    # Layer-16 v3 — secret vault at ~/.config/corvin-voice/secrets.json
    # (or wherever CORVIN_SECRET_VAULT points). Operator-only file: the LLM must
    # never WRITE to it (would let a forged tool plant a rogue key). NOTE: this
    # gate is a WRITE-protection boundary (L10) — it does NOT block *reads*
    # (`cat`/`less`/`grep` extract no write target and are allowed). Read-side
    # confinement of the vault is out of scope here and belongs to the sandbox /
    # tool-allowlist layer, not this path-gate (corrected round-3: the prior
    # comment overclaimed read-denial that this function never enforced).
    vault = _vault_path()
    vault_str = _ref(vault)
    if abs_str == vault_str:
        return True
    # Also protect the parent dir against bulk-write tricks like
    # ``cp /dev/stdin ~/.config/corvin-voice/`` (the destination becomes
    # a path under the dir, so `is_protected_path(parent_dir)` alone is
    # not enough — the bash extractor will hand us the full target path
    # and we cover it via the secrets.json equality check above plus
    # the prefix check that follows).
    # R4: session.key (the validator's authoritative license-token cache, read
    # by _find_token) lives in the same ~/.config/corvin-voice/ dir and is just
    # as operator-only as secrets.json — an LLM-side wipe forces a license
    # downgrade / DoS until session_refresh rewrites it.
    if abs_str.startswith(_ref(vault.parent) + sep) and \
            abs_p.name in ("secrets.json", "session.key"):
        return True
    # R4 addendum: when CORVIN_SECRET_VAULT points to a custom path, vault.parent
    # moves away from the canonical XDG dir, leaving session.key unprotected.
    # Add an independent guard anchored to XDG_CONFIG_HOME so session.key is
    # always protected regardless of where the vault is configured.
    _cfg_base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    _xdg_cv = Path(os.path.expanduser(_cfg_base)) / "corvin-voice"
    if abs_p.parent == _xdg_cv and abs_p.name in ("secrets.json", "session.key"):
        return True

    # Layer 10 v2 / 29.3a hardening — cowork persona JSONs at every tier
    # carry the delegate_output_judge_mode + delegate_sandbox_mode +
    # delegate_prompt_safety_mode security floors. An LLM with
    # Write/Edit could otherwise rewrite its own persona JSON to
    # weaken the floors. Same threat for bridge per-channel
    # settings.json which carries chat_profile blocks (whitelist +
    # per-chat overrides). Both are operator-managed via text editor.
    parts = abs_p.parts
    if (len(parts) >= 3
            and abs_p.suffix == ".json"
            and parts[-2] == "personas"
            and parts[-3] in ("cowork", "claude-cowork")):
        return True
    if (len(parts) >= 3
            and abs_p.name == "settings.json"
            and parts[-3] == "bridges"):
        return True

    return False


# ----- Python AST gate (Gate 1 / Gate 4) ----------------------------------

# NOTE (R3): this AST gate is best-effort DEFENSE-IN-DEPTH, NOT a complete
# code-exec sandbox — a blocklist over a Turing-complete language can always be
# obfuscated around (marshal, string-built names, etc.). The LOAD-BEARING
# protections are: (a) the write-target detection below (redirects/tee/mv/cp/
# sed-i/open()/heredoc-with-hint) which is what actually guards protected paths,
# and (b) for FORGED TOOLS, the bwrap sandbox + forbidden_imports + sitecustomize
# loopback-deny. This gate closes the cheap, named indirections; it is not a
# claim of complete inline-code containment.
_AST_BLOCKLIST_IMPORTS: frozenset[str] = frozenset({
    # network
    "socket", "ssl", "urllib", "http", "ftplib", "smtplib",
    "imaplib", "poplib", "xmlrpc", "telnetlib",
    # subprocess / process control
    "subprocess", "pty", "tty",
    # native code / FFI
    "ctypes", "cffi",
    # dynamic import / deserialization-exec (R3: importlib.import_module was an
    # uninspected route to subprocess/os; marshal/pickle execute arbitrary code)
    "importlib", "marshal",
})

_AST_BLOCKLIST_BUILTINS: frozenset[str] = frozenset({
    "eval", "exec", "compile", "__import__",
})

# Indirection helpers that are blocked ONLY when used to reach builtins/modules
# by name (e.g. getattr(__builtins__, "eval")). Plain getattr on ordinary
# objects stays allowed to avoid false-positives on legitimate code.
_AST_INDIRECTION_FUNCS: frozenset[str] = frozenset({"getattr", "vars"})
_AST_INDIRECTION_TARGETS: frozenset[str] = frozenset({"__builtins__", "builtins", "__import__"})

_AST_BLOCKLIST_ATTRS: frozenset[str] = frozenset({
    # os process execution
    "system", "popen", "execve", "execle", "execlp",
    "execvp", "execvpe", "fork", "forkpty",
    # subprocess
    "check_call", "check_output", "Popen",
    # filesystem destruction
    "rmtree",
    # dynamic import (R3: importlib.import_module / loader.exec_module bypass)
    "import_module", "exec_module", "load_module",
})

# Detect `python3 -c 'code'` or `python3 -c "code"` (no nested quotes).
_PY_C_SINGLE_RE = re.compile(
    r"\bpython3?\b[^|;&\n]{0,40}-c\s+'([^']+)'"
)
_PY_C_DOUBLE_RE = re.compile(
    r'\bpython3?\b[^|;&\n]{0,40}-c\s+"([^"]+)"'
)
# Detect `python3 /path/to/file.py` (optional flags before the file).
_PY_FILE_RE = re.compile(
    r"\bpython3?\s+(?:-[a-zA-Z]+\s+)*([^\s|;&]+\.py\b)"
)

_PY_FILE_SIZE_LIMIT = 256 * 1024  # 256 KiB — refuse to analyse huge files

# R2 finding: inline code fed to python via stdin (bypassing the -c AST gate).
# Piped: echo '<code>' | python   /  printf '<code>' | python3
_PY_PIPE_RE = re.compile(
    r"""\b(?:echo|printf)\b\s+(['"])(.+?)\1\s*\|\s*python3?\b""", re.DOTALL
)
# Heredoc: python <<'EOF' ... EOF  (capture the body between the delimiters)
_PY_HEREDOC_RE = re.compile(
    r"\bpython3?\b[^\n]*<<-?\s*['\"]?(\w+)['\"]?\s*\n(.*?)\n\1\b", re.DOTALL
)


def _python_ast_check(code: str) -> tuple[bool, str]:
    """Return (safe, reason). True = code is safe to execute.

    Fail-closed on SyntaxError: if we cannot parse it, we cannot vouch
    for it. The caller emits code.exec_blocked with the parse error.
    """
    import ast as _ast

    try:
        tree = _ast.parse(code)
    except SyntaxError as exc:
        return False, f"AST parse error (deny-on-unparse): {exc}"

    for node in _ast.walk(tree):
        if isinstance(node, _ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _AST_BLOCKLIST_IMPORTS:
                    return False, f"forbidden import: {alias.name!r}"
        elif isinstance(node, _ast.ImportFrom):
            mod = node.module or ""
            top = mod.split(".")[0]
            if top in _AST_BLOCKLIST_IMPORTS:
                return False, f"forbidden from-import: {mod!r}"
        elif isinstance(node, _ast.Call):
            if isinstance(node.func, _ast.Name):
                if node.func.id in _AST_BLOCKLIST_BUILTINS:
                    return False, f"forbidden builtin: {node.func.id}()"
                # R3: getattr(__builtins__, "eval") / vars(builtins)[...] reach a
                # blocked builtin by name to dodge the literal-name check above.
                # Block ONLY the builtins-indirection form (first arg is
                # __builtins__/builtins/__import__) so ordinary getattr is fine.
                if node.func.id in _AST_INDIRECTION_FUNCS and node.args:
                    a0 = node.args[0]
                    if (isinstance(a0, _ast.Name) and a0.id in _AST_INDIRECTION_TARGETS) or \
                       (isinstance(a0, _ast.Attribute) and a0.attr in _AST_INDIRECTION_TARGETS):
                        return False, f"forbidden builtins indirection: {node.func.id}(<builtins>, …)"
            elif isinstance(node.func, _ast.Attribute):
                if node.func.attr in _AST_BLOCKLIST_ATTRS:
                    return False, f"forbidden method: .{node.func.attr}()"

    return True, ""


# ----- Pinned trusted tools: the ONE exception to the Python AST gate ----------
#
# Corvin-Knowledge's CLI (scripts/kb.py) imports ``subprocess`` for git, so the AST gate refuses it - which kept the
# knowledge base (kb new / kb task / kb check) out of reach of every agent session. The exception below is as narrow
# as it can be made:
#   * the script must be written in the command as EXACTLY the pinned absolute path (plain string comparison: no
#     normalisation, no wildcard, no relative form, no "..", no symlink, no quoting);
#   * every file of the tool's bundle (kb.py plus the sibling modules it imports from its own directory - Python puts
#     the script directory first on sys.path, so an unpinned sibling would run under the exception) must be a regular
#     file whose SHA-256, computed FRESH from the bytes on every call, equals the value pinned here;
#   * a missing file, a symlink, an unreadable file or a hash mismatch DENIES the command (fail closed) - it does not
#     fall back to "not my business";
#   * the command may not carry anything that makes the tool run other code (--reviewer-cmd / --drafter-cmd start
#     arbitrary commands by design; PYTHON*= / LD_*= / BASH_ENV= re-route interpreter start-up).
# Nothing else in this gate is relaxed: write-target detection, protected paths and every other Python snippet in the
# same command are still checked. The pin lives in this source file on purpose: changing it is a reviewed commit to
# the hook itself, not an edit of a data file next to the tool.
#
# RE-PIN after a reviewed change of the tool:   sha256sum kb.py kb_lib.py kb_model.py   (in the scripts/ directory),
# then update the three values below in the same commit that justifies the change. The values are the engine the
# Knowledge Graph plugin ships (Corvin-Marketplace .../corvin_knowledge/src/kb_engine/ENGINE.json): every tenant KB
# runs exactly those bytes, so ENGINE.json and _KB_ENGINE_PIN move together.
#
# WHERE the bundle may sit (ADR-2265: every install gets its own KB): the static table below names the maintainers'
# sibling checkout; ``_session_kb_pins`` adds, per hook call, the KB of THIS session's tenant
# (<CORVIN_HOME>/tenants/<CORVIN_TENANT_ID>/knowledge/scripts/kb.py) and the operator's explicit CORVIN_KB_REPO. Same
# hashes, same exact-path rule; another tenant's KB is never pinned for this session (tenant isolation).
# Residual risk (stated, not hidden): the hash is checked when the hook runs, not when python opens the file; an
# attacker who can already write the tool's directory concurrently could swap a file in between.
def _pin_root() -> Path:
    """<projects>/Corvin-Knowledge/scripts, derived from this hook's own location (CorvinOS is its sibling repo)."""
    try:
        return Path(__file__).resolve().parents[3].parent / "Corvin-Knowledge" / "scripts"
    except IndexError:
        return Path("/nonexistent-pin-root")


#: kb engine 133f98518be0 (Corvin-Knowledge scripts/, unchanged since 2026-10-16; the plugin ships the same bytes), re-pinned 2026-11-05: the 2026-11-03 pin named a windows-binary-io build that was never committed anywhere, so kb.py was refused for every session.
_KB_ENGINE_PIN: dict[str, str] = {
    "kb.py": "1321506414271146ec8bf749548a90193f67dd71da865dce690239d0749ea027",
    "kb_lib.py": "1e0add58952fb0e2e7af754498084796e37ef4ca5a2d510346ecf1ff154f0bea",
    "kb_model.py": "20365628ab203c1736e4a75992e504020a05126dc299fcd4537b37dbecc15fa0",
}

_PINNED_PYTHON_TOOLS: dict[str, dict[str, str]] = {
    str(_pin_root() / "kb.py"): dict(_KB_ENGINE_PIN),
}

_TENANT_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,63}$")


def _session_tenant() -> str:
    """The tenant this agent session runs for: the console sets CORVIN_TENANT_ID on every chat worker
    (chat_runtime), an ACS worker carries CORVIN_ACS_TENANT_ID. Anything else is ``_default``."""
    for var in ("CORVIN_TENANT_ID", "CORVIN_ACS_TENANT_ID"):
        v = os.environ.get(var, "").strip()
        if v:
            return v
    return "_default"


def _session_kb_pins() -> dict[str, dict[str, str]]:
    """The pinned kb.py locations of THIS session besides the static table: its tenant's own KB and an
    explicit CORVIN_KB_REPO. A malformed tenant id pins nothing (fail closed, never a path outside the home)."""
    out: dict[str, dict[str, str]] = {}
    tenant = _session_tenant()
    if _TENANT_ID_RE.match(tenant) and ".." not in tenant:
        try:
            out[str(_corvin_home() / "tenants" / tenant / "knowledge" / "scripts" / "kb.py")] = dict(_KB_ENGINE_PIN)
        except Exception:  # noqa: BLE001 -- an unresolvable home pins nothing
            pass
    env = os.environ.get("CORVIN_KB_REPO", "").strip()
    if env and os.path.isabs(env):
        out[str(Path(env) / "scripts" / "kb.py")] = dict(_KB_ENGINE_PIN)
    return out


def _pinned_tools() -> dict[str, dict[str, str]]:
    """Static table + this session's KB locations (the static table wins on a collision)."""
    return {**_session_kb_pins(), **_PINNED_PYTHON_TOOLS}

# PYTHON*= re-routes interpreter start-up (PYTHONPATH, PYTHONSTARTUP, PYTHONHOME, ...) and stays refused. The four
# variables below only change stream encoding / buffering / bytecode caching, load no code, and are what a session
# sets to get readable output from kb.py - refusing them made the pinned tool look broken for no security gain.
_PIN_HARMLESS_PYTHON_ENV = ("IOENCODING", "UNBUFFERED", "UTF8", "DONTWRITEBYTECODE")
_PIN_REFUSED_CMD_RE = re.compile(
    r"--reviewer-cmd|--drafter-cmd|\bPYTHON(?!(?:" + "|".join(_PIN_HARMLESS_PYTHON_ENV) + r")=)[A-Z_]*=|"
    r"\bLD_[A-Z_]+=|\bBASH_ENV=|\bsitecustomize\b"
)


def _pin_hint(cmd: str) -> str:
    """When ``cmd`` names a pinned tool by anything but its exact pinned path, say how to name it.

    The AST gate's generic answer ("use forge_exec") is wrong for a session that only wrote ``scripts/kb.py`` after a
    ``cd``: the relative form is refused by design (plain string comparison, no normalisation) and the one fix is the
    absolute path. Matching is by basename, so a look-alike script elsewhere also gets the pointer, never an exemption.
    """
    table = _pinned_tools()
    wanted: dict[str, str] = {}
    # an existing location first (on a fresh install only the tenant's own KB exists)
    for s in sorted(table, key=lambda s: not Path(s).is_file()):
        wanted.setdefault(Path(s).name, s)
    for m in _PY_FILE_RE.finditer(cmd):
        named = m.group(1)
        pinned = wanted.get(Path(named).name)
        if pinned and _pin_key(named) not in table:
            return (f" The tool {Path(named).name!r} is runnable only when written as the exact absolute path "
                    f"(no relative, ~ or $VAR form): python3 {pinned} ...")
    return ""


def _pin_key(script: str) -> str:
    """The pin-table key for a script named in a command: the string itself.

    Windows only: the table key is ``str(Path)`` (backslash-separated), but a
    Bash command there can only spell that path ``C:/.../kb.py`` or
    ``/c/.../kb.py`` - Git-Bash eats unquoted backslashes - so the pinned tool
    could never match and was always refused. Same absolute path, separator
    spelling only: relative, ``~`` and ``$VAR`` forms still never match."""
    if not _IS_WIN:
        return script
    return _normalize_msys_drive(script).replace("/", "\\")


def _pinned_tool_gate(cmd: str) -> tuple[frozenset[str], str]:
    """Return (exempt script strings, deny reason).

    ``exempt`` holds the pinned script paths of this command whose whole bundle verified; the caller skips only
    those for AST analysis. A non-empty ``deny reason`` means a pinned path WAS named but could not be trusted:
    the command is refused.
    """
    import hashlib  # noqa: PLC0415
    import hmac  # noqa: PLC0415

    exempt: set[str] = set()
    table = _pinned_tools()
    for m in _PY_FILE_RE.finditer(cmd):
        script = m.group(1)
        bundle = table.get(_pin_key(script))  # exact string match, nothing else counts
        if bundle is None:
            continue
        if _PIN_REFUSED_CMD_RE.search(cmd):
            return frozenset(), (f"pinned tool {script!r} refused: the command carries an option or environment "
                                 f"that makes it run other code")
        base = Path(_pin_key(script)).parent
        for name, want in bundle.items():
            p = base / name
            try:
                if p.is_symlink() or not p.is_file():
                    return frozenset(), f"pinned tool file {name!r} is missing or not a regular file"
                data = p.read_bytes()
            except OSError:
                return frozenset(), f"pinned tool file {name!r} is unreadable"
            got = hashlib.sha256(data).hexdigest()
            # The pins are taken from the LF blob; a Windows checkout (core.autocrlf=true) has CRLF on disk.
            # Python's tokenizer reads CRLF exactly as LF, so the CRLF->LF form runs the very same code.
            if not hmac.compare_digest(got, want) and b"\r\n" in data:
                got = hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()
            if not hmac.compare_digest(got, want):
                return frozenset(), (f"pinned tool file {name!r} changed (sha256 mismatch); review it, then "
                                     f"update the pin in path_gate.py")
        exempt.add(script)
    return frozenset(exempt), ""


def _extract_python_snippets(cmd: str, exempt: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
    """Extract Python code strings from a Bash command for AST analysis.

    Returns list of (code, label) tuples. label is used only for the
    audit detail field — never emitted in plain text. ``exempt`` lists script
    paths already verified by ``_pinned_tool_gate``; only those are skipped.
    """
    results: list[tuple[str, str]] = []

    for m in _PY_C_SINGLE_RE.finditer(cmd):
        results.append((m.group(1), "inline-c"))

    for m in _PY_C_DOUBLE_RE.finditer(cmd):
        results.append((m.group(1), "inline-c"))

    for m in _PY_FILE_RE.finditer(cmd):
        pyfile = m.group(1)
        if pyfile in exempt:
            continue
        try:
            p = Path(pyfile)
            if p.is_file() and p.stat().st_size <= _PY_FILE_SIZE_LIMIT:
                results.append((p.read_text(encoding="utf-8", errors="replace"),
                                "file"))
        except OSError:
            pass  # unreadable file — skip, do not block on absence

    # R2 finding: the AST eval/exec/__import__ blocklist was bypassable by
    # feeding the same inline code to python via stdin instead of -c. Capture:
    #   echo '<code>' | python     /  printf '<code>' | python   (piped stdin)
    #   python <<'EOF'\n<code>\nEOF                              (heredoc stdin)
    # so the AST gate inspects those forms too. (`python -m <module>` runs an
    # INSTALLED module — no inline source to inspect — and is governed by bwrap +
    # forbidden_imports inside the sandbox, not this inline-code gate; failing it
    # closed would break `python -m pytest`/`pip`. Documented limitation.)
    for m in _PY_PIPE_RE.finditer(cmd):
        results.append((m.group(2), "pipe-stdin"))
    for m in _PY_HEREDOC_RE.finditer(cmd):
        results.append((m.group(2), "heredoc-stdin"))

    return results


# ----- bash extraction ----------------------------------------------------

# Sentinel returned by _bash_targets when the command is too ambiguous to
# parse safely AND references a protected path component.
UNPARSEABLE = "<unparseable>"

# Bash output-redirect detection. Two hardening fixes (R6):
#   #4 clobber-override operator `>|` (and numbered-fd form `N>|`, e.g. `1>|`):
#      `echo x >| audit.jsonl` writes but the old alternation `(?:>{1,2}|&>|>&)`
#      did not list `>|`, so it slipped through undetected. Added `>\|`. The
#      `\d*` prefix already covers the numbered-fd form `N>|`.
#   #5 space-less redirect `cmd>file` (bash allows `echo x>file`): the old
#      `(?:^|[\s;&|])` lead-anchor required whitespace/separator before the
#      operator, so `word>protected` was missed. The lead-anchor is dropped so
#      a redirect with no preceding space is still detected. Captured tokens
#      that don't resolve to a protected path are harmless (allowed at resolve
#      time); the gate only denies when a captured target resolves protected —
#      so dropping the anchor cannot over-block legitimate non-protected writes.
_REDIRECT_RE  = re.compile(r"\d*(?:>{1,2}|>\||&>|>&)\s*([^\s;&|<>]+)")
_PY_OPEN_RE   = re.compile(
    r"""open\(\s*['"]([^'"]+)['"]\s*,\s*['"][rwax+b]*[wax]+[rwax+b]*['"]""")
# Inline interpreter one-liners (`python -c '...'`, `perl -e`, `ruby -e`, node
# `-e`) can write via any of open(mode="w") / os.open / pathlib.write_text /
# fs.writeFileSync — forms _PY_OPEN_RE cannot enumerate. When such a one-liner
# also mentions a protected hint, the write target is opaque, so fail closed
# (mirrors the `bash -c` / eval treatment). Absolute non-protected writes are
# unaffected because _looks_protected gates the fail-closed.
_INLINE_INTERP_RE = re.compile(
    r"\b(?:python[0-9.]*|perl|ruby|node|deno|php|osascript)\b[^|;&]*\s-(?:c|e)\b")

# Layer-16 v2 — additional Bash vectors. fail-closed on any of these when
# the surrounding command contains a protected hint, because the actual
# write target is opaque at parse time:
#   >(<inner>)        process substitution writing into a sub-shell
#   bash -c '...'     sh -c '...'   recursive shell
#   xargs ...         deferred substitution via {}
#   awk -i inplace    in-place rewrite of the positional file argument
#   <<              heredoc (the body content can contain redirects we miss)
_PROC_SUBST_OUT_RE = re.compile(r">\(([^)]*)\)")
_RECURSIVE_SHELL_RE = re.compile(r"\b(?:ba)?sh\s+-c\b")
_XARGS_RE = re.compile(r"\bxargs\b")
_AWK_INPLACE_RE = re.compile(r"\bawk\b[^|;&]*-i\s+(?:inplace|['\"]inplace['\"])\b")
_HEREDOC_RE = re.compile(r"<<-?\s*['\"]?\w+['\"]?")

_DEST_LAST_CMDS = ("mv", "cp", "install", "rsync")
# Commands whose EVERY non-flag argument is a candidate path that can wipe,
# truncate, perm-downgrade, symlink-replace, or delete a protected file. Each
# arg is checked against is_protected_path(); non-path args (e.g. a chmod mode
# `666`, a truncate size `0`) are harmless — they simply don't resolve to a
# protected path. Without these, `truncate -s0 audit.jsonl`, `rm audit.jsonl`,
# `chmod 666 audit.jsonl`, and `ln -sf /tmp/evil forge/tools/x.py` ALL passed
# the gate (no target extracted → allow), defeating the L10 write-protection
# boundary for the L16 chain and the Forge tool workspace.
_TARGET_ALL_CMDS = ("truncate", "ln", "chmod", "chown", "chgrp", "chattr",
                    "rm", "rmdir", "unlink", "shred")
# Scripted line editors: the edit program arrives via stdin / `-c` and is opaque
# to static parsing, so a protected file argument is fail-closed.
_SCRIPTED_EDITORS = ("ex", "ed")
#: Commands whose path arguments CREATE or OVERWRITE a file but do not destroy
#: a tree (review R9-4): every non-flag argument is checked with
#: is_protected_path only — no tree rule, so `mkdir outputs` in a session
#: workdir (which lives under the corvin home) is not blocked.
_CREATE_PATH_CMDS = ("mkdir", "touch", "patch", "xxd", "uniq", "split", "csplit",
                     "tac", "mkfifo", "mknod")
#: Output options that name the file a command writes — only for commands
#: where they DO (`grep -o`/`ls -o` take no file; applying the flags to every
#: command blocked read-only searches, review R10-3). Also the run-together
#: form `-o<path>` (R10-1).
_OUTPUT_FLAGS = ("-o", "-O", "--output", "--output-document", "-out", "--out")
_OUTPUT_FLAG_CMDS = ("curl", "wget", "sort", "openssl", "pandoc", "gcc", "cc", "clang",
                     "g++", "go", "rustc", "xz", "zstd", "gzip", "convert", "magick",
                     "ffmpeg", "pdftotext", "dot", "plantuml")
#: Read-only commands allowed after a `cd` INTO a ledger-class store.
_READ_ONLY_CMDS = ("cd", "pushd", "popd", "cat", "less", "more", "head", "tail", "ls",
                   "wc", "grep", "rg", "stat", "file", "jq", "echo", "pwd", "true",
                   # pure readers (no output-file argument; a redirect is
                   # checked separately)
                   "egrep", "fgrep", "zgrep", "zcat", "bzcat", "xzcat", "sha256sum",
                   "sha1sum", "md5sum", "cksum", "b2sum", "diff", "cmp", "du", "cut",
                   "od", "hexdump", "strings", "nl", "realpath", "readlink",
                   "basename", "dirname", "test", "[", "printf", "tree", "column")
#: Commands that rewrite a file argument IN PLACE whenever they run on one. The
#: write-target extraction used to know `sed -i` (exact short form) only, so
#: `sed --in-place`, `sed -Ei`, `perl -pi`, `gzip`, `sponge`, `shuf -o`,
#: `vim -c`, `sqlite3 <db> 'delete ...'` on a protected file all passed
#: (review 2026-11-19 SEC-2). Every path argument of these is a write target.
_INPLACE_ALWAYS_CMDS = ("patch", "sponge", "sqlite3", "sqlite",
                        "vi", "vim", "nvim", "view", "nano", "pico", "emacs",
                        "emacsclient", "ed", "ex", "joe", "micro", "ne")
#: PowerShell read cmdlets / aliases (compared lower-case; PowerShell tool only).
_PS_READ_ONLY_CMDS = frozenset({
    "get-content", "gc", "type", "get-childitem", "gci", "dir", "select-string", "sls",
    "get-item", "gi", "test-path", "get-filehash", "measure-object", "write-output",
    "write-host", "get-acl", "get-itemproperty", "resolve-path", "get-location",
    "set-location", "sl", "push-location", "pop-location",
})
#: In-place (de)compressors: they replace the file unless told to write stdout
#: or only to list / test.
_COMPRESS_CMDS = ("gzip", "gunzip", "bzip2", "bunzip2", "xz", "unxz", "lzma", "unlzma",
                  "zstd", "unzstd", "lz4", "unlz4", "compress", "uncompress", "pigz",
                  "pbzip2", "lzip")
_COMPRESS_READ_FLAGS = ("-c", "--stdout", "--to-stdout", "-l", "--list", "-t", "--test")
#: git subcommands whose path arguments are rewritten / removed in the work tree.
_GIT_PATH_WRITERS = ("checkout", "restore", "rm", "mv")
#: The ledger / anchor / queue stores, as PATH fragments: a bare word
#: ("session_ledger" in a commit message) is not a hint (R7-5), a path is.
_LEDGER_HINTS = ("session_ledger/", ".corvin-ledger/", "cel_anchors/", "secrets/personal_kb/",
                 "pending_notifications/")
_PROTECTED_HINTS = ("forge", "skill-forge", "audit.jsonl", "policy.json",
                    ".corvin", *_LEDGER_HINTS,
                    "secrets.json", "corvin-voice",
                    # Review 2026-11-19 SEC-3 - the gate sources, the chain's
                    # out-of-tree anchors, the hook registration and the L44
                    # policy. Path fragments, so a bare word in a message is
                    # not a hint.
                    "path_gate.py", "voice/hooks/", "hooks.json", "with_python.sh",
                    "security_events.py", "tripwire.py", "core/paths/",
                    "shared/paths.py", "audit_anchor", "audit_mac_active",
                    "chain_ids/", "chain_tails/", "mac_active_chains/",
                    "chain_witness/", "chain_witness_retired/", "chain_ids_retired/",
                    ".claude/plugins",
                    ".claude/settings", "house_rules.yaml", "house_rules.py",
                    # ADR-0012 — data-locality operator policy
                    "data_policy.yaml", "data_policy.yml",
                    "data_policy.json",
                    # ADR-0013 — compute worker tree + socket
                    "compute", "worker.sock",
                    # ADR-0026 — Compute Fabric artifact store, plugin installs,
                    # datasource manifests/checkpoints, and custom adapter code.
                    # These are all under <corvin_home>/**/compute/ which is
                    # already covered by the "compute" hint above, but we list
                    # them explicitly for clarity and for Bash fail-closed matching.
                    "compute/artifacts", "compute/plugins",
                    "compute/datasources", "compute/datasource-adapters",
                    # ADR-0014 — admin-UI plugin trust artefacts
                    "operator_tokens", "admin/sessions",
                    "csrf_secrets", "admin/static",
                    # ADR-0017 — license-gate token + grace state + pubkey
                    "license.jwt", "license/state.json",
                    "license/pubkey.pem",
                    # ADR-0111 — SOB client key material + encrypted bundle
                    "sob.enc", "sub_private.key",
                    "license/sob.enc", "license/sub_private.key",
                    "license_manifest.json",
                    # ADR-0093 M1.3 — license plugin source tree:
                    # an LLM-instructed patch to verifier.py or tier_flags.py
                    # would bypass has_flag() locally. Deny writes to these
                    # files so path_gate.denied lands in the audit chain.
                    "corvin_license/verifier.py",
                    "corvin_license/tier_flags.py",
                    # Layer 10 v2 / 29.3a — cowork persona + bridge settings
                    # carry the delegate security floors. Bash that mentions
                    # them must fail-closed on ambiguity.
                    "cowork/personas/", "claude-cowork/personas/",
                    "bridges/discord/settings", "bridges/telegram/settings",
                    "bridges/whatsapp/settings", "bridges/slack/settings",
                    "bridges/email/settings", "bridges/teams/settings",
                    "bridges/signal/settings", "bridges/shared/settings",
                    # Layer 29.4a — tenant policy file
                    "tenant.corvin.yaml", "tenant.corvin.yml",
                    "tenant.corvin.json",
                    # ADR-0021 Layer 31 — supply-chain manifest + SBOM +
                    # pin-hash + per-plugin capability declaration. Bash
                    # that mentions them must fail-closed on ambiguity.
                    "requirements.txt", "package-lock.json",
                    "sbom.cdx.json", "plugin.corvin.yaml",
                    "supply_chain.yaml", "supply_chain.yml",
                    "supply_chain.json",
                    # ADR-0032 — AWPKG installed-package tree
                    "packages", "_awpkg_meta.json",
                    # ADR-0096 — MCP Plugin Manager catalog + active list
                    "mcp-tools", "mcp_manager")


def _looks_protected(s: str) -> bool:
    # Case-fold before matching so FORGE/ or Audit.JSONL are caught on
    # case-insensitive filesystems (macOS HFS+, Windows NTFS default).
    s_lower = s.lower()
    return any(h.lower() in s_lower for h in _PROTECTED_HINTS)


def _split_segments(cmd: str) -> list[str]:
    """Split a bash command on top-level separators (; & | && || and newlines).
    Naive: does not account for quoting. Adequate as a first pass — the
    individual segments still go through shlex below.

    Newlines/CR are genuine command separators in bash: without them a payload
    like ``echo ok\\nrm -f ~/.corvin/.../audit.jsonl`` was split into a single
    benign ``echo`` segment and the destructive second line was never inspected
    (fail-open). Treating \\n\\r as separators is purely additive strictness."""
    return [s.strip() for s in re.split(r"\|\||&&|[;&|\n\r]", cmd) if s.strip()]


def _last_nonflag(tokens: list[str]) -> str | None:
    for t in reversed(tokens):
        if t.startswith("-"):
            continue
        return t
    return None


def _all_nonflag(tokens: list[str]) -> list[str]:
    """Every non-flag token (candidate paths). Non-path args resolve to a
    non-protected path and are harmless."""
    return [t for t in tokens if not t.startswith("-")]


# Command wrappers that simply EXEC the command following them. The gate keyed
# every per-command check on toks[0]; a wrapper in toks[0] (`env rm -rf ~/.corvin`,
# `busybox rm …`, `nice`/`timeout`/`sudo`/`setsid`/`nohup`/`stdbuf`/`ionice`/`time`)
# put the wrapper — not `rm` — in cmd_name and slipped past ALL of them, including
# the leaf-name guard (round-4, empirically confirmed: `env rm audit.jsonl` was
# ALLOW while `rm audit.jsonl` was DENY). We unwrap to the real command first.
_CMD_WRAPPERS = {
    "env", "command", "builtin", "exec", "nice", "ionice", "nohup", "setsid",
    "stdbuf", "time", "timeout", "sudo", "doas", "busybox", "unbuffer",
}
# wrapper → set of option flags that consume a following value (so we skip both)
_WRAPPER_ARG_OPTS = {
    "env": {"-u", "--unset", "-C", "--chdir", "-S", "--split-string"},
    "timeout": {"-s", "--signal", "-k", "--kill-after"},
    "nice": {"-n", "--adjustment"},
    "ionice": {"-c", "--class", "-n", "--classdata", "-p", "--pid", "-t"},
    "sudo": {"-u", "--user", "-g", "--group", "-C", "-p", "--prompt", "-U", "-h",
             "-R", "-D", "--chdir"},
    "doas": {"-u", "-C", "--chdir"},
}


def _strip_cmd_wrappers(toks: list[str], _depth: int = 0) -> list[str]:
    """Return the token list starting at the REAL command, peeling leading exec
    wrappers and their own args. Conservative: only wrappers with a parseable arg
    grammar are peeled; an unrecognized structure stops the peel (fail-closed
    downstream — the destructive-verb + corvin-tree backstop still fires)."""
    if _depth > 8 or not toks:
        return toks
    head = toks[0].rsplit("/", 1)[-1]
    if head not in _CMD_WRAPPERS:
        return toks
    rest = toks[1:]
    if head == "env":
        i = 0
        while i < len(rest):
            t = rest[i]
            if "=" in t and not t.startswith("-"):
                i += 1  # VAR=VAL assignment
            elif t in _WRAPPER_ARG_OPTS["env"]:
                i += 2
            elif t.startswith("-"):
                i += 1
            else:
                break
        return _strip_cmd_wrappers(rest[i:], _depth + 1)
    if head == "busybox":
        # `busybox <applet> …` — the applet is the real command.
        return _strip_cmd_wrappers(rest, _depth + 1)
    arg_opts = _WRAPPER_ARG_OPTS.get(head, set())
    i = 0
    while i < len(rest) and rest[i].startswith("-"):
        i += (2 if rest[i] in arg_opts else 1)
    # timeout takes a mandatory DURATION positional before the command.
    if head == "timeout" and i < len(rest):
        i += 1
    return _strip_cmd_wrappers(rest[i:], _depth + 1)


# Archive extractors — none were in any command set, so `tar -C ~/.corvin -xf
# evil.tar` / `unzip -o evil.zip -d ~/.corvin` could OVERWRITE the hash-chained
# audit.jsonl (round-4). Reading the tree INTO an archive is a read (not gated);
# only EXTRACTION whose destination touches the corvin tree fails closed.
_ARCHIVE_CMDS = ("tar", "gtar", "bsdtar", "unzip", "cpio", "pax",
                 "7z", "7za", "7zr")


def _dir_at_or_under_corvin_home(d: str) -> bool:
    """chdir-context test: is dir *d* the corvin home or INSIDE it? Unlike
    _touches_corvin_tree this EXCLUDES ancestors of home — after `cd <ancestor>`
    a relative destructive target (`rm -rf build`) descends AWAY from the tree, so
    flagging ancestors over-blocked common workflows like `cd ~ && rm -rf
    Downloads/x` and `cd <repo> && rm -rf dist` (round-5 over-block fix)."""
    try:
        abs_d = _abs(d)
    except Exception:  # noqa: BLE001 — unresolvable → fail closed (mirrors
        # _touches_corvin_tree). An unresolvable cd-target (e.g. a NUL byte
        # that _abs()/Path.resolve() chokes on) must NOT be silently treated
        # as "not chdir'd into the tree" — the real shell can still land
        # inside the corvin home once such poisoning is stripped/normalised
        # at execution time, so treating "unresolvable" as "cd'd into the
        # tree" is the only safe default here.
        return True
    if is_protected_path(abs_d):
        return True
    sep = os.sep
    abs_str = str(abs_d).rstrip(sep) or sep
    # Protected tree roots for cd-context: the corvin runtime home AND the
    # voice config dir (~/.config/corvin-voice), which holds the secret vault +
    # BYOK token. Without the voice dir here, `cd ~/.config/corvin-voice && :
    # > secrets.json` was not recognised as a cd-into-tree (bypass A) because
    # that dir is not under CORVIN_HOME.
    roots: list[str] = []
    for _resolver in (_corvin_home, lambda: _vault_path().parent):
        try:
            r = _ref(_resolver()).rstrip(sep)
            if r:
                roots.append(r)
        except Exception:  # noqa: BLE001
            pass
    return any(abs_str == r or abs_str.startswith(r + sep) for r in roots)


def _is_read_only_tree(d: str) -> bool:
    """A directory after a ``cd`` into which only reads may follow: a
    ledger-class / audit-integrity store or a protected tree of the RUNTIME root (``<home>/**/forge``, ``compute``,
    ``memory``, ...). A relative name written there lands in the protected
    store although the hook resolved it against its own cwd. Protected SOURCE
    trees of a checkout are not included: ``cd <hooks dir> && pytest`` is a
    test run, and a write to a file in it is still caught by its target."""
    if _is_strict_store_path(d):
        return True
    try:
        abs_str = str(_abs(d)).rstrip(os.sep)
        home_str = _ref(_corvin_home())
    except Exception:  # noqa: BLE001
        return True
    return abs_str.startswith(home_str + os.sep) and is_protected_path(abs_str)


def _seg_chdirs_into_tree(seg: str) -> bool:
    """True when a segment changes the working directory INTO (at/under) the corvin
    tree via `cd`, `pushd`, or a wrapper chdir flag (`env -C/--chdir`,
    `sudo -D/--chdir`, `doas -C`) — after which a relative destructive target
    (`rm -rf tenants`) lands inside the protected tree at run time even though the
    hook resolved it against its own cwd (round-4/round-5)."""
    try:
        raw = _split_tokens(seg)
    except ValueError:
        return False
    if not raw:
        return False
    hit = _dir_at_or_under_corvin_home
    head = raw[0].rsplit("/", 1)[-1]
    if head in ("cd", "pushd") and len(raw) > 1 and hit(raw[1]):
        return True
    # wrapper chdir flags — env/sudo/doas can chdir before exec'ing the command.
    if head in ("env", "sudo", "doas"):
        _chdir_flags = ("-C", "--chdir") if head != "sudo" else ("-C", "-D", "--chdir")
        for i, t in enumerate(raw):
            if t in _chdir_flags and i + 1 < len(raw) and hit(raw[i + 1]):
                return True
            if t.startswith("--chdir=") and hit(t.split("=", 1)[1]):
                return True
            if t.startswith(("-C", "-D")) and len(t) > 2 and hit(t[2:]):
                return True
    return False


def _archive_dest_dirs(cmd_name: str, arg_toks: list[str]) -> list[str]:
    """Extraction-destination dirs for an archive command (tar -C, unzip -d,
    cpio -D, 7z -o<dir>)."""
    dests: list[str] = []
    for idx, t in enumerate(arg_toks):
        if cmd_name in ("tar", "gtar", "bsdtar", "cpio", "pax"):
            if t in ("-C", "--directory", "-D", "--directory=") and idx + 1 < len(arg_toks):
                dests.append(arg_toks[idx + 1])
            elif t.startswith("--directory="):
                dests.append(t.split("=", 1)[1])
            elif t.startswith("-C") and len(t) > 2:
                dests.append(t[2:])
        elif cmd_name == "unzip":
            if t == "-d" and idx + 1 < len(arg_toks):
                dests.append(arg_toks[idx + 1])
        elif cmd_name in ("7z", "7za", "7zr"):
            if t.startswith("-o") and len(t) > 2:
                dests.append(t[2:])
    return dests


# `find` mutating actions — these WRITE (delete, rewrite, or run an opaque
# command on) the matched files, so a find rooted at / matching a protected
# path is a write vector. Same bypass class as the previously-patched
# truncate/rm/ln vectors: `find <home> -name audit.jsonl -delete` extracted no
# target and was allowed, deleting the hash-chained audit log (path-audit
# 2026-07-06, empirically confirmed).
_FIND_MUTATING = ("-delete", "-exec", "-execdir", "-ok", "-okdir",
                  "-fprint", "-fprint0", "-fprintf", "-fls")
# `find` and its common aliases (GNU coreutils `gfind` on macOS/Homebrew,
# BSD `bfind`). Matching only the literal "find" let `gfind ... -delete` skip
# the whole branch (path-audit 2026-07-07 round-2).
_FIND_CMDS = ("find", "gfind", "bfind")


def _touches_corvin_tree(target: str) -> bool:
    """True when a recursive / forcible / glob operation on *target* could reach
    a protected file inside the corvin runtime tree.

    Covers every direction that is_protected_path (which only flags specific leaf
    names / subdir tokens) misses, all empirically proven bypasses:
      * target IS a protected leaf/subdir (is_protected_path), OR
      * target == the corvin home root — `rm -rf ~/.corvin` wipes every
        hash-chained audit.jsonl, OR
      * target is UNDER the home — `rm -rf ~/.corvin/tenants`,
        `rm ~/.corvin/*.jsonl` (glob), `chmod -R ~/.corvin/global`, OR
      * target is an ANCESTOR of the home — `find ~ -name '*.jsonl' -delete`,
        `rm -rf ~`.
    Applies to both `find` mutating roots and the recursive/glob destructive
    commands (rm/mv/chmod/...). Fail closed — audit-chain integrity is a
    CLAUDE.md load-bearing red-line (path-audit 2026-07-06/07)."""
    try:
        abs_t = _abs(target)
    except Exception:  # noqa: BLE001 — unresolvable → fail closed
        return True
    if is_protected_path(abs_t):
        return True
    sep = os.sep
    abs_str = str(abs_t).rstrip(sep) or sep
    # Protected roots: the corvin runtime home AND the voice config dir
    # (~/.config/corvin-voice, which holds the BYOK secret vault + profile).
    # BOTH must be covered here or a direct `rm -rf ~/.config/corvin-voice`,
    # `mv`, or `tar -czf` of that dir escaped the gate — deleting or EXFILTRATING
    # the secret vault — even though the file-level is_protected_path flags
    # secrets.json. Mirror the cd-context roots in _dir_at_or_under_corvin_home.
    roots: list[str] = []
    for _resolver in (_corvin_home, lambda: _vault_path().parent):
        try:
            r = _ref(_resolver()).rstrip(sep)
            if r:
                roots.append(r)
        except Exception:  # noqa: BLE001
            pass
    # target == root, target under root, or target an ancestor of root
    if any(abs_str == r
           or abs_str.startswith(r + sep)
           or r.startswith(abs_str + sep)
           for r in roots):
        return True
    # Round 2 (R2-SEC-3 / R2-SEC-4): the guard roots - equal or ancestor only.
    # A path UNDER one of them is decided by is_protected_path above, so a
    # session's own files below ~/.claude or beside a non-default key stay free.
    try:
        return _removes_guard_root(abs_str)
    except Exception:  # noqa: BLE001
        return True


def _find_root_touches_protected(root: str) -> bool:
    """A `find` search root touches the corvin tree — see _touches_corvin_tree."""
    return _touches_corvin_tree(root)
    return False


def _find_roots(arg_toks: list[str]) -> list[str]:
    """Return the search-root path args of a `find` invocation.

    Layout: ``find [-H|-L|-P|-D x|-O n] [path...] [expression]``. The roots are
    the leading non-flag tokens after any global options, up to the first
    expression predicate (which starts with ``-``, ``(``, ``!`` or ``,``)."""
    roots: list[str] = []
    i = 0
    while i < len(arg_toks):
        t = arg_toks[i]
        if t in ("-H", "-L", "-P"):
            i += 1
            continue
        if t in ("-D", "-O") and i + 1 < len(arg_toks):  # -D debugopts / -O level
            i += 2
            continue
        break
    while i < len(arg_toks):
        t = arg_toks[i]
        if t.startswith("-") or t in ("(", "!", ","):
            break
        roots.append(t)
        i += 1
    return roots


def _target_dir_flag(arg_toks: list[str], abbrev: bool = True) -> str | None:
    """Resolve a -t <dir> / -t<dir> / --target-directory=<dir> destination, if any.

    GNU mv/cp/install/rsync all accept this; previously only `install` honoured
    it, so `cp -t <protected_dir> src` (and `--target-directory=`) bypassed the
    gate because _last_nonflag returned the SOURCE, not the protected dest."""
    for i, tok in enumerate(arg_toks):
        if tok in ("-t", "--target-directory") and i + 1 < len(arg_toks):
            return arg_toks[i + 1]
        if tok.startswith("--target-directory="):
            return tok.split("=", 1)[1]
        # GNU prefix abbreviation: `cp --t=<dir>`, `mv --targ <dir>` (round 3).
        # Not for rsync (popt, no abbreviations; `--timeout=` is not a directory).
        if abbrev and len(tok) >= 3 and _long_opt_is(tok, "--target-directory"):
            val = _long_opt_value(arg_toks, i)
            if val is not None:
                return val
        if tok.startswith("-t") and len(tok) > 2 and not tok.startswith("--"):
            return tok[2:]  # glued GNU short form -t<dir>
    return None


def _short_cluster_has(tok: str, flag: str, arg_flags: str) -> bool:
    """True when the short-option cluster *tok* (``-Ei``, ``-pi.bak``, ``-ni``)
    carries *flag* before any option that consumes the rest of the cluster as
    its argument (``-e SCRIPT`` glued as ``-es/i/x/``)."""
    if not tok.startswith("-") or tok.startswith("--") or len(tok) < 2:
        return False
    for ch in tok[1:]:
        if ch == flag:
            return True
        if ch in arg_flags:
            return False
    return False


def _path_candidates(arg_toks: list[str]) -> list[str]:
    """Every token that can name a path: the non-flag arguments plus the value
    of ``--opt=value`` and of the glued short forms ``-o<path>`` / ``-C<dir>``."""
    out: list[str] = []
    for t in arg_toks:
        if t.startswith("-"):
            if "=" in t:
                out.append(t.split("=", 1)[1])
            elif len(t) > 2 and not t.startswith("--"):
                out.append(t[2:])
            continue
        out.append(t)
    return [c.strip("\"'") for c in out if c]


def _inplace_write_candidates(cmd_name: str, arg_toks: list[str]) -> list[str]:
    """Path arguments a command rewrites in place, or [] when this invocation
    is not an in-place writer. Candidates are checked by the caller with
    ``is_protected_path`` - a non-path argument (a sed script) resolves to an
    unprotected path and is harmless."""
    if cmd_name in _INPLACE_ALWAYS_CMDS:
        return _path_candidates(arg_toks)
    if cmd_name in _COMPRESS_CMDS:
        if any(t in _COMPRESS_READ_FLAGS for t in arg_toks):
            return []
        return _path_candidates(arg_toks)
    if cmd_name in ("sed", "gsed"):
        if any(_long_opt_is(t, "--in-place")
               or _short_cluster_has(t, "i", "efl") for t in arg_toks):
            return _path_candidates(arg_toks)
        # `w FILE` / `W FILE` / `s///w FILE` inside the script name a file sed
        # truncates and writes (round 3). Loose on purpose: candidates only.
        return [m.group(1) for t in arg_toks if not t.startswith("-")
                for m in _SED_WRITE_TARGET_RE.finditer(t)]
    if cmd_name in ("perl", "ruby"):
        if any(_short_cluster_has(t, "i", "eEmMIxCd") for t in arg_toks):
            return _path_candidates(arg_toks)
        return []
    if cmd_name in ("awk", "gawk", "mawk", "nawk"):
        for i, t in enumerate(arg_toks):
            nxt = arg_toks[i + 1] if i + 1 < len(arg_toks) else ""
            if ((t in ("-i", "--include") and "inplace" in nxt)
                    or (t.startswith(("-i", "--include=")) and "inplace" in t)):
                return _path_candidates(arg_toks)
        return []
    if cmd_name == "yq":
        if any(t in ("-i", "--inplace") or t.startswith("--inplace=")
               or _short_cluster_has(t, "i", "") for t in arg_toks):
            return _path_candidates(arg_toks)
        return []
    if cmd_name == "shuf":
        return [arg_toks[i + 1] for i, t in enumerate(arg_toks)
                if (t == "-o" or (_long_opt_is(t, "--output") and "=" not in t))
                and i + 1 < len(arg_toks)] + [
            t.split("=", 1)[1] for t in arg_toks
            if _long_opt_is(t, "--output") and "=" in t] + [
            t[2:] for t in arg_toks
            if t.startswith("-o") and len(t) > 2 and not t.startswith("--")]
    if cmd_name == "wget":
        return [arg_toks[i + 1] for i, t in enumerate(arg_toks)
                if t in ("-P", "--directory-prefix") and i + 1 < len(arg_toks)] + [
            t.split("=", 1)[1] for t in arg_toks if t.startswith("--directory-prefix=")] + [
            t[2:] for t in arg_toks if t.startswith("-P") and len(t) > 2]
    if cmd_name == "xxd" and any(_short_cluster_has(t, "r", "") or t in ("-revert", "--revert")
                                 for t in arg_toks):
        return _path_candidates(arg_toks)
    if cmd_name == "xxd" or cmd_name == "uniq":
        # INPUT [OUTPUT]: only the second positional is written (R3-SEC-9).
        nf = [t for t in _all_nonflag(arg_toks) if not _NUMERIC_ARG_RE.fullmatch(t)]
        return nf[1:]
    if cmd_name in ("split", "csplit"):
        return _path_candidates(arg_toks)
    return []


# ----- reader invocations (review 2026-11-19 round 2, R2-SEC-5) ------------
#
# Round 1 made every non-read command that NAMES a strict store (the audit
# chain, a ledger) fail closed. The read list was a list of command NAMES, so
# every analysis tool an operator session uses on the chain was refused:
# ``awk '{print $1}' audit.jsonl``, ``sort``, ``xxd | head``, ``perl -ne``,
# ``tar czf /tmp/c.tgz audit.jsonl``, ``gzip -c``, ``python3 -m json.tool``, a
# ``for`` loop over the file, ``cp <gate source> /tmp/x``. A command name does
# not say whether an invocation writes; its ARGUMENTS do. The functions below
# decide per invocation, conservatively: anything they cannot prove to be a read
# stays a non-read and fails closed exactly as before.

_AWK_NAMES = ("awk", "gawk", "mawk", "nawk")
#: awk program text that writes or runs something: ``print > "f"``,
#: ``print | "cmd"``, ``system(...)``, ``close(...)`` of an output.
_AWK_WRITE_RE = re.compile(
    r"\bsystem\s*\(|\bprintf?\b[^;}]*(?:>|\|)|\|\s*&"
    # round 3: `"cmd" | getline` runs a command, `@load` / `@include` pull code in.
    r"|\|\s*getline\b|@\s*(?:load|include|namespace)\b")
#: perl / ruby one-liner text that can write, delete or run something.
_SCRIPT_WRITE_RE = re.compile(
    r">|\bopen\b|\bunlink\b|\brename\b|\btruncate\b|\bsystem\b|\bexec\b|`|\bqx\b"
    r"|\bFile\b|\bIO\b|\bsyswrite\b|\bsysopen\b|\bmkdir\b|\brmdir\b|\bchmod\b|\bchown\b"
    r"|\butime\b|\blink\b|\bsymlink\b|\bFileUtils\b|\bKernel\b|\brequire\b|\buse\b|\beval\b"
    # perl's in-place switch set from inside the program, `do FILE`, sockets.
    r"|\$\^I|INPLACE_EDIT|\bdo\b|\bsocket\b|\bspawn\b|\bfork\b")
#: The file a sed ``w`` / ``W`` command (or ``s///w``) names - a write TARGET.
#: Deliberately loose (any ``w<space>path``): it only adds candidates that are
#: then checked with ``is_protected_path``. Whether a sed script is a pure read
#: is decided by ``_sed_script_reads_only``, never by a regex (round 3,
#: R3-SEC-1: ``$w F``, ``1,$w F``, ``0~1w F``, ``s/a/b/gw F``, ``1e cmd`` all
#: slipped a regex that wanted ``^ ; { } \\s /`` before the command letter).
_SED_WRITE_TARGET_RE = re.compile(r"[wW](?:\s+|(?=[/~]))([^\s;}]+)")
#: A perl / ruby regex literal in OPERAND position (after ``if`` / ``unless`` /
#: ``=~`` / ``(`` / ``&&`` / ``||`` / ``and`` / ``or`` or at the start) whose body
#: is plain text: no ``(``, ``{``, ``@``, backslash or quote, so nothing in it
#: can run. Removed before the write scan so ``print if /open/`` is a read
#: (R3-SEC-9). A ``/`` after an operand is DIVISION (``1 / unlink / 1`` runs
#: ``unlink``) and is never stripped.
_SCRIPT_PLAIN_REGEX_RE = re.compile(
    r"(?:^|(?<=\bif)|(?<=\bunless)|(?<==~)|(?<=!~)|(?<=\()|(?<=&&)|(?<=\|\|)"
    r"|(?<=\band)|(?<=\bor))\s*/[A-Za-z0-9_ .^*+?|:,=-]*\$?/[a-z]*")
_TAR_NAMES = ("tar", "gtar", "bsdtar")
#: Keyword-only shell segments that run nothing themselves.
_SHELL_NOOP_WORDS = frozenset({"done", "fi", "esac", "then", "else", "do", "{", "}", "(", ")"})
#: Leading shell keywords after which the real command of the segment follows.
_SHELL_LEAD_WORDS = frozenset({"do", "then", "else", "{", "!", "time", "if", "elif",
                               "while", "until"})


def _strip_shell_keywords(toks: list[str]) -> list[str]:
    """Peel ``do`` / ``then`` / ``else`` / ``if`` / ``while`` ... so the token list
    starts at the command the segment runs (``do wc -l "$f"`` -> ``wc -l "$f"``)."""
    i = 0
    while i < len(toks) and toks[i] in _SHELL_LEAD_WORDS:
        i += 1
    return toks[i:]


def _for_loop_words(toks: list[str]) -> "list[str] | None":
    """The word list of a ``for NAME in WORDS`` segment, or None if not one."""
    if len(toks) >= 3 and toks[0] == "for" and toks[2] == "in":
        return [t.strip("\"'") for t in toks[3:]]
    return None


def _loop_word_is_protected(word: str) -> bool:
    """A ``for`` word that hands the loop body a protected path: a strict store
    or protected file named literally, or a glob that matches one now (or, when
    it matches nothing yet, names a chain / ledger by hint). NOT the whole
    runtime tree: a session workdir lives under the corvin home, and
    ``for f in *.txt; do mv "$f" out/; done`` there is ordinary work."""
    word = (word or "").strip("\"'")
    if not word or word.startswith("-"):
        return False
    if "$" in word or "`" in word:
        return _looks_protected(word)
    if any(c in word for c in "*?["):
        import glob as _glob  # noqa: PLC0415
        try:
            matches = _glob.glob(str(_abs(word)), recursive=True)
        except Exception:  # noqa: BLE001
            return True
        if matches:
            return any(_is_strict_store_path(m) or is_protected_path(m) for m in matches)
        low = word.lower()
        return any(h in low for h in ("audit.jsonl", "audit_anchor", "chain_", *_LEDGER_HINTS))
    return _is_strict_store_path(word) or is_protected_path(word)


def _long_opt_name(tok: str) -> str:
    """``--name`` of a long option token (``--name=value`` included), else ''."""
    if tok.startswith("--") and len(tok) > 2:
        return tok.split("=", 1)[0]
    return ""


def _long_opt_is(tok: str, *fulls: str) -> bool:
    """True when *tok* is one of the long options *fulls* or a prefix of one.

    GNU getopt_long accepts any unambiguous prefix: ``sed --in-pl`` and
    ``sed --i`` are ``--in-place``, ``sort --outp`` is ``--output``,
    ``tar --rem`` is ``--remove-files``. Comparing the exact spelling let every
    abbreviation through (round 3, R3-SEC-1). An ambiguous prefix is refused by
    the tool itself, so matching it here only errs towards a deny."""
    name = _long_opt_name(tok)
    return bool(name) and any(f.startswith(name) for f in fulls)


def _long_opt_value(arg_toks: list[str], i: int) -> "str | None":
    """Value of the long option at *i*: after ``=``, else the next token."""
    t = arg_toks[i]
    if "=" in t:
        return t.split("=", 1)[1]
    return arg_toks[i + 1] if i + 1 < len(arg_toks) else None


def _has_unknown_long_opt(args: list[str], known: "frozenset[str]") -> bool:
    """A long option that is not spelled exactly like a known harmless one.
    That covers every abbreviation and every option this gate has not vetted:
    the invocation is then not a proven read."""
    for t in args:
        if t == "--":
            break
        name = _long_opt_name(t)
        if name and name not in known:
            return True
    return False


_SED_READ_LONG = frozenset({"--quiet", "--silent", "--expression", "--regexp-extended",
                            "--null-data", "--separate", "--unbuffered", "--line-length",
                            "--posix", "--debug", "--sandbox", "--help", "--version"})
_SORT_READ_LONG = frozenset({
    "--check", "--unique", "--reverse", "--numeric-sort", "--key", "--field-separator",
    "--ignore-case", "--stable", "--general-numeric-sort", "--human-numeric-sort",
    "--month-sort", "--version-sort", "--random-sort", "--sort", "--merge",
    "--zero-terminated", "--dictionary-order", "--ignore-leading-blanks",
    "--ignore-nonprinting", "--buffer-size", "--parallel", "--batch-size", "--debug",
    "--files0-from", "--random-source", "--help", "--version"})
_UNIQ_READ_LONG = frozenset({"--count", "--repeated", "--all-repeated", "--skip-fields",
                             "--group", "--ignore-case", "--skip-chars", "--unique",
                             "--zero-terminated", "--check-chars", "--help", "--version"})
_AWK_READ_LONG = frozenset({"--field-separator", "--assign", "--re-interval", "--posix",
                            "--traditional", "--characters-as-bytes", "--sandbox",
                            "--version", "--help", "--non-decimal-data", "--use-lc-numeric",
                            "--bignum", "--csv", "--no-optimize", "--optimize"})
_TAR_READ_LONG = frozenset({
    "--create", "--list", "--diff", "--compare", "--file", "--gzip", "--gunzip", "--bzip2",
    "--xz", "--zstd", "--lzma", "--lzip", "--lzop", "--auto-compress", "--verbose",
    "--exclude", "--exclude-from", "--exclude-vcs", "--exclude-vcs-ignores",
    "--exclude-caches", "--exclude-backups", "--directory", "--files-from", "--null",
    "--totals", "--numeric-owner", "--owner", "--group", "--mode", "--mtime", "--sort",
    "--format", "--transform", "--xform", "--dereference", "--hard-dereference",
    "--one-file-system", "--no-recursion", "--recursion", "--absolute-names",
    "--ignore-failed-read", "--warning", "--anchored", "--no-anchored", "--wildcards",
    "--no-wildcards", "--checkpoint", "--verify", "--sparse", "--acls", "--no-acls",
    "--xattrs", "--no-xattrs", "--selinux", "--no-selinux", "--blocking-factor",
    "--record-size", "--strip-components", "--newer", "--after-date", "--newer-mtime",
    "--full-time", "--utc", "--quoting-style", "--show-transformed-names", "--help",
    "--version", "--posix", "--no-auto-compress", "--ignore-zeros", "--block-number",
    "--to-stdout", "--label", "--seek", "--no-seek", "--force-local", "--pax-option",
    "--clamp-mtime", "--no-same-owner", "--no-same-permissions"})
#: tar options that delete, overwrite or run something although the mode is
#: create / list (compared as PREFIXES, see ``_long_opt_is``).
_TAR_SIDE_EFFECT_LONG = ("--remove-files", "--delete", "--unlink-first", "--recursive-unlink",
                         "--to-command", "--checkpoint-action", "--use-compress-program",
                         "--rsh-command", "--info-script", "--new-volume-script",
                         "--index-file", "--listed-incremental", "--volno-file")
_TAR_MODE_LONG = {"--create": "c", "--list": "t", "--extract": "x", "--get": "x",
                  "--append": "r", "--update": "u", "--catenate": "A",
                  "--concatenate": "A", "--delete": "D", "--diff": "d", "--compare": "d"}
#: Short tar options that take an argument (old-style clusters consume the
#: following words in this order: ``tar cfg ARCHIVE SNAPSHOT``).
_TAR_ARG_LETTERS = "fbCTXgIKLNVFH"
#: Short tar options a create / list / compare run may carry and still be a read.
_TAR_READ_LETTERS = frozenset("ctdfvzjJaWhpPlSCTXbBioON")
_CP_READ_LONG = frozenset({
    "--archive", "--recursive", "--preserve", "--no-preserve", "--force", "--verbose",
    "--no-clobber", "--update", "--dereference", "--no-dereference", "--parents",
    "--reflink", "--sparse", "--target-directory", "--no-target-directory",
    "--one-file-system", "--strip-trailing-slashes", "--help", "--version"})
_COMPRESS_READ_LONG = frozenset({
    "--stdout", "--to-stdout", "--list", "--test", "--keep", "--verbose", "--quiet",
    "--decompress", "--uncompress", "--force", "--fast", "--best", "--threads",
    "--no-name", "--name", "--long", "--ultra", "--help", "--version", "--robot",
    "--format", "--check", "--memlimit", "--single-stream", "--no-sparse"})
_ZIP_READ_LONG = frozenset({"--recurse-paths", "--quiet", "--verbose", "--junk-paths",
                            "--symlinks", "--exclude", "--include"})
#: Commands whose arguments carry a PROGRAM (a sed / awk / perl script): a
#: protected path can sit inside such a token, relative to the cwd.
_SCRIPT_ARG_CMDS = ("sed", "gsed", "awk", "gawk", "mawk", "nawk", "perl", "ruby")
_NUMERIC_ARG_RE = re.compile(r"[+-]?(?:0[xX][0-9A-Fa-f]+|\d+)[kKmMgG]?")


def _sed_scripts(args: list[str]) -> "list[str] | None":
    """The script texts of a sed invocation (every ``-e`` / ``--expression``
    value, else the first positional), or None when the option set is not one
    a pure read can carry (``-i``, ``-f``, an unknown short option)."""
    scripts: list[str] = []
    positional: list[str] = []
    explicit = False
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--":
            positional.extend(args[i + 1:])
            break
        if t.startswith("--"):
            name = _long_opt_name(t)
            if name == "--expression":
                explicit = True
                val = _long_opt_value(args, i)
                if val is None:
                    return None
                scripts.append(val)
                if "=" not in t:
                    i += 1
            elif name == "--line-length" and "=" not in t:
                i += 1
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            for j, ch in enumerate(cluster):
                if ch == "e":
                    explicit = True
                    rest = cluster[j + 1:]
                    if rest:
                        scripts.append(rest)
                    elif i + 1 < len(args):
                        scripts.append(args[i + 1])
                        i += 1
                    else:
                        return None
                    break
                if ch == "l":
                    if not cluster[j + 1:]:
                        i += 1
                    break
                if ch not in "nEruzs":
                    return None
        else:
            positional.append(t)
        i += 1
    if not explicit:
        if not positional:
            return None
        scripts.append(positional[0])
    return scripts


def _sed_script_reads_only(script: str) -> bool:
    """True when *script* is a sed program that can only print, delete or
    transform the pattern space: addresses plus ``p P d D q Q n N l h H g G x =
    z F``, ``s`` without the ``w`` / ``e`` flags, ``y``, labels and branches,
    ``a`` / ``i`` / ``c`` text and ``r`` / ``R`` (which read). Any ``w``, ``W``
    or ``e`` command, any ``s///w`` / ``s///e`` and anything this parser does
    not recognise make it False.

    A real parser, not a regex: the text inside ``/.../`` is an address or a
    pattern, never a command, so ``/write w something/p`` is a read while
    ``$w F``, ``1,$w F``, ``0~1w F``, ``s/a/b/gw F`` and ``1e cmd`` are not."""
    s = script
    n = len(s)
    i = 0

    def _delimited(i: int, d: str) -> int:
        """Index after the next unescaped *d* from *i*, or -1."""
        while i < n:
            if s[i] == "\\":
                i += 2
                continue
            if s[i] == d:
                return i + 1
            i += 1
        return -1

    def _digits(i: int) -> int:
        while i < n and s[i].isdigit():
            i += 1
        return i

    def _address(i: int) -> int:
        """Index after one address at *i* (unchanged when there is none), -1 on error."""
        if i < n and s[i].isdigit():
            i = _digits(i)
            if i < n and s[i] == "~":
                i = _digits(i + 1)
            return i
        if i < n and s[i] == "$":
            return i + 1
        if i < n and s[i] in "/\\":
            if s[i] == "\\":
                i += 1
                if i >= n:
                    return -1
            i = _delimited(i + 1, s[i])
            if i < 0:
                return -1
            while i < n and s[i] in "IM":
                i += 1
            return i
        return i

    def _blank(i: int) -> int:
        while i < n and s[i] in " \t":
            i += 1
        return i

    def _to_eol(i: int) -> int:
        while i < n and s[i] != "\n":
            if s[i] == "\\":
                i += 1
            i += 1
        return i

    while True:
        while i < n and s[i] in " \t\n;}":
            i += 1
        if i >= n:
            return True
        j = _address(i)
        if j < 0:
            return False
        if j != i:
            i = _blank(j)
            if i < n and s[i] == ",":
                i = _blank(i + 1)
                if i < n and s[i] in "+~":
                    i += 1
                j = _address(i)
                if j < 0 or j == i:
                    return False
                i = j
        i = _blank(i)
        while i < n and s[i] == "!":
            i = _blank(i + 1)
        if i >= n:
            return False
        c = s[i]
        i += 1
        if c == "{":
            continue
        if c == "#":
            i = _to_eol(i)
            continue
        if c in ":btT":
            while i < n and s[i] not in ";\n":
                i += 1
            continue
        if c in "aicrR":
            i = _to_eol(i)
            continue
        if c == "s" or c == "y":
            if i >= n or s[i] in " \n\\":
                return False
            d = s[i]
            i = _delimited(i + 1, d)
            if i < 0:
                return False
            i = _delimited(i, d)
            if i < 0:
                return False
            if c == "s":
                while i < n and s[i] in "gpiIMm0123456789":
                    i += 1
        elif c in "qQlL":
            i = _digits(_blank(i))
        elif c not in "pPdDnNhHgGx=zF":
            return False  # w, W, e, or something this parser does not know
        i = _blank(i)
        if i < n and s[i] not in ";\n}":
            return False  # s///w FILE, s///e, trailing text after a command


def _script_one_liner_reads_only(args: list[str]) -> bool:
    """perl / ruby: every ``-e`` / ``-E`` program (also glued, ``-e'...'``) is
    free of anything that writes, deletes or runs, and no other option loads
    code or edits in place. No inline program -> not a proven read."""
    has_inline = False
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--":
            break
        if t.startswith("--"):
            return False
        if t.startswith("-") and len(t) > 1:
            cluster = t[1:]
            for j, ch in enumerate(cluster):
                if ch in "eE":
                    rest = cluster[j + 1:]
                    if rest:
                        code = rest
                    elif i + 1 < len(args):
                        code = args[i + 1]
                        i += 1
                    else:
                        return False
                    has_inline = True
                    if _SCRIPT_WRITE_RE.search(_SCRIPT_PLAIN_REGEX_RE.sub(" 1 ", code)):
                        return False
                    break
                if ch == "F":
                    break  # -F<pattern>: the rest is the split pattern
                if ch not in "nplawWcTtUuX0123456789":
                    return False  # -i, -M, -m, -I, -r, -s, -x, -S, -d, -C ...
        i += 1
    return has_inline


def _tar_parse(arg_toks: list[str]) -> "tuple[str | None, str, bool]":
    """(archive file, mode letters, plain) of a tar invocation. ``plain`` is
    False when a short option outside ``_TAR_READ_LETTERS`` is present (``g``
    writes a snapshot file, ``I`` / ``F`` run a program, ``x`` extracts ...)."""
    modes = ""
    archive: "str | None" = None
    plain = True
    i = 0
    first = True
    while i < len(arg_toks):
        t = arg_toks[i]
        cluster = ""
        old_style = False
        if t.startswith("--"):
            name = _long_opt_name(t)
            for full, letter in _TAR_MODE_LONG.items():
                if name and full.startswith(name):
                    modes += letter
            if name and "--file".startswith(name) and name != "--":
                val = _long_opt_value(arg_toks, i)
                if val is not None:
                    archive = val
                    if "=" not in t:
                        i += 1
        elif t.startswith("-") and len(t) > 1:
            cluster = t[1:]
        elif first and re.fullmatch(r"[A-Za-z]+", t):
            cluster = t  # old style: `tar czf a.tgz`
            old_style = True
        if cluster:
            for j, ch in enumerate(cluster):
                if ch in "ctxruAd":
                    modes += ch
                if ch not in _TAR_READ_LETTERS:
                    plain = False
                if ch in _TAR_ARG_LETTERS:
                    val = None
                    if not old_style and cluster[j + 1:]:
                        val = cluster[j + 1:]
                    elif i + 1 < len(arg_toks):
                        val = arg_toks[i + 1]
                        i += 1
                    if ch == "f" and val is not None:
                        archive = val
                    if not old_style:
                        break
        first = False
        i += 1
    return archive, modes, plain


def _tar_has_side_effect_opt(arg_toks: list[str]) -> bool:
    return any(_long_opt_is(t, *_TAR_SIDE_EFFECT_LONG) for t in arg_toks)


def _tar_archive_file(arg_toks: list[str]) -> "tuple[str | None, str]":
    """Compatibility wrapper around ``_tar_parse``."""
    archive, modes, _plain = _tar_parse(arg_toks)
    return archive, modes


def _embedded_path_pieces(tok: str) -> list[str]:
    """Path-shaped fragments INSIDE one argument: a sed ``w <file>``, an awk
    string, ``exec=rm <file>`` in a tar checkpoint action. The pieces of the
    token split at shell / script punctuation, plus - for a piece like
    ``w/abs/file`` - the part from its first ``/`` on. The whole token itself
    is not returned (the caller checks it)."""
    out: list[str] = []
    for piece in re.split(r"[\s\"'=,;(){}<>|&`]+", tok):
        piece = piece.strip()
        if not piece:
            continue
        if piece != tok:
            out.append(piece)
        if "/" in piece and not piece.startswith(("/", "~")):
            out.append(piece[piece.index("/"):])
    return out


def _copy_source_readable(src: str) -> bool:
    """A protected path a COPY tool may read: an audit chain file or a gate
    source FILE named literally. Never a glob, a tree, the anchor key / its
    marker trees or the secret vault - copying those out is not an analysis read
    (the key forges the chain)."""
    src = src.strip("\"'")
    if not src or any(c in src for c in "*?[{"):
        return False
    try:
        abs_p = _abs(src)
        abs_str = str(abs_p)
    except Exception:  # noqa: BLE001
        return False
    if any(abs_str == d or abs_str.startswith(d + os.sep) for d in _audit_key_dirs()):
        return False
    try:
        if abs_p.is_dir():
            return False
    except OSError:
        return False
    name = abs_p.name
    if name == "audit.jsonl" or name.startswith("audit.jsonl."):
        return True
    return _is_gate_source_path(abs_p, abs_str)


def _write_dest_clean(dest: "str | None", rel_ok: bool = True) -> bool:
    """True when *dest* is a concrete path OUTSIDE every protected tree.
    ``rel_ok=False`` (after a ``cd`` the hook cannot follow) additionally
    requires an absolute path: a relative name lands in the cd target."""
    if not dest:
        return False
    dest = dest.strip("\"'")
    if not dest or dest == "-":
        return bool(dest)
    if not rel_ok and not dest.startswith(("/", "~")):
        return False
    if "$" in dest or "`" in dest or any(c in dest for c in "*?["):
        return False
    return not _touches_corvin_tree(dest) and not is_protected_path(dest)


def _is_reader_invocation(toks: list[str], rel_ok: bool = True) -> bool:
    """True when this command invocation only READS its file arguments, any
    output going to stdout or to a path outside the protected trees. *toks*
    starts at the real command (wrappers and shell keywords peeled).
    Conservative: unknown command or unprovable form -> False."""
    if not toks:
        return True
    if len(toks) == 1 and toks[0] in _SHELL_NOOP_WORDS:
        return True
    name = toks[0].rsplit("/", 1)[-1]
    args = toks[1:]
    if name in _READ_ONLY_CMDS or name in ("tac", "rev", "fold", "expand", "paste", "comm",
                                           "join", "tr", "numfmt", "seq", "date", "sleep",
                                           "base64", "base32", "sha512sum", "sha224sum",
                                           "sha384sum", "zstdcat", "lz4cat", "bat"):
        if name in ("base64", "base32"):
            return not any(t == "-o" or _long_opt_is(t, "--output")
                           or (t.startswith("-o") and not t.startswith("--")) for t in args)
        # The few "read" tools that can still write a file or run a program.
        if name == "tree":
            return not any(_short_cluster_has(t, "o", "LPIH") or _long_opt_is(t, "--output")
                           for t in args)
        if name == "rg":
            return not any(_long_opt_name(t) in ("--pre", "--hostname-bin") for t in args)
        if name in ("less", "more"):
            return not any(_short_cluster_has(t, "o", "bhjkPpTtxyz#") or
                           _short_cluster_has(t, "O", "bhjkPpTtxyz#")
                           or _long_opt_is(t, "--log-file", "--LOG-FILE") for t in args)
        if name in ("zstdcat", "lz4cat"):
            if _has_unknown_long_opt(args, _COMPRESS_READ_LONG):
                return False
            if any(t.startswith("-") and not t.startswith("--") and "o" in t for t in args):
                return False
            return name != "lz4cat" or len(_all_nonflag(args)) <= 1
        return True
    if name in _AWK_NAMES:
        if _has_unknown_long_opt(args, _AWK_READ_LONG):
            return False
        if any((t in ("-i", "--include") or t.startswith(("-i", "--include=")))
               for t in args):
            return False
        if any(t in ("-f", "--file", "-E", "--exec", "-l", "--load") or t.startswith("--file=")
               for t in args):
            return False  # program / extension from a file: not inspected
        # gawk options that WRITE a file: -o/--pretty-print, -p/--profile,
        # -d/--dump-variables, -D/--debug (interactive, can write).
        if any(t[:2] in ("-o", "-p", "-d", "-D") and not t.startswith("--")
               or t.startswith(("--pretty-print", "--profile", "--dump-variables", "--debug"))
               for t in args):
            return False
        return not any(_AWK_WRITE_RE.search(t) for t in args)
    if name == "sort":
        # `--output` spelled out (`=value` or next word) or `-o`: a read of the
        # inputs when the output lies outside every protected tree. Every other
        # long option that is not a vetted one - each ABBREVIATION of --output
        # (`--out=`, `--outp`), --compress-program, --temporary-directory - makes
        # the invocation unproven.
        dests: list[str] = []
        rest: list[str] = []
        i = 0
        while i < len(args):
            t = args[i]
            if t in ("-o", "--output"):
                if i + 1 >= len(args):
                    return False
                dests.append(args[i + 1])
                i += 2
                continue
            if t.startswith("--output="):
                dests.append(t.split("=", 1)[1])
            elif t.startswith("-o") and not t.startswith("--") and len(t) > 2:
                dests.append(t[2:])
            else:
                rest.append(t)
            i += 1
        if _has_unknown_long_opt(rest, _SORT_READ_LONG):
            return False
        if any(t == "-T" or _short_cluster_has(t, "o", "kStT")
               or _short_cluster_has(t, "T", "kSto") for t in rest):
            return False
        return all(_write_dest_clean(d, rel_ok) for d in dests)
    if name in ("uniq", "xxd"):
        if name == "xxd" and any(_short_cluster_has(t, "r", "") or t in ("-revert", "--revert")
                                 for t in args):
            return False
        if name == "uniq" and _has_unknown_long_opt(args, _UNIQ_READ_LONG):
            return False
        # INPUT [OUTPUT]: the first positional is read. A second one is the
        # output file - a read of INPUT as long as it lies outside every
        # protected tree (R3-SEC-9: `uniq CHAIN /tmp/out.txt`).
        nf = [t for t in _all_nonflag(args) if not _NUMERIC_ARG_RE.fullmatch(t)]
        if len(nf) <= 1:
            return True
        return len(nf) == 2 and _write_dest_clean(nf[1], rel_ok)
    if name in ("sed", "gsed"):
        if _has_unknown_long_opt(args, _SED_READ_LONG):
            return False  # --in-place, --file and every abbreviation (`--in-pl`, `--i`)
        scripts = _sed_scripts(args)
        if scripts is None:
            return False  # -i, -f, an option this gate has not vetted
        return all(_sed_script_reads_only(sc) for sc in scripts)
    if name in ("perl", "ruby"):
        return _script_one_liner_reads_only(args)
    if name in _COMPRESS_CMDS:
        if _has_unknown_long_opt(args, _COMPRESS_READ_LONG):
            return False  # --rm, --output*, anything not vetted
        if any(t.startswith("-") and not t.startswith("--") and "o" in t for t in args):
            return False  # zstd -o FILE
        if name in ("lz4", "unlz4") and len(_all_nonflag(args)) > 1:
            return False  # lz4 INPUT OUTPUT
        return any(t in _COMPRESS_READ_FLAGS or _short_cluster_has(t, "c", "S")
                   or _short_cluster_has(t, "l", "S") or _short_cluster_has(t, "t", "S")
                   for t in args)
    if name in _TAR_NAMES:
        archive, modes, plain = _tar_parse(args)
        if not plain or not modes or any(m in modes for m in "xruAD"):
            return False
        if _has_unknown_long_opt(args, _TAR_READ_LONG) or _tar_has_side_effect_opt(args):
            return False  # --remove-files, --checkpoint-action, ... and abbreviations
        if "c" in modes:
            return _write_dest_clean(archive, rel_ok)
        return True  # list / compare
    if name == "zip":
        if _has_unknown_long_opt(args, _ZIP_READ_LONG):
            return False
        if any(t in ("-m", "--move", "-d", "--delete", "-T", "-TT", "--unzip-command",
                     "-O", "--output-file", "--out", "-b", "--temp-path")
               or t.startswith(("--output-file=", "--out="))
               or _short_cluster_has(t, "m", "bnitx") for t in args):
            return False
        nf = _all_nonflag(args)
        return bool(nf) and _write_dest_clean(nf[0], rel_ok)
    if name == "cp":
        if _has_unknown_long_opt(args, _CP_READ_LONG):
            return False  # --remove-destination, --link, --backup, abbreviations
        if any(t in ("-l", "-s") or _short_cluster_has(t, "l", "tS")
               or _short_cluster_has(t, "s", "tS") for t in args):
            return False
        nf = [t for t in _all_nonflag(args)]
        dest = _target_dir_flag(args) or (nf[-1] if nf else None)
        if not _write_dest_clean(dest, rel_ok):
            return False
        sources = nf if _target_dir_flag(args) else nf[:-1]
        if not sources:
            return False
        return all((not _touches_corvin_tree(src)) or _copy_source_readable(src)
                   for src in sources)
    if name in ("python", "python3") or re.fullmatch(r"python3\.\d+", name):
        # `python3 -m json.tool FILE` pretty-prints to stdout; a second
        # positional is its OUTPUT file.
        if len(args) >= 2 and args[0] == "-m" and args[1] == "json.tool":
            return len(_all_nonflag(args[2:])) <= 1
        return False
    return False


def _git_write_check(arg_toks: list[str]) -> tuple[list[str], bool]:
    """(write_targets, fail_closed) for a ``git`` invocation.

    ``git -C <dir> clean -fdx`` deletes every ignored file below ``<dir>`` -
    the runtime root of a checkout (``<repo>/.corvin``, git-ignored) with its
    audit chain included; ``git checkout/restore/rm/mv -- <path>`` rewrites or
    removes the named work-tree file. Whole-tree operations without a path
    (``reset --hard``, ``stash``, a branch switch) restore COMMITTED content
    and are not gated."""
    base = ""
    i = 0
    while i < len(arg_toks):
        t = arg_toks[i]
        if t == "-C" and i + 1 < len(arg_toks):
            base = os.path.join(base, arg_toks[i + 1]) if base else arg_toks[i + 1]
            i += 2
        elif t.startswith("-C") and len(t) > 2:
            base = os.path.join(base, t[2:]) if base else t[2:]
            i += 1
        elif t == "--work-tree" and i + 1 < len(arg_toks):
            base = arg_toks[i + 1]
            i += 2
        elif t.startswith("--work-tree="):
            base = t.split("=", 1)[1]
            i += 1
        elif t in ("-c", "--git-dir", "--namespace", "--exec-path", "--super-prefix",
                   "--config-env") and i + 1 < len(arg_toks):
            i += 2
        elif t.startswith("-"):
            i += 1
        else:
            break
    if i >= len(arg_toks):
        return [], False
    sub, rest = arg_toks[i], arg_toks[i + 1:]

    def _in_base(a: str) -> str:
        a = a.strip("\"'")
        if base and not a.startswith(("/", "~", "$")):
            return os.path.join(base, a)
        return a

    if sub == "clean":
        if any(t in ("-n", "--dry-run") or _short_cluster_has(t, "n", "e") for t in rest):
            return [], False
        paths = [_in_base(a) for a in _all_nonflag(rest)]
        removes_ignored = any(_short_cluster_has(t, "x", "e") or _short_cluster_has(t, "X", "e")
                              for t in rest)
        roots = paths or [base or "."]
        for r in roots:
            if is_protected_path(r) or _dir_at_or_under_corvin_home(r):
                return [], True
            if removes_ignored and _touches_corvin_tree(r):
                return [], True
        return [], False
    if sub in _GIT_PATH_WRITERS:
        return [_in_base(a) for a in _all_nonflag(rest)], False
    return [], False


#: Why the last ``_bash_targets`` call failed closed ("" = the generic reason).
#: A module global on purpose: the function has ~40 ``return [], True`` sites
#: and one process handles one hook call.
_FAIL_DETAIL = ""


def _fail(detail: str = "") -> tuple[list[str], bool]:
    global _FAIL_DETAIL
    _FAIL_DETAIL = detail
    return [], True


def _strict_store_detail(cmd_name: str, target: str) -> str:
    return ("`" + cmd_name[:40] + "` names " + target[:160] + ", a strict store (audit chain, "
            "its anchors, a session ledger), and this invocation is not a proven read")


def _fail_closed_message(cmd: str) -> str:
    """The deny text for a fail-closed Bash verdict (R2-SEC-5). The old text -
    "Bash command unparseable ... use the forge MCP tools" - was wrong for the
    common case: a perfectly parseable `awk ... audit.jsonl`, for which no MCP
    tool is the answer."""
    why = _FAIL_DETAIL or (
        "the command references a protected path (audit chain, session ledger, gate "
        "source, secrets, runtime tree) in a form the gate cannot prove to be read-only "
        "(command substitution, heredoc, inline interpreter, eval/xargs/sh -c, or an "
        "unparseable segment)")
    return (
        "path_gate: denied (fail-closed) - " + why + ". Reading is allowed: cat, head, "
        "tail, grep, jq, wc, sort, uniq, awk/sed/perl without an output file, sha256sum, "
        "xxd, `tar c`/`gzip -c`/`cp` to a path OUTSIDE the protected trees. Writes to "
        "forge / skill-forge state go through their MCP tools; a gate source file is "
        "edited in a git worktree and merged (docs/claude-ref/layer-10-path-gate.md). "
        "Command (first 80 chars): " + cmd[:80]
    )


def _bash_targets(cmd: str, *, powershell: bool = False) -> tuple[list[str], bool]:
    """Return (target_paths, fail_closed).

    ``powershell`` is True for the PowerShell tool: its read cmdlets and the
    ``$x = <read cmdlet> <path>`` assignment form count as reads there (in a
    POSIX shell ``$x = cat f`` RUNS ``$x``, so the form is never a read).

    fail_closed=True means we could not parse safely AND the command looks
    like it might touch a protected path. The caller treats this as a deny.
    """
    global _FAIL_DETAIL
    _FAIL_DETAIL = ""
    targets: list[str] = []

    # eval / exec → only fail-closed if a protected hint is in the command,
    # otherwise normal commands like `eval $PROMPT` (touching nothing of
    # ours) stay allowed.
    if re.search(r"(?:^|[\s;&|])(?:eval|exec)\b", cmd) and _looks_protected(cmd):
        return [], True

    # V-007: exec N> file descriptor redirect — e.g. `exec 3> /protected/path`.
    # The exec+digit pattern is distinct from the generic eval/exec block above
    # because `exec 3>` does NOT match \bexec\b followed by a word boundary
    # that would be caught before. Extract the target path when possible;
    # fail-closed on ambiguity when the command carries a protected hint.
    if re.search(r'\bexec\s+\d+>', cmd):
        m = re.search(r'\bexec\s+\d+>\s*([^\s;&|<>]+)', cmd)
        if m:
            targets.append(m.group(1))
        elif _looks_protected(cmd):
            return [], True  # fail-closed: exec fd redirect with protected hint

    # Command substitution with a protected hint inside → fail-closed.
    if (re.search(r"\$\(", cmd) or "`" in cmd) and _looks_protected(cmd):
        return [], True


    # V-013: Command substitution in pipe position — e.g.
    # `$(output) | tee forge/out.py` or `echo x | tee $(some_path)`.
    # The general $() check above already catches many of these; this
    # pattern is kept explicit for documentation and for cases where the
    # protected hint appears only in the tee destination, not in the
    # substitution body.
    if (re.search(r'(\$\(|`)[^)]*(\)|`)\s*\|', cmd)
            or re.search(r'\|\s*tee\b.*(\$\(|`)', cmd)):
        if _looks_protected(cmd):
            return [], True  # fail-closed: substitution in write-adjacent position

    # Layer-16 v2 vectors — opaque write targets, fail-closed on hint:

    # V-007: mkfifo — named pipe creation that could proxy a write to a
    # protected path. The actual write target is determined at runtime by
    # whatever consumer reads from the named pipe, so we cannot resolve it
    # statically; fail-closed when a protected hint is present.
    if re.search(r'\bmkfifo\b', cmd) and _looks_protected(cmd):
        return [], True  # fail-closed

    # V-007: >(cmd) process substitution write side — can proxy writes to
    # protected paths through a sub-shell. Checked here (before the inner-
    # content check below) to catch the case where the hint is in the
    # surrounding command rather than inside the substitution body.
    if re.search(r'>\s*\(', cmd) and _looks_protected(cmd):
        return [], True  # fail-closed

    # Process substitution >(<inner>) — sub-shell writes whatever it wants.
    # If the inner part references a protected hint, deny.
    for m in _PROC_SUBST_OUT_RE.finditer(cmd):
        if _looks_protected(m.group(1)):
            return [], True

    # Recursive shell wrappers (bash -c, sh -c, env -i bash -c, etc.).
    # The single-string body can hide arbitrary redirects from our parsers.
    if _RECURSIVE_SHELL_RE.search(cmd) and _looks_protected(cmd):
        return [], True

    # xargs — replacement {} is filled at runtime; we cannot resolve the
    # actual write target.
    if _XARGS_RE.search(cmd):
        if _looks_protected(cmd):
            return [], True
        # `find <corvin-tree> -name '*.jsonl' | xargs rm` — the find is read-only
        # (no -delete) so the find branch never fires, and a glob carries no
        # literal protected hint, yet xargs feeds the matched audit logs to rm.
        # Catch the pipe-to-destructive form: any bare path token that touches the
        # corvin tree, when a destructive verb is present (round-3).
        _XARGS_DESTRUCTIVE = ("rm", "rmdir", "unlink", "shred", "truncate",
                              "chmod", "chown", "chgrp", "mv", "ln")
        _cmd_toks = re.split(r"\s+", cmd)
        if any(v in _cmd_toks for v in _XARGS_DESTRUCTIVE):
            for _tk in _cmd_toks:
                if _tk and not _tk.startswith("-") and _touches_corvin_tree(_tk):
                    return [], True

    # awk -i inplace — rewrites the positional file arg in place. The
    # positional arg can drift across pipes and substitutions; fail-closed
    # is the safer call.
    if _AWK_INPLACE_RE.search(cmd) and _looks_protected(cmd):
        return [], True

    # Heredoc with a protected hint — the body content may contain a
    # redirect we cannot enumerate. Strict but small false-positive surface.
    if _HEREDOC_RE.search(cmd) and _looks_protected(cmd):
        return [], True

    # Inline interpreter one-liner touching a protected hint → opaque write, fail
    # closed (open(mode="w") / os.open / write_text / fs.writeFileSync evade the
    # positional-open regex below).
    if _INLINE_INTERP_RE.search(cmd) and _looks_protected(cmd):
        return [], True

    for m in _REDIRECT_RE.finditer(cmd):
        # Strip surrounding quotes: `> "$HOME/.corvin/audit.jsonl"` captured the
        # quotes as literal path chars, so _abs treated the leading `"` as a
        # non-absolute token that never matched the protected tree (bypass C).
        targets.append(m.group(1).strip("'\""))
    for m in _PY_OPEN_RE.finditer(cmd):
        targets.append(m.group(1))

    segs = _split_segments(cmd)
    # Literal `cd` / `pushd` destinations of this command: a relative write
    # target in a later segment lands under one of them (review 2026-11-19 SEC-2).
    _cd_dirs: list[str] = []
    for _seg in segs:
        try:
            _ct = _split_tokens(_seg)
        except ValueError:
            continue
        if (_ct and _ct[0].rsplit("/", 1)[-1] in ("cd", "pushd") and len(_ct) > 1
                and not _ct[1].startswith("-")):
            _cd_dirs.append(_ct[1])

    # cd-into-tree backstop (round-4): the hook runs in its own subprocess and
    # cannot observe a `cd`, so `cd ~/.corvin && rm -rf tenants` resolved the
    # relative `tenants` against the hook's cwd and passed. If ANY segment cd's
    # into the corvin tree AND ANY segment runs a destructive command, fail
    # closed — the relative target would land inside the protected tree at run
    # time. (cd into an unrelated dir, or cd-into-tree + a read like cat/ls, is
    # not blocked.)
    # A `cd` INTO a ledger-class store (session ledger, anchors, notification
    # queue, console turn logs): only reads may follow, any write — `sed -i`,
    # `sort -o`, `patch`, `touch`, a redirect — to a relative name lands in the
    # record (review R10-2).
    for _seg in segs:
        try:
            _ct = _split_tokens(_seg)
        except ValueError:
            continue
        if (_ct and _ct[0].rsplit("/", 1)[-1] in ("cd", "pushd") and len(_ct) > 1
                and _is_read_only_tree(_ct[1])):
            for _other in segs:
                try:
                    _ot = _strip_cmd_wrappers(_strip_shell_keywords(_split_tokens(_other)))
                except ValueError:
                    return _fail("a command after `cd` into a read-only store could not be parsed")
                # Only reads may follow. A reader invocation (R2-SEC-5: `sort`,
                # `awk '{print}'`, `sha256sum`, ...) counts; any redirect does
                # not - its relative target would land in the store.
                if _ot and (not _is_reader_invocation(_ot, rel_ok=False)
                            or _REDIRECT_RE.search(_other)):
                    return _fail(
                        "`cd` into a read-only store (audit chain / session ledger / "
                        "protected runtime tree) followed by `" + _ot[0][:40] + "`, which the "
                        "gate cannot prove to be a read")
    _cd_into_tree = any(_seg_chdirs_into_tree(_seg) for _seg in segs)
    if _cd_into_tree:
        _DESTRUCTIVE_ALL = (_TARGET_ALL_CMDS + _DEST_LAST_CMDS + _FIND_CMDS
                            + _ARCHIVE_CMDS + ("dd", "tee"))
        for _seg in segs:
            try:
                _st = _strip_cmd_wrappers(_split_tokens(_seg))
            except ValueError:
                return [], True
            if (_st and _st[0].rsplit("/", 1)[-1] in _DESTRUCTIVE_ALL
                    # R2-SEC-5: `tar cf /abs/outside.tar <file>` / `cp <file>
                    # /abs/outside` after the cd only read the tree.
                    and not _is_reader_invocation(_st, rel_ok=False)):
                return _fail("`" + _st[0][:40] + "` after a `cd` into the protected runtime "
                             "tree: a relative target would land inside it")
            # A write-redirect with a RELATIVE target after a cd into the tree
            # lands inside the protected tree at run time (bypass A): `: >
            # audit.jsonl`, `printf {} > secrets.json` use builtins not in
            # _DESTRUCTIVE_ALL, so cover the redirect vector itself. Absolute
            # targets are resolvable and handled by the normal target path, so
            # only relative ones fail closed here.
            for _rm in _REDIRECT_RE.finditer(_seg):
                _rt = _rm.group(1).strip("'\"")
                if _rt and not _rt.startswith(("/", "~", "$")):
                    return [], True

    # sed -i takes its target as the last positional arg of the segment.
    for seg in segs:
        try:
            toks = _split_tokens(seg)
        except ValueError:
            if _looks_protected(seg):
                return [], True
            continue
        if not toks:
            continue
        # Peel leading exec wrappers (`env`/`sudo`/`nice`/`timeout`/`busybox`/…)
        # so cmd_name is the REAL command, not the wrapper (round-4 CRITICAL).
        toks = _strip_cmd_wrappers(_strip_shell_keywords(toks))
        if not toks:
            continue
        cmd_name = toks[0].rsplit("/", 1)[-1]
        # `for f in <words>`: the word list itself runs nothing. When it names a
        # strict store, the loop body receives that path in a variable the hook
        # cannot follow, so every OTHER segment must be a proven read
        # (`for f in audit.jsonl; do wc -l "$f"; done` yes, `...; do rm "$f"` no).
        _loop_words = _for_loop_words(toks)
        if _loop_words is not None:
            if any(_loop_word_is_protected(_w) for _w in _loop_words):
                for _other in segs:
                    if _other is seg:
                        continue
                    try:
                        _ot = _strip_cmd_wrappers(_strip_shell_keywords(_split_tokens(_other)))
                    except ValueError:
                        return _fail("a command in a loop over a protected path could not be parsed")
                    if _ot and (not _is_reader_invocation(_ot) or _REDIRECT_RE.search(_other)
                                and not all(_write_dest_clean(_m.group(1))
                                            for _m in _REDIRECT_RE.finditer(_other))):
                        return _fail(
                            "a loop over a protected path runs `" + _ot[0][:40] + "`, which "
                            "the gate cannot prove to be a read")
            continue
        _reader = _is_reader_invocation(toks)
        # A non-read command with a ledger-class store path ANYWHERE in its
        # arguments (also `-P`/`-C`/`-o<path>` forms) fails closed: in-place
        # editors (`perl -i -pe`, `ruby -i`, `gawk -i inplace`, vi/vim/nano/
        # emacs), downloaders (`wget -P`), `git -C`, `zip`, `base64 -o` — the
        # list of writers is open-ended, the list of these stores is not
        # (review R10-3). Reads (cat/grep/…) stay allowed.
        _ro_toks = toks
        if powershell and len(toks) >= 3 and toks[0].startswith("$") and toks[1] == "=":
            _ro_toks = toks[2:]
        _ro_name = _ro_toks[0].rsplit("/", 1)[-1]
        _is_read = (_reader
                    or (_ro_toks is not toks and _is_reader_invocation(_ro_toks))
                    or (powershell and _ro_name.lower() in _PS_READ_ONLY_CMDS))
        if not _is_read:
            for _t in toks[1:]:
                _cand = _t
                if _t.startswith("-") and "=" in _t:
                    _cand = _t.split("=", 1)[1]
                elif _t[:2] in ("-o", "-O", "-P", "-C") and len(_t) > 2:
                    _cand = _t[2:]
                _cand = _cand.strip("\"'")
                if _cand.startswith("-") or not _cand:
                    continue
                if _is_strict_store_path(_cand):
                    return _fail(_strict_store_detail(cmd_name, _cand))
                # A relative name after a literal `cd <dir>` is resolved by the
                # shell against <dir>, not against the hook's cwd.
                if not _cand.startswith(("/", "~", "$")) and any(
                        _is_strict_store_path(os.path.join(_d, _cand)) for _d in _cd_dirs):
                    return _fail(_strict_store_detail(cmd_name, _cand))
                # The path INSIDE a token (round 3, R3-SEC-1): `sed -n 'w <chain>'`,
                # `sed '1e unlink <ledger>'`, an awk string, `--checkpoint-action=
                # exec=rm <ledger>`. git / gh carry free text (a commit message
                # may name the chain) and have their own write check.
                if cmd_name in ("git", "gh"):
                    continue
                for _piece in _embedded_path_pieces(_t):
                    if _piece.startswith(("/", "~")):
                        if _is_strict_store_path(_piece):
                            return _fail(_strict_store_detail(cmd_name, _piece))
                    elif (cmd_name in _SCRIPT_ARG_CMDS
                          and re.fullmatch(r"[\w./+-]+", _piece)
                          and ("." in _piece or "/" in _piece)):
                        if (_is_audit_integrity_path(_piece)
                                or ("/" in _piece and _is_ledger_store_path(_piece))
                                or any(_is_strict_store_path(os.path.join(_d, _piece))
                                       for _d in _cd_dirs)):
                            return _fail(_strict_store_detail(cmd_name, _piece))
        # In-place writers and git path writers: every path argument is a write
        # target, checked against the FULL protected set (review 2026-11-19 SEC-2).
        if not _reader:
            targets.extend(_inplace_write_candidates(cmd_name, toks[1:]))
        if cmd_name == "git":
            _git_targets, _git_closed = _git_write_check(toks[1:])
            if _git_closed:
                return [], True
            targets.extend(_git_targets)
        # The output option of a command that writes a file names a target (R9-4).
        if cmd_name in _OUTPUT_FLAG_CMDS:
            for _i, _t in enumerate(toks[1:], 1):
                if _t in _OUTPUT_FLAGS and _i + 1 < len(toks):
                    targets.append(toks[_i + 1])
                elif _t.startswith(("--output=", "--output-document=", "-out=")):
                    targets.append(_t.split("=", 1)[1])
                elif len(_t) >= 4 and _long_opt_is(_t, "--output", "--output-document"):
                    # GNU prefix abbreviation: `sort --outp F`, `sort --out=F`.
                    _ov = _long_opt_value(toks, _i)
                    if _ov is not None:
                        targets.append(_ov)
                elif _t[:2] in ("-o", "-O") and len(_t) > 2 and not _t.startswith("--"):
                    targets.append(_t[2:])
        if cmd_name in _ARCHIVE_CMDS:
            # Archive EXTRACTION whose destination touches the corvin tree can
            # overwrite audit.jsonl. Fail closed on an explicit -C/-d/-D/-o dest
            # under/at/ancestor-of the home; also on any bare token touching the
            # tree (belt-and-suspenders for odd flag forms). Reading the tree into
            # an archive is a read and stays allowed (round-4).
            arg_toks = toks[1:]
            for d in _archive_dest_dirs(cmd_name, arg_toks):
                if _touches_corvin_tree(d):
                    return [], True
            # `tar cf o.tar <file> --remove-files` (also `--rem`) deletes its
            # members, `--checkpoint-action=exec=...` / `--to-command` run a
            # program: every member is a write target then (round 3).
            if cmd_name in _TAR_NAMES and _tar_has_side_effect_opt(arg_toks):
                for a in _all_nonflag(arg_toks):
                    if _touches_corvin_tree(a):
                        return _fail("`" + cmd_name + "` with an option that deletes its "
                                     "members or runs a program, on a path inside the "
                                     "protected runtime tree: " + a[:120])
                targets.extend(_all_nonflag(arg_toks))
        elif cmd_name == "sed" and any(t == "-i" or t.startswith("-i") for t in toks[1:]):
            t = _last_nonflag(toks[1:])
            if t:
                targets.append(t)
        elif cmd_name == "tee":
            # tee writes to ALL non-flag arguments — capture every one.
            # Flags: -a/--append, -i/--ignore-interrupts, -p/--output-error,
            # --output-error=<mode>, -- (end-of-options).
            # V-015: 'echo x | tee /tmp/safe /forge/audit.jsonl' must deny
            # on the second argument as well as the first.
            past_opts = False
            for tok in toks[1:]:
                if past_opts:
                    targets.append(tok)
                elif tok == "--":
                    past_opts = True
                elif tok.startswith("-"):
                    pass  # skip flag (tee flags are boolean or =value; no separate arg)
                else:
                    targets.append(tok)
        elif cmd_name == "dd":
            # dd of=<path> — scan all tokens, since `of=` may appear
            # anywhere on the command line (after if=, bs=, etc.).
            for tok in toks[1:]:
                if tok.startswith("of="):
                    targets.append(tok[3:])
        elif cmd_name in _DEST_LAST_CMDS:
            # mv/cp/install/rsync: destination is -t/--target-directory= when
            # present (GNU — for ALL four, not just install), else the last
            # non-flag arg (standard `cp src dest` form).
            arg_toks = toks[1:]
            # A mv/cp/rsync that MOVES a protected SOURCE away (or over the corvin
            # tree recursively/by glob) is destructive — `mv ~/.corvin/tenants
            # /tmp/x` relocates the audit trees but only the (unprotected) dest was
            # checked before. Fail closed on ANY arg touching the corvin tree
            # (round-3). R2-SEC-5: a plain `cp` of an audit chain file or a gate
            # source to a destination outside every protected tree is a read
            # (`_is_reader_invocation`) - that is how a worktree copy of a gate
            # source or an offline copy of the chain is made.
            if not (cmd_name == "cp" and _reader):
                for a in _all_nonflag(arg_toks):
                    if _touches_corvin_tree(a):
                        return _fail(
                            "`" + cmd_name + "` with an argument inside (or above) the "
                            "protected runtime tree: " + a[:120])
            dest = (_target_dir_flag(arg_toks, abbrev=cmd_name != "rsync")
                    or _last_nonflag(arg_toks))
            if dest:
                targets.append(dest)
        elif cmd_name in _FIND_CMDS:
            # find (and gfind/bfind aliases) with a mutating action (-delete /
            # -exec rm / -fprintf ...) is a write vector. It fails closed when
            # ANY search root touches a protected tree — in EITHER direction:
            # a root under protection, OR a root that is an ANCESTOR of the
            # corvin home (find recurses down into it). The ancestor case is the
            # glob bypass: `find ~ -name '*.jsonl' -delete` never names the
            # protected file literally and its root isn't itself protected, yet
            # it deletes ~/.corvin/audit.jsonl (path-audit 2026-07-07 round-2).
            # Read-only finds (no mutating action) are untouched. The literal
            # protected-hint check and -fprint*/-fls target capture stay as
            # belt-and-suspenders.
            arg_toks = toks[1:]
            if any(t in _FIND_MUTATING for t in arg_toks):
                roots = _find_roots(arg_toks) or ["."]  # no explicit root → cwd
                if any(_find_root_touches_protected(r) for r in roots):
                    return [], True  # fail-closed: mutating find over a protected tree
                for idx, t in enumerate(arg_toks):
                    if t in ("-fprint", "-fprint0", "-fprintf", "-fls") and idx + 1 < len(arg_toks):
                        targets.append(arg_toks[idx + 1])
                if _looks_protected(seg):
                    return [], True  # fail-closed: mutating find touching a protected hint
        elif cmd_name in _CREATE_PATH_CMDS:
            # Only the ledger-class stores: a session workdir sits under the
            # corvin home, and the full protected set (memory/, admin/,
            # compute/, …) blocked `mkdir memory` in it (review R10-3).
            # The tenant learning/skills rule is anchored at the tenant root, so
            # it has no such false positive and is checked here too.
            if not _reader and any(_is_strict_store_path(a) or _is_tenant_learning_path(a)
                                   for a in _all_nonflag(toks[1:])):
                return _fail(_strict_store_detail(cmd_name, "a protected store path"))
        elif cmd_name in _TARGET_ALL_CMDS:
            # truncate/ln/chmod/chown/chattr/rm/rmdir/unlink/shred/...: EVERY
            # non-flag arg is a candidate path that can wipe / truncate /
            # perm-downgrade / symlink-replace / delete a protected file.
            nonflag = _all_nonflag(toks[1:])
            # Recursive / glob / root destructive op over the corvin tree fails
            # closed: `rm -rf ~/.corvin`, `rm ~/.corvin/*.jsonl`, `chmod -R
            # ~/.corvin/tenants`, `rm -rf ~/.corvin/global` all reach hash-chained
            # audit logs but name no protected leaf literally, so is_protected_path
            # alone missed them (round-3, empirically confirmed).
            for a in nonflag:
                if _touches_corvin_tree(a):
                    return [], True
            # is_protected_path() still filters non-path args (a mode, a size);
            # for `ln` this captures both the link source and the protected target.
            targets.extend(nonflag)
        elif cmd_name in _SCRIPTED_EDITORS or (
            cmd_name in ("vi", "vim", "nvim")
            and any(t == "-es" or t == "-e" or t == "-s" or t.startswith("-es") for t in toks[1:])
        ):
            # ex/ed/scripted-vi: the edit program is opaque (stdin / -c), so a
            # protected target is fail-closed. (e.g. `printf '1d\nwq\n' | ex audit.jsonl`)
            if _looks_protected(seg):
                return [], True
            t = _last_nonflag(toks[1:])
            if t:
                targets.append(t)

    # The same targets as the shell resolves them after a literal `cd <dir>`.
    if _cd_dirs:
        for _t in list(targets):
            _tn = _t.strip("\"'")
            if _tn and not _tn.startswith(("/", "~", "$", "-")):
                targets.extend(os.path.join(_d, _tn) for _d in _cd_dirs)

    return targets, False


# ----- PowerShell tool (native Windows) -----------------------------------
#
# Claude Code on native Windows exposes its shell tool as ``PowerShell`` (not
# ``Bash``) when it runs commands through PowerShell. Neither hooks.json's
# matcher nor check() knew that tool name, so on such a host
# ``Remove-Item ~\.corvin\...\audit.jsonl`` or ``Set-Content <forge>\policy.json``
# never reached a single L10 check (2026-11-02, verified live: the PowerShell
# tool is what Claude Code 2.1.x offers on Windows without Git Bash). The Bash
# checks still apply (redirects ``>``, inline interpreters, pinned tools, the
# Python AST gate); on top, the cmdlets / aliases that write are mapped onto
# the same target classes the Bash parser uses.

#: Destructive: every positional arg is checked with _touches_corvin_tree, as
#: for rm/mv/chmod in _bash_targets.
_PS_DESTRUCTIVE = frozenset({
    "remove-item", "ri", "rm", "rmdir", "rd", "del", "erase",
    "move-item", "mi", "mv", "move", "rename-item", "ren", "rni",
    "clear-content", "clc", "clear-item", "cli", "set-acl",
    # native tools that change attributes / ACLs / ownership of a protected file
    "icacls", "takeown", "cacls", "attrib",
})
#: Writers: every positional / ``-Param:value`` arg is checked with
#: is_protected_path (a write target, like a redirect or a cp destination).
_PS_WRITERS = frozenset({
    "set-content", "sc", "add-content", "ac", "out-file", "tee-object", "tee",
    "new-item", "ni", "md", "mkdir", "set-item", "si", "copy-item", "cpi",
    "cp", "copy", "expand-archive", "compress-archive", "export-csv",
    "export-clixml", "invoke-webrequest", "iwr", "invoke-restmethod", "irm",
    "start-bitstransfer", "new-itemproperty", "set-itemproperty",
    "robocopy", "xcopy",
})
#: Writers whose ONLY path is the first positional (or -Path/-FilePath/...);
#: later positionals are values, so an unresolved ``$x`` there is not a path.
_PS_FIRST_POSITIONAL_ONLY = frozenset({
    "set-content", "sc", "add-content", "ac", "out-file", "tee-object", "tee",
    "new-item", "ni", "set-item", "si", "export-csv", "export-clixml",
    "new-itemproperty", "set-itemproperty",
})
_PS_PATH_PARAMS = frozenset({
    "path", "filepath", "literalpath", "destination", "outfile", "target", "pspath",
})
_PS_ENC_RE = re.compile(
    r"(?i)(?:^|\s)-(?:ec|" + "|".join("encodedcommand"[:k] for k in range(1, 15)) + r")\s+(\S+)")
#: Opaque: the real target is decided at run time — fail closed when the
#: command carries a protected hint.
_PS_OPAQUE_RE = re.compile(
    r"(?i)(?:^|[\s;|&({])(?:invoke-expression|iex|start-process|saps|start|"
    r"invoke-command|icm|cmd(?:\.exe)?|powershell(?:\.exe)?|pwsh(?:\.exe)?|"
    r"wsl(?:\.exe)?|bash(?:\.exe)?|sh(?:\.exe)?)(?=$|[\s;|&)}])"
    r"|\[[\w.]*(?:file|directory|stream|writer|path)[\w.]*\]\s*::"
    r"|\.(?:writealltext|writeallbytes|writealllines|appendalltext|"
    r"appendalllines|delete|moveto|copyto|replace|create|createtext|"
    r"appendtext|openwrite|setaccesscontrol)\s*\("
    r"|&\s*\{|\.\s*\{|-encodedcommand\b|-enc\b"
    r"|(?:^|[\s;|&({])(?:new-object|add-type)(?=$|[\s;|&)}])"
)
_PS_ENV_RE = re.compile(r"(?i)\$env:([A-Za-z_][A-Za-z0-9_]*)")
_PS_HOME_RE = re.compile(r"(?i)\$home(?![A-Za-z0-9_])")


def _ps_expand(tok: str) -> str:
    """PowerShell variable forms ``_abs`` (os.path.expandvars) does not know."""
    tok = tok.strip("'\"")
    tok = _PS_ENV_RE.sub(lambda m: os.environ.get(m.group(1), m.group(0)), tok)
    return _PS_HOME_RE.sub(lambda m: str(Path.home()).replace("\\", "/"), tok)


def _ps_mentions_protected(text: str) -> bool:
    """Fail-closed hint for a command whose path operand is decided at run time
    (``$p = '...policy.json'; Set-Content $p``, ``... | Remove-Item``): does the
    command text name the corvin home / a protected leaf at all? Narrower than
    _looks_protected (no bare ``forge``/``compute``), so dev work in a repo
    directory called forge/compute is not caught."""
    low = text.lower().replace("/", "\\")
    if any(h in low for h in (".corvin", "corvin_home", "audit.jsonl", "policy.json",
                              "secrets.json", "license.jwt", "session_ledger")):
        return True
    try:
        home = _ref(_corvin_home()).lower().replace("/", "\\")
    except Exception:  # noqa: BLE001 - unresolvable -> fail closed
        return True
    return bool(home) and home in low


def _powershell_deny(cmd: str, _depth: int = 0) -> str:
    """Return a deny target/description for a PowerShell command, or ''."""
    if _PS_OPAQUE_RE.search(cmd) and _looks_protected(cmd):
        return cmd[:80]
    if _depth < 3:
        for m in _PS_ENC_RE.finditer(cmd):  # powershell -EncodedCommand <UTF-16LE base64>
            try:
                import base64  # noqa: PLC0415
                inner = base64.b64decode(m.group(1), validate=True).decode("utf-16-le")
            except Exception:  # noqa: BLE001 - undecodable payload: cannot be inspected
                return cmd[:80]
            hit = _powershell_deny(inner, _depth + 1)
            if hit or _ps_mentions_protected(inner):
                return hit or inner[:80]
    # ``${env:X}`` -> ``$env:X``; script-block braces are statement separators
    # (``... | ForEach-Object { Remove-Item $_.FullName }``).
    norm = re.sub(r"\$\{(env:)?([^}]+)\}", lambda m: "$" + (m.group(1) or "") + m.group(2), cmd)
    norm = re.sub(r"[{}]", ";", norm)
    unresolved_hint = _ps_mentions_protected(cmd)
    for seg in _split_segments(norm):
        try:
            toks = _split_tokens(seg)
        except ValueError:
            if _looks_protected(seg):
                return seg[:80]
            continue
        if not toks:
            continue
        name = toks[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
        if name.endswith(".exe"):
            name = name[:-4]
        if name not in _PS_DESTRUCTIVE and name not in _PS_WRITERS:
            continue
        first_only = name in _PS_FIRST_POSITIONAL_ONLY
        positional = 0
        has_path_arg = False
        pending = ""  # name of a preceding ``-Param`` awaiting its value
        for raw in toks[1:]:
            cand = raw
            is_path = False
            if raw.startswith("-") and len(raw) > 1 and not raw[1].isdigit():
                pname, colon, val = raw[1:].partition(":")
                if not colon:
                    pending = pname.lower()
                    continue  # a parameter name; its value is the next token
                cand = val
                is_path = pname.lower() in _PS_PATH_PARAMS
            elif pending:
                is_path = pending in _PS_PATH_PARAMS
                pending = ""
            else:
                is_path = (positional == 0) if first_only else True
                positional += 1
            cand = _ps_expand(cand)
            if not cand:
                continue
            for part in cand.split(","):  # -Path a,b
                part = part.strip().strip("'\"")
                if not part:
                    continue
                if is_path:
                    has_path_arg = True
                if "$" in part and (_looks_protected(part) or (is_path and unresolved_hint)):
                    return part  # unresolvable variable that may name a protected path
                if name in _PS_DESTRUCTIVE:
                    if _touches_corvin_tree(part):
                        return part
                elif is_protected_path(part):
                    return part
        # ``Get-ChildItem <protected> | Remove-Item``: the target arrives on the pipeline.
        if name in _PS_DESTRUCTIVE and not has_path_arg and unresolved_hint:
            return seg[:80]
    return ""


# ----- main check ---------------------------------------------------------

_OTA_GATED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "PowerShell", "WebFetch")


def _ota_license_token_present() -> bool:
    """True when a license token is configured on this host (env or key file).

    Mirrors validator._find_token()'s discovery sources EXACTLY (review LOW: a
    drift here false-denies every write-class tool on a paid install): the
    CORVIN_LICENSE_KEY env var, ``<config_dir>/session.key`` (config dir honours
    XDG_CONFIG_HOME, like validator._config_dir), and
    ``<corvin_home>/global/license.key``. It does NOT look for ``license.key`` in
    the config dir — the validator never loads that path, so treating it as a
    token would deny on a key the validator can't use. A token PRESENT but
    failing to validate is the tamper signal M5 observes from the hook
    subprocess (which cannot see the in-process canary).
    """
    # A key file counts as a token only when its content is NON-EMPTY after
    # strip — matching validator._find_token()'s `if t:` guard. A zero-byte /
    # whitespace-only key (corruption, truncated write) reads as "no token" on
    # both sides, so the free tier (no usable token) never false-denies under
    # the opt-in M5 gate (review LOW: keeps the "mirrors EXACTLY" claim true).
    def _nonempty_file(p: "Path") -> bool:
        try:
            return p.is_file() and bool(p.read_text("utf-8").strip())
        except OSError:
            return False

    if os.environ.get("CORVIN_LICENSE_KEY", "").strip():
        return True
    cfg_base = os.environ.get("XDG_CONFIG_HOME") or "~/.config"
    cfg = Path(os.path.expanduser(cfg_base)) / "corvin-voice"
    if _nonempty_file(cfg / "session.key"):
        return True
    try:
        if _nonempty_file(_corvin_home() / "global" / "license.key"):
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _ota_structural_deny(tool: str) -> "tuple[bool, str]":
    """ADR-0154 M5 (Structural Embedding) — opt-in, default OFF.

    The path-gate hook runs as a FRESH subprocess per tool call, so it cannot
    observe the adapter's in-process tamper canary. The signal it *can* observe
    is a license token that is **present on disk but fails to validate** — a
    tampered / corrupted / wrong-key license. When ``CORVIN_OTA_PATH_GATE=1`` and
    that condition holds, the gate denies all write-class tools.

    This is the M5 "wrong license → writes blocked" deterrent, in the ONLY
    direction a fail-closed compliance gate may move — toward MORE protection,
    never less (CLAUDE.md / GDPR Art. 32: path-gate must never fail-open).

    Free-tier-safe: with NO token present (Apache-core), this never denies.
    Default OFF (ADR-0154 § Accepted Risks): with the flag unset the gate is
    byte-for-byte unchanged. Best-effort: any import/compute failure falls
    through to the base gate (a healthy install is never bricked by infra error).
    """
    if os.environ.get("CORVIN_OTA_PATH_GATE") != "1":
        return False, ""
    if tool not in _OTA_GATED_TOOLS:
        return False, ""
    if not _ota_license_token_present():
        return False, ""  # free tier / no license — nothing to validate
    try:
        import sys as _sys
        _op = str(Path(__file__).resolve().parents[2])  # operator/ for license.*
        if _op not in _sys.path:
            _sys.path.insert(0, _op)
        from license.validator import load_license_from_env, is_loaded  # type: ignore

        load_license_from_env()  # idempotent; loads once per subprocess
        # A token is present but did NOT activate → present-but-invalid =
        # tampered/wrong/corrupt license. Deny gated writes.
        if not is_loaded():
            return True, (
                "path_gate: license integrity assertion failed — write-class "
                "tools are blocked until the instance is re-validated. "
                "(ADR-0154 M5)"
            )
    except Exception:  # noqa: BLE001
        # Never brick on infra failure — fall through to the base gate.
        return False, ""
    return False, ""


#: A worker starting the skill-forge / forge MCP server itself (refutation 2026-11-05,
#: RL-5): such a server reads its caller persona from the env the WORKER gave it. An
#: interpreter followed - in the same command segment - by the server module/script.
#: Reading the file (cat, grep, sed) is untouched. A naive-start guard, not isolation.
_MCP_SERVER_SELF_START_RE = re.compile(
    r"(?:^|[\s/=])(?:python[0-9.]*|pypy[0-9.]*|py|uv|uvx)(?:\s|$)[^;&|\n\r]*?"
    r"(?:-m\s*skill_forge\.mcp_server\b|skill_forge[/\\]*mcp_server\.py\b"
    r"|forge\.py\s+mcp\b|-m\s*forge\s+mcp\b)")
_MCP_SERVER_SELF_START_HINT = (
    "path_gate: a worker may not start the skill-forge / forge MCP server itself - its "
    "caller persona would be whatever this command sets (attribution, not identity). "
    "Use the mcp__skill_forge__* / mcp__forge__* tools this session already has.")


def check(payload: dict) -> tuple[bool, str]:
    """Return (allow, reason). allow=False means deny.

    FAIL-CLOSED (F-A8, 2026-09-07): any exception inside the gate is a DENY,
    never an allow. Before this wrapper an uncaught error propagated out of
    ``main()`` as a traceback with a non-2 exit status, which Claude Code
    treats as a *non-blocking* hook error — i.e. the protected write went
    through. ``docs/claude-ref/layer-10-path-gate.md``: "Don't fail-open".
    """
    try:
        return _check_unguarded(payload)
    except Exception as exc:  # noqa: BLE001 — a gate that cannot decide denies
        return False, f"path_gate internal error ({type(exc).__name__}) — fail-closed deny"


def _check_unguarded(payload: dict) -> tuple[bool, str]:
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    if not isinstance(inp, dict):
        return True, ""

    # ADR-0154 M5 (gated, default OFF): structural license-integrity coupling.
    _ota_deny, _ota_reason = _ota_structural_deny(tool)
    if _ota_deny:
        return False, _ota_reason

    if tool in ("Write", "Edit", "MultiEdit"):
        p = inp.get("file_path") or inp.get("path")
        if isinstance(p, str) and is_protected_path(p):
            return False, _deny_msg(tool, p)
        # MultiEdit carries a list of {file_path, ...} dicts — each must be checked.
        if tool == "MultiEdit":
            for edit in (inp.get("edits") or []):
                if not isinstance(edit, dict):
                    continue
                ep = edit.get("file_path") or edit.get("path")
                if isinstance(ep, str) and is_protected_path(ep):
                    return False, _deny_msg("MultiEdit", ep)

    elif tool == "NotebookEdit":
        p = inp.get("notebook_path") or inp.get("file_path")
        if isinstance(p, str) and is_protected_path(p):
            return False, _deny_msg(tool, p)

    elif tool in ("Bash", "PowerShell"):
        cmd = inp.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return True, ""
        if _MCP_SERVER_SELF_START_RE.search(re.sub(r"[\"'\\]", "", cmd)):
            return False, _MCP_SERVER_SELF_START_HINT  # RL-5
        if tool == "PowerShell":
            _ps_t = _powershell_deny(cmd)
            if _ps_t:
                return False, _deny_msg("PowerShell", _ps_t, command=cmd[:80])
        targets, fail_closed = _bash_targets(cmd, powershell=(tool == "PowerShell"))
        if fail_closed:
            return False, _fail_closed_message(cmd)
        # Shell assignments in the same command (`D=…; echo x >> $D/f`): the
        # shell expands them, so the hook must too (review R5-1).
        _assign = dict(re.findall(r"(?:^|[\s;&|(])([A-Za-z_][A-Za-z0-9_]*)=([^\s;&|]+)", cmd))
        for t in targets:
            # The shell removes quotes and backslashes before it opens a path:
            # `session""_ledger` and `sess\ion_ledger` name the protected
            # directory (review R6-2).
            t_norm = re.sub(r"[\"'\\]", "", t)
            for _name, _val in _assign.items():
                t_norm = re.sub(r"\$\{?" + _name + r"\}?", re.sub(r"[\"']", "", _val), t_norm)
            if is_protected_path(t) or is_protected_path(t_norm):
                return False, _deny_msg("Bash", t, command=cmd[:80])
            # A write target the hook cannot resolve (an unassigned variable)
            # that names a protected file fails closed. Only WRITE targets —
            # applying this to the whole command blocked `grep "$PAT" …` and
            # any command whose cwd path contains ".corvin" (review R6-5).
            # Only when the variable cannot be resolved even from the process
            # environment, and only for the ledger-class stores: `pip freeze >
            # "$PWD/requirements.txt"` resolves and was wrongly denied (R9-5).
            _expanded = os.path.expandvars(t_norm)
            if "$" in _expanded and any(h in _expanded for h in _LEDGER_HINTS):
                return False, _deny_msg("Bash", t, command=cmd[:80])
            t = t_norm
            # A glob in a target is expanded by the shell, not by this hook:
            # `cat x > .corvin-led?er/ledger.jsonl` named no protected path
            # literally (review R5-1). Check what it matches NOW, and fail
            # closed when it carries a protected hint but matches nothing yet.
            if any(c in t for c in "*?["):
                import glob as _glob  # noqa: PLC0415
                try:
                    matches = _glob.glob(str(_abs(t)), recursive=True)
                except Exception:  # noqa: BLE001
                    matches = []
                if any(is_protected_path(m) for m in matches) or (
                        not matches and _looks_protected(t)):
                    return False, _deny_msg("Bash", t, command=cmd[:80])

        # Gate 1: Python AST analysis — inspect any Python code that would
        # be executed by this Bash command before it runs.
        exempt, pin_deny = _pinned_tool_gate(cmd)
        if pin_deny:
            _emit_code_exec_audit(payload, outcome="blocked", blocked_reason=pin_deny)
            return False, f"path_gate: {pin_deny}"
        snippets = _extract_python_snippets(cmd, exempt)
        for code, label in snippets:
            safe, reason = _python_ast_check(code)
            if not safe:
                msg = (
                    f"path_gate: AST gate blocked Python execution: {reason}. "
                    f"Use forge_exec (sandboxed) instead of Bash for "
                    f"LLM-generated code." + _pin_hint(cmd)
                )
                _emit_code_exec_audit(payload, outcome="blocked",
                                      blocked_reason=reason)
                return False, msg
        if snippets:
            _emit_code_exec_audit(payload, outcome="allowed")
        if exempt:
            _emit_code_exec_audit(payload, outcome="allowed_pinned")

    elif tool == "WebFetch":
        url = inp.get("url", "")
        if isinstance(url, str) and url.startswith("file://"):
            p = url[len("file://"):]
            if is_protected_path(p):
                return False, _deny_msg("WebFetch", p)

    return True, ""


def _deny_msg(tool: str, target: str, *, command: str | None = None) -> str:
    base = (
        f"path_gate: {tool} write to protected forge/skill-forge path "
        f"is denied: {target}. These paths are managed exclusively by the "
        f"forge / skill-forge MCP servers — use mcp__forge__forge_tool, "
        f"mcp__skill_forge__skill_create, etc. instead of editing the "
        f"workspace files directly."
    )
    if command:
        base += f" (bash: {command!r})"
    return base


_SHARED_PATHS = None


def _tenant_audit_chain(tenant_id: str | None = None) -> Path:
    """THE tenant chain — ``bridges/shared/paths.py::tenant_audit_chain``.

    Loaded BY FILE PATH from this checkout (voice/hooks → corvin_operator →
    bridges/shared), the same rule ``data_classification._sibling_paths``
    follows: a bare ``import paths`` binds whatever ``paths`` is first on
    sys.path and a dotted name can land in another checkout. The hook used to
    compose ``<home>/global/forge/audit.jsonl`` (the legacy host-wide chain)
    and ``<home>/tenants/<tid>/global/audit.jsonl`` by hand — neither is the
    chain the boot tripwire, ``audit_query`` or any compliance report reads.
    """
    global _SHARED_PATHS
    if _SHARED_PATHS is None:
        import importlib.util as _ilu  # noqa: PLC0415

        spec = _ilu.spec_from_file_location(
            "_paths_path_gate",
            Path(__file__).resolve().parents[2] / "bridges" / "shared" / "paths.py",
        )
        mod = _ilu.module_from_spec(spec)  # type: ignore[arg-type]
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        _SHARED_PATHS = mod
    return Path(_SHARED_PATHS.tenant_audit_chain(tenant_id))


def _emit_audit(payload: dict, reason: str) -> None:
    """Best-effort — write a path_gate.denied event into the unified
    forge audit chain. Silent on failure: audit is observability, not a
    second deny gate. The deny itself happens via exit code 2 in main()."""
    try:
        # Make `forge` package importable when called as a hook subprocess.
        repo = _repo_root()
        if repo is not None:
            forge_pkg_parent = repo / "corvin_operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
        audit_path = _tenant_audit_chain()
    except Exception as _exc:  # noqa: BLE001 — deny still holds via exit 2; never silent
        sys.stderr.write(f"path_gate: audit unavailable, denial not recorded: {_exc}\n")
        return
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    target = (
        inp.get("file_path") or inp.get("path")
        or inp.get("notebook_path") or inp.get("url") or ""
    )
    cmd = inp.get("command", "") if tool in ("Bash", "PowerShell") else ""
    details = {
        "tool_name":  tool,
        "target":     target if isinstance(target, str) else "",
        "command":    cmd[:200] if isinstance(cmd, str) else "",
        "persona":    _resolve_aliased_env("CORVIN_CALLER_PERSONA") or "",
        "channel_id": _resolve_aliased_env("CORVIN_CHANNEL_ID") or "",
        "reason":     reason[:300],
    }
    try:
        write_event(audit_path, "path_gate.denied", tool=tool, details=details)
    except Exception as _exc:
        # Observability is best-effort; deny is enforced by exit code.
        # Log the failure so operators can diagnose misconfiguration.
        import sys as _sys
        _sys.stderr.write(f"path_gate: audit write failed: {_exc}\n")


def _emit_code_exec_audit(
    payload: dict,
    *,
    outcome: str,
    blocked_reason: str = "",
) -> None:
    """Best-effort audit for Gate 4: emit code.exec_attempt / code.exec_blocked.

    Metadata only — no code content ever enters the chain.
    """
    try:
        repo = _repo_root()
        if repo is not None:
            forge_pkg_parent = repo / "corvin_operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
        audit_path = _tenant_audit_chain()
    except Exception as _exc:  # noqa: BLE001
        sys.stderr.write(f"path_gate: code-exec audit unavailable: {_exc}\n")
        return
    event_type = "code.exec_blocked" if outcome == "blocked" else "code.exec_attempt"
    details: dict = {
        "language": "python",
        "outcome": outcome,
        "caller_persona": _resolve_aliased_env("CORVIN_CALLER_PERSONA") or "",
        "channel_id": _resolve_aliased_env("CORVIN_CHANNEL_ID") or "",
    }
    if blocked_reason:
        details["blocked_reason"] = blocked_reason[:200]
    try:
        write_event(audit_path, event_type, details=details)
    except Exception as _exc:  # noqa: BLE001
        sys.stderr.write(f"path_gate: code-exec audit write failed: {_exc}\n")


def _emit_tool_trace(payload: dict, decision: str) -> None:
    """ADR-0109 M6 — best-effort emit of forge.tool_executed into the WDAT
    audit chain when this hook is running inside an ACS worker subprocess.

    Only fires when CORVIN_ACS_WORKER_ID is set in the environment.
    Writes to the tenant chain (``tenant_audit_chain``), same as acs_runtime.
    Metadata only: tool_name, worker_id, run_id, decision. No input params.
    """
    worker_id = os.environ.get("CORVIN_ACS_WORKER_ID", "").strip()
    if not worker_id:
        return
    run_id = os.environ.get("CORVIN_ACS_RUN_ID", "").strip()
    tenant_id = os.environ.get("CORVIN_ACS_TENANT_ID", "").strip() or "_default"
    tool_name = payload.get("tool_name", "")
    try:
        repo = _repo_root()
        if repo is not None:
            forge_pkg_parent = repo / "corvin_operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    # THE tenant chain, via the same shared resolver ``acs_runtime._audit_path``
    # uses — so forge.tool_executed and the acs.* records share one file. The
    # old hand-composed ``tenants/<tid>/global/audit.jsonl`` was the parallel
    # chain acs_runtime already left (ADR-0650).
    try:
        wdat_audit = _tenant_audit_chain(tenant_id)
    except Exception as _exc:  # noqa: BLE001
        sys.stderr.write(f"path_gate: tool-trace audit unavailable: {_exc}\n")
        return
    try:
        wdat_audit.parent.mkdir(parents=True, exist_ok=True)
        write_event(
            wdat_audit,
            "forge.tool_executed",
            tool=tool_name,
            details={
                "tool_name": tool_name,
                "worker_id": worker_id,
                "run_id":    run_id,
                "decision":  decision,
            },
        )
    except Exception as _exc:  # noqa: BLE001
        sys.stderr.write(f"path_gate: tool-trace audit write failed: {_exc}\n")


def _emit_dialectic(payload: dict, reason: str) -> None:
    """Layer-11 dialectic decision-point. Emits a `decision.dialectical`
    event into the unified audit chain alongside the deny — same
    fail-closed verdict, but with thesis/antithesis recorded for
    observability of the false-positive risk.

    Best-effort: any failure is silent. The deny is enforced by exit 2.
    Heat-Score for path_gate: consequence high (workspace damage),
    uncertainty high (unparseable / ambig), scope session-bound.
    Threshold default 0.6 — lower than other sites because false-positive
    cost is high (legitimate Bash being blocked is annoying)."""
    try:
        # Make dialectic.py importable.
        here = Path(__file__).resolve().parent
        # voice/hooks → voice → operator → operator/bridges/shared
        bridges_shared = here.parent.parent / "bridges" / "shared"
        if str(bridges_shared) not in sys.path:
            sys.path.insert(0, str(bridges_shared))
        import dialectic as _dialectic  # type: ignore
        tool = payload.get("tool_name", "")
        inp = payload.get("tool_input") or {}
        target = (inp.get("file_path") or inp.get("path")
                  or inp.get("notebook_path") or "")
        cmd = inp.get("command", "") if tool in ("Bash", "PowerShell") else ""
        # The "deny" decision; antithesis records what we suspect could be
        # a false-positive so the audit can later inform threshold tuning.
        d = _dialectic.decide(
            site="path_gate",
            thesis="deny",
            antithesis={"reason": "false-positive-suspected",
                        "tool": tool, "target": target[:120],
                        "command": cmd[:200]},
            consequence=0.9,
            uncertainty=0.7 if "fail-closed" in reason else 0.4,
            scope=3,
            persona=_resolve_aliased_env("CORVIN_CALLER_PERSONA") or "",
            channel_id=_resolve_aliased_env("CORVIN_CHANNEL_ID") or "",
        )
        # The decision is informational here — fail-closed always wins.
        # The audit event was already written by decide() via _audit().
        _ = d
    except Exception:
        pass  # observability only — never fail the deny path


# Roadmap F13 — boot-time self-test for the path-gate detection logic.
#
# A regression in the matcher is silent: a vector that USED to be denied
# can quietly fall through after a refactor, and the only signal would be
# a successful direct-write hitting a forge / skill-forge workspace —
# i.e. the very thing the gate exists to prevent. The self-test is the
# canary: at adapter boot we feed a curated set of high-priority
# negative vectors through `check()` and emit a CRITICAL
# `path_gate.self_test_failed` audit event when one of them is allowed.
#
# Coverage targets the load-bearing classes:
#   - direct Write to forge / skill-forge / audit / policy / slot-mirror
#   - Bash redirect (>) into a protected path
#   - Bash tee into a protected path
#   - eval / command-substitution with a protected hint
#   - sed -i on a protected path
#   - heredoc with a protected hint
#   - python -c open(...,'w') on a protected path
#
# Non-goals:
#   - exhaustive — that's what test_path_gate.py is for. The self-test
#     is a fast smoke alarm, not a test suite.
#   - allow-list verification — the gate is fail-closed, false-positive
#     denies on benign commands are acceptable cost (test_path_gate.py
#     guards specific allow cases).


def _self_test_vectors() -> list[tuple[str, dict]]:
    """Curated must-deny payloads. The path strings reference the live
    corvin_home so the resolution path matches the runtime gate."""
    home = _corvin_home()
    forge_skill = home / "global" / "skill-forge" / "skills" / "x" / "SKILL.md"
    forge_tool = home / "global" / "forge" / "tools" / "x.py"
    audit_log = home / "global" / "forge" / "audit.jsonl"
    policy_path = home / "global" / "forge" / "policy.json"
    repo = _repo_root()
    if repo is not None:
        slot_path = repo / "corvin_operator" / "skill-forge" / "skills" / "dyn" / "x" / "SKILL.md"
        slot_str = str(slot_path)
    else:
        slot_str = str(forge_skill)  # fall back to scope path

    ledger = home / "tenants" / "_default" / "session_ledger" / "x" / "y" / "ledger.jsonl"
    return [
        # Review 2026-11-19 SEC-2/SEC-3 - the gate's own source, the anchor key
        # and an in-place writer the old extraction did not know.
        ("direct-write-gate-source",
         {"tool_name": "Edit",
          "tool_input": {"file_path": str(Path(__file__).resolve())}}),
        ("direct-write-anchor-key",
         {"tool_name": "Write",
          "tool_input": {"file_path": str(_anchor_key_path())}}),
        ("bash-sed-long-in-place-audit",
         {"tool_name": "Bash",
          "tool_input": {"command": f"sed --in-place 's/a/b/' {audit_log}"}}),
        ("bash-gzip-audit",
         {"tool_name": "Bash",
          "tool_input": {"command": f"gzip {audit_log}"}}),
        # ADR-2102 — a forged session-ledger line is re-supplied as "the user said".
        ("direct-write-session-ledger",
         {"tool_name": "Write",
          "tool_input": {"file_path": str(ledger)}}),
        ("bash-append-session-ledger",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo forged >> {ledger}"}}),
        ("direct-write-skill",
         {"tool_name": "Write",
          "tool_input": {"file_path": str(forge_skill)}}),
        ("direct-write-forge-tool",
         {"tool_name": "Edit",
          "tool_input": {"file_path": str(forge_tool)}}),
        ("direct-write-audit",
         {"tool_name": "Write",
          "tool_input": {"file_path": str(audit_log)}}),
        ("direct-write-policy",
         {"tool_name": "Edit",
          "tool_input": {"file_path": str(policy_path)}}),
        ("direct-write-slot-mirror",
         {"tool_name": "Write",
          "tool_input": {"file_path": slot_str}}),
        ("bash-redirect",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo poison > {forge_skill}"}}),
        ("bash-tee",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo x | tee -a {audit_log}"}}),
        # V-015: multi-destination tee — the protected path is the SECOND argument;
        # the old _TEE_RE single-capture regex only extracted the first and missed it.
        ("bash-tee-multi-dest",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo x | tee /tmp/safe {audit_log}"}}),
        ("bash-eval-hint",
         {"tool_name": "Bash",
          "tool_input": {"command": "eval \"$(cat /tmp/forge-payload.sh)\""}}),
        ("bash-cmd-subst-hint",
         {"tool_name": "Bash",
          "tool_input": {"command": "x=$(cat /etc/skill-forge/secret); echo $x"}}),
        ("bash-sed-inplace",
         {"tool_name": "Bash",
          "tool_input": {"command": f"sed -i 's/x/y/' {policy_path}"}}),
        ("bash-heredoc-hint",
         {"tool_name": "Bash",
          "tool_input": {"command": "cat <<EOF\nforge poison\nEOF"}}),
        ("bash-py-open",
         {"tool_name": "Bash",
          "tool_input": {"command":
            f"python3 -c \"open('{forge_skill}','w').write('x')\""}}),
        # ADR-0026 — Compute Fabric artefact tree sub-paths.
        ("adr0026-write-artifacts",
         {"tool_name": "Write",
          "tool_input": {
              "file_path": str(home / "tenants" / "_default" / "compute" /
                               "artifacts" / "run_abc" / "model.pkl")}}),
        ("adr0026-write-plugins",
         {"tool_name": "Write",
          "tool_input": {
              "file_path": str(home / "tenants" / "_default" / "compute" /
                               "plugins" / "acme" / "compute_plugin.yaml")}}),
        ("adr0026-write-datasources",
         {"tool_name": "Edit",
          "tool_input": {
              "file_path": str(home / "tenants" / "_default" / "compute" /
                               "datasources" / "crm_events.checkpoint.json")}}),
        ("adr0026-write-datasource-adapters",
         {"tool_name": "Write",
          "tool_input": {
              "file_path": str(home / "tenants" / "_default" / "compute" /
                               "datasource-adapters" / "acme" / "adapter.py")}}),
        # V-007: exec file descriptor redirect → fail-closed DENY
        ("bash-exec-fd",
         {"tool_name": "Bash",
          "tool_input": {"command": f"exec 3> {audit_log}; echo x >&3"}}),
        # V-007: mkfifo named pipe with protected hint → fail-closed DENY
        ("bash-mkfifo",
         {"tool_name": "Bash",
          "tool_input": {"command":
            f"mkfifo /tmp/p; echo x > /tmp/p & cat > /tmp/p > {forge_tool}"}}),
        # V-007: process substitution write side >(…) with protected hint → DENY
        ("bash-proc-subst",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo x | tee >(cat > {forge_tool})"}}),
        # V-013: command substitution in pipe position with protected hint → DENY
        ("bash-cmd-subst-pipe",
         {"tool_name": "Bash",
          "tool_input": {"command": f"$(echo 'writing to forge') | tee {forge_tool}"}}),
        # L16 compliance fix: numbered fd redirects (2>, 1>, 3> …) must be caught.
        # 'echo x 2>/forge/audit.jsonl' previously bypassed _REDIRECT_RE because
        # the leading digit was not in the pattern. Now _REDIRECT_RE has \d* to
        # match optional fd numbers before the redirect operator.
        ("bash-fd-redirect-stderr",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo x 2>{audit_log}"}}),
        # R6 #4: clobber-override operator `>|` — bash bypasses noclobber and
        # truncates the target. `echo x >| audit.jsonl` previously slipped past
        # _REDIRECT_RE because `>|` was not in the operator alternation.
        ("bash-clobber-override",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo poison >| {audit_log}"}}),
        # R6 #4: numbered-fd clobber-override `N>|`, e.g. `1>|`.
        ("bash-clobber-override-fd",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo poison 1>| {audit_log}"}}),
        # R6 #5: space-less redirect `word>protected` — bash allows
        # `echo x>file` with no whitespace before the operator. The old
        # lead-anchor `(?:^|[\s;&|])` required a separator, so this was missed.
        ("bash-spaceless-redirect",
         {"tool_name": "Bash",
          "tool_input": {"command": f"echo data>{audit_log}"}}),
        ("bash-spaceless-redirect-word",
         {"tool_name": "Bash",
          "tool_input": {"command": f"cmd>{policy_path}"}}),
        # Security-audit regressions (2026-06-25): write/delete/replace/symlink
        # primitives that previously extracted NO target and fell through to allow.
        ("bash-truncate-audit",
         {"tool_name": "Bash", "tool_input": {"command": f"truncate -s0 {audit_log}"}}),
        ("bash-rm-audit",
         {"tool_name": "Bash", "tool_input": {"command": f"rm -f {audit_log}"}}),
        ("bash-chmod-audit",
         {"tool_name": "Bash", "tool_input": {"command": f"chmod 666 {audit_log}"}}),
        ("bash-ln-forge-tool",
         {"tool_name": "Bash", "tool_input": {"command": f"ln -sf /tmp/evil.py {forge_tool}"}}),
        ("bash-cp-target-dir-forge",
         {"tool_name": "Bash",
          "tool_input": {"command": f"cp /tmp/evil.py --target-directory={forge_tool.parent}"}}),
        ("bash-cp-t-forge",
         {"tool_name": "Bash",
          "tool_input": {"command": f"cp -t {forge_tool.parent} /tmp/evil.py"}}),
        ("bash-ex-scripted-audit",
         {"tool_name": "Bash", "tool_input": {"command": f"printf '1d\\nwq\\n' | ex {audit_log}"}}),
    ] + _license_source_vectors(repo)


def _license_source_vectors(repo) -> list[tuple[str, dict]]:
    """ADR-0093 M1.3 — license plugin source tree protection vectors."""
    if repo is None:
        return []
    verifier_py = repo / "core" / "license" / "corvin_license" / "verifier.py"
    tier_flags_py = repo / "core" / "license" / "corvin_license" / "tier_flags.py"
    return [
        ("adr0093-write-license-verifier",
         {"tool_name": "Edit",
          "tool_input": {"file_path": str(verifier_py)}}),
        ("adr0093-write-license-tier-flags",
         {"tool_name": "Write",
          "tool_input": {"file_path": str(tier_flags_py)}}),
        ("adr0093-bash-patch-verifier",
         {"tool_name": "Bash",
          "tool_input": {"command":
            f"sed -i 's/_PUBKEY_SHA256/DISABLED/' {verifier_py}"}}),
    ]


def path_gate_self_test() -> tuple[bool, list[str]]:
    """Run the curated negative-vector set through ``check()``.

    Returns ``(ok, failures)``. ``ok=True`` when every vector was denied;
    ``failures`` lists the labels of vectors that were unexpectedly
    allowed.

    Emits a CRITICAL ``path_gate.self_test_failed`` audit event into the
    unified hash chain when failures are found. Out-of-band semantics
    are not needed here — the path-gate gap is structural, not
    chain-corrupting, so the event still goes into the chain so
    `voice-audit verify` covers it.

    Best-effort on the audit write: a missing forge package skips the
    audit but still returns the ``(ok, failures)`` tuple so the caller
    can react.
    """
    failures: list[str] = []
    for label, payload in _self_test_vectors():
        try:
            allow, _reason = check(payload)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{label} (raised {type(exc).__name__})")
            continue
        if allow:
            failures.append(label)

    if not failures:
        return True, []

    # Emit a CRITICAL audit event so an operator sees this on the next
    # `voice-audit verify` even if the bridge keeps running.
    try:
        repo = _repo_root()
        if repo is not None:
            forge_pkg_parent = repo / "corvin_operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
        audit_target = _tenant_audit_chain()
        audit_target.parent.mkdir(parents=True, exist_ok=True)
        write_event(
            audit_target, "path_gate.self_test_failed",
            severity="CRITICAL",
            tool="", run_id="",
            details={
                "failure_count": len(failures),
                "first_failures": failures[:20],
            },
        )
    except Exception:
        pass

    return False, failures


def _utf8_stdio() -> None:
    """Claude Code speaks UTF-8 on the hook's stdin/stdout/stderr. On Windows
    without UTF-8 mode those streams use the ANSI code page: a non-ASCII path
    in the payload is mis-decoded, and a deny reason containing one raises
    UnicodeEncodeError -> exit 1, a NON-blocking hook error (fail-open). No-op
    where the streams are already UTF-8 (Linux/macOS, PYTHONUTF8=1)."""
    for _s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            if _s is not None and (_s.encoding or "").replace("-", "").lower() != "utf8":
                _s.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main() -> int:
    _utf8_stdio()
    raw = sys.stdin.read()
    if not raw.strip():
        return 0
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        # Fail-closed: Claude Code is the only producer of hook payloads and
        # always emits well-formed JSON. Malformed input is either a bug or
        # an injection attempt — deny and audit rather than silently allow.
        _emit_audit({}, "malformed-hook-payload")
        return 2
    if not isinstance(payload, dict):
        _emit_audit({}, "malformed-hook-payload")
        return 2
    allow, reason = check(payload)
    # ADR-0109 M6: emit forge.tool_executed for both allowed and denied calls
    # when running inside an ACS worker subprocess (best-effort, no-op otherwise).
    _emit_tool_trace(payload, "allow" if allow else "deny")
    if allow:
        return 0
    _emit_audit(payload, reason)
    _emit_dialectic(payload, reason)
    # Best-effort debug log — the audit chain is the load-bearing record;
    # this just makes the deny appear in the rotating file log too, so
    # operators tailing logs/corvin.log don't have to also run
    # `voice-audit tail` to see why a Bash/Write was blocked.
    try:
        _bridge_shared = (
            Path(__file__).resolve().parents[2]
            / "voice" / "bridges" / "shared"
        )
        if _bridge_shared.is_dir() and str(_bridge_shared) not in sys.path:
            sys.path.insert(0, str(_bridge_shared))
        from debug_logging import get_logger as _corvin_get_logger  # type: ignore
        _pg_log = _corvin_get_logger("path_gate")
        tool = (payload.get("tool_name")
                or payload.get("tool", {}).get("name")
                or "?")
        _pg_log.warning("DENY tool=%s reason=%s", tool, reason)
    except Exception:
        pass
    print(reason, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
