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
  <repo>/operator/skill-forge/skills/dyn/**   engine-facing slot mirror
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


def _corvin_home() -> Path:
    """Mirror of forge.paths.corvin_home(), inlined to keep the hook
    self-contained and import-free at runtime."""
    env = _resolve_aliased_env("CORVIN_HOME")
    if env:
        return Path(os.path.expanduser(os.path.expandvars(env)))
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
            return parent / ".corvin"
    return Path.home() / ".corvin"


def _repo_root() -> Path | None:
    """Nearest ancestor with a plugins/ subdir, else None."""
    here = Path(__file__).resolve()
    for parent in [here, *here.parents]:
        if (parent / ".corvin_repo").exists() or (parent / "plugins").is_dir():
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


def _abs(p: str | Path) -> Path:
    """Best-effort absolute resolution. Non-existent paths are fine.

    Expands ~ and $VARS FIRST (R4): a Bash redirect like
    ``: > ~/.config/corvin-voice/secrets.json`` is non-absolute as a literal
    string, so it would otherwise resolve under cwd and dodge EVERY
    protected-path check — yet the shell expands ~ at run time and truncates the
    real file. expandvars covers ``$HOME``/``${XDG_CONFIG_HOME}`` forms; command
    substitution ($(...)/backticks) is already fail-closed in _bash_targets."""
    pp = Path(os.path.expanduser(os.path.expandvars(str(p))))
    if not pp.is_absolute():
        pp = Path.cwd() / pp
    try:
        return pp.resolve(strict=False)
    except (OSError, RuntimeError):
        return pp


def is_protected_path(path: str | Path) -> bool:
    """True if writing to *path* should be blocked."""
    if not path:
        return False
    abs_p = _abs(path)
    abs_str = str(abs_p)
    sep = os.sep

    home = _corvin_home()
    home_str = str(home)
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
        slot = repo / "operator" / "skill-forge" / "skills" / "dyn"
        slot_str = str(slot)
        if abs_str == slot_str or abs_str.startswith(slot_str + sep):
            return True
        bundled_policy = str(repo / "operator" / "forge" / "forge" / "policy.json")
        if abs_str == bundled_policy:
            return True
        bundled_default = str(repo / "operator" / "forge" / "forge" / "policy.default.json")
        if abs_str == bundled_default:
            return True
        # ADR-0143 Layer 44 — house-rules acceptable-use policy + its gate module.
        # An LLM-side write to operator/policy/house_rules.yaml (weaken the rules)
        # or to operator/bridges/shared/house_rules.py (which holds the
        # EXPECTED_POLICY_SHA256 integrity anchor + the gate logic) would defeat
        # the L44 acceptable-use guarantee without touching the audit chain.
        # Operator-side edits happen via git, not an in-process write tool.
        house_rules_policy = str(repo / "operator" / "policy" / "house_rules.yaml")
        house_rules_mod = str(repo / "operator" / "bridges" / "shared" / "house_rules.py")
        if abs_str in (house_rules_policy, house_rules_mod):
            return True
        # ADR-0093 M1.3 — license plugin source tree (verifier.py, tier_flags.py,
        # and any other module under core/license/corvin_license/). An LLM-side
        # patch to these files would bypass has_flag() / _verify_ed25519() locally
        # without touching the audit chain. Block the entire subdirectory.
        license_src = repo / "core" / "license" / "corvin_license"
        license_src_str = str(license_src)
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
        op_license = repo / "operator" / "license"
        op_license_str = str(op_license)
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
        # Supply-chain manifests live under core/ and operator/ (was plugins/ before ADR-0035)
        _in_plugin_tree = any(
            abs_str.startswith(str(repo / _d) + sep)
            for _d in ("core", "operator", "plugins")
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
    vault_str = str(vault)
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
    if abs_str.startswith(str(vault.parent) + sep) and \
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


def _extract_python_snippets(cmd: str) -> list[tuple[str, str]]:
    """Extract Python code strings from a Bash command for AST analysis.

    Returns list of (code, label) tuples. label is used only for the
    audit detail field — never emitted in plain text.
    """
    results: list[tuple[str, str]] = []

    for m in _PY_C_SINGLE_RE.finditer(cmd):
        results.append((m.group(1), "inline-c"))

    for m in _PY_C_DOUBLE_RE.finditer(cmd):
        results.append((m.group(1), "inline-c"))

    for m in _PY_FILE_RE.finditer(cmd):
        pyfile = m.group(1)
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
_PROTECTED_HINTS = ("forge", "skill-forge", "audit.jsonl", "policy.json",
                    ".corvin",
                    "secrets.json", "corvin-voice",
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
    except Exception:  # noqa: BLE001
        return False
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
            r = str(_resolver()).rstrip(sep)
            if r:
                roots.append(r)
        except Exception:  # noqa: BLE001
            pass
    return any(abs_str == r or abs_str.startswith(r + sep) for r in roots)


def _seg_chdirs_into_tree(seg: str) -> bool:
    """True when a segment changes the working directory INTO (at/under) the corvin
    tree via `cd`, `pushd`, or a wrapper chdir flag (`env -C/--chdir`,
    `sudo -D/--chdir`, `doas -C`) — after which a relative destructive target
    (`rm -rf tenants`) lands inside the protected tree at run time even though the
    hook resolved it against its own cwd (round-4/round-5)."""
    try:
        raw = shlex.split(seg, posix=True)
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
            r = str(_resolver()).rstrip(sep)
            if r:
                roots.append(r)
        except Exception:  # noqa: BLE001
            pass
    # target == root, target under root, or target an ancestor of root
    return any(abs_str == r
               or abs_str.startswith(r + sep)
               or r.startswith(abs_str + sep)
               for r in roots)


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


def _target_dir_flag(arg_toks: list[str]) -> str | None:
    """Resolve a -t <dir> / -t<dir> / --target-directory=<dir> destination, if any.

    GNU mv/cp/install/rsync all accept this; previously only `install` honoured
    it, so `cp -t <protected_dir> src` (and `--target-directory=`) bypassed the
    gate because _last_nonflag returned the SOURCE, not the protected dest."""
    for i, tok in enumerate(arg_toks):
        if tok in ("-t", "--target-directory") and i + 1 < len(arg_toks):
            return arg_toks[i + 1]
        if tok.startswith("--target-directory="):
            return tok.split("=", 1)[1]
        if tok.startswith("-t") and len(tok) > 2 and not tok.startswith("--"):
            return tok[2:]  # glued GNU short form -t<dir>
    return None


def _bash_targets(cmd: str) -> tuple[list[str], bool]:
    """Return (target_paths, fail_closed).

    fail_closed=True means we could not parse safely AND the command looks
    like it might touch a protected path. The caller treats this as a deny.
    """
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

    # cd-into-tree backstop (round-4): the hook runs in its own subprocess and
    # cannot observe a `cd`, so `cd ~/.corvin && rm -rf tenants` resolved the
    # relative `tenants` against the hook's cwd and passed. If ANY segment cd's
    # into the corvin tree AND ANY segment runs a destructive command, fail
    # closed — the relative target would land inside the protected tree at run
    # time. (cd into an unrelated dir, or cd-into-tree + a read like cat/ls, is
    # not blocked.)
    _cd_into_tree = any(_seg_chdirs_into_tree(_seg) for _seg in segs)
    if _cd_into_tree:
        _DESTRUCTIVE_ALL = (_TARGET_ALL_CMDS + _DEST_LAST_CMDS + _FIND_CMDS
                            + _ARCHIVE_CMDS + ("dd", "tee"))
        for _seg in segs:
            try:
                _st = _strip_cmd_wrappers(shlex.split(_seg, posix=True))
            except ValueError:
                return [], True
            if _st and _st[0].rsplit("/", 1)[-1] in _DESTRUCTIVE_ALL:
                return [], True
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
            toks = shlex.split(seg, posix=True)
        except ValueError:
            if _looks_protected(seg):
                return [], True
            continue
        if not toks:
            continue
        # Peel leading exec wrappers (`env`/`sudo`/`nice`/`timeout`/`busybox`/…)
        # so cmd_name is the REAL command, not the wrapper (round-4 CRITICAL).
        toks = _strip_cmd_wrappers(toks)
        if not toks:
            continue
        cmd_name = toks[0].rsplit("/", 1)[-1]
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
            # (round-3).
            for a in _all_nonflag(arg_toks):
                if _touches_corvin_tree(a):
                    return [], True
            dest = _target_dir_flag(arg_toks) or _last_nonflag(arg_toks)
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

    return targets, False


# ----- main check ---------------------------------------------------------

_OTA_GATED_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit", "Bash", "WebFetch")


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


def check(payload: dict) -> tuple[bool, str]:
    """Return (allow, reason). allow=False means deny."""
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

    elif tool == "Bash":
        cmd = inp.get("command", "")
        if not isinstance(cmd, str) or not cmd:
            return True, ""
        targets, fail_closed = _bash_targets(cmd)
        if fail_closed:
            return False, (
                "path_gate: Bash command unparseable AND references a "
                "protected path; fail-closed. Use the forge/skill-forge "
                "MCP tools instead. Command (first 80 chars): "
                + cmd[:80]
            )
        for t in targets:
            if is_protected_path(t):
                return False, _deny_msg("Bash", t, command=cmd[:80])

        # Gate 1: Python AST analysis — inspect any Python code that would
        # be executed by this Bash command before it runs.
        snippets = _extract_python_snippets(cmd)
        for code, label in snippets:
            safe, reason = _python_ast_check(code)
            if not safe:
                msg = (
                    f"path_gate: AST gate blocked Python execution: {reason}. "
                    f"Use forge_exec (sandboxed) instead of Bash for "
                    f"LLM-generated code."
                )
                _emit_code_exec_audit(payload, outcome="blocked",
                                      blocked_reason=reason)
                return False, msg
        if snippets:
            _emit_code_exec_audit(payload, outcome="allowed")

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


def _emit_audit(payload: dict, reason: str) -> None:
    """Best-effort — write a path_gate.denied event into the unified
    forge audit chain. Silent on failure: audit is observability, not a
    second deny gate. The deny itself happens via exit code 2 in main()."""
    try:
        # Make `forge` package importable when called as a hook subprocess.
        repo = _repo_root()
        if repo is not None:
            forge_pkg_parent = repo / "operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    audit_path = _corvin_home() / "global" / "forge" / "audit.jsonl"
    tool = payload.get("tool_name", "")
    inp = payload.get("tool_input") or {}
    target = (
        inp.get("file_path") or inp.get("path")
        or inp.get("notebook_path") or inp.get("url") or ""
    )
    cmd = inp.get("command", "") if tool == "Bash" else ""
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
            forge_pkg_parent = repo / "operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    audit_path = _corvin_home() / "global" / "forge" / "audit.jsonl"
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
    except Exception:
        pass


def _emit_tool_trace(payload: dict, decision: str) -> None:
    """ADR-0109 M6 — best-effort emit of forge.tool_executed into the WDAT
    audit chain when this hook is running inside an ACS worker subprocess.

    Only fires when CORVIN_ACS_WORKER_ID is set in the environment.
    Writes to tenants/<tid>/global/audit.jsonl (same chain wdat_report reads).
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
            forge_pkg_parent = repo / "operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
    except Exception:
        return
    # Use simple env-var resolution (no ancestor walk) so that the audit path
    # matches acs_runtime._write_audit() — both must write to the same chain.
    # _corvin_home() walks ancestors and returns repo/.corvin on dev machines,
    # while acs_runtime uses CORVIN_HOME → ~/.corvin (no walk). Using the walk
    # here would route forge.tool_executed to a different file than acs.* events.
    _ch_env = os.environ.get("CORVIN_HOME")
    _base = (
        Path(os.path.expanduser(os.path.expandvars(_ch_env)))
        if _ch_env
        else Path.home() / ".corvin"
    )
    wdat_audit = _base / "tenants" / tenant_id / "global" / "audit.jsonl"
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
    except Exception:
        pass


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
        cmd = inp.get("command", "") if tool == "Bash" else ""
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
        slot_path = repo / "operator" / "skill-forge" / "skills" / "dyn" / "x" / "SKILL.md"
        slot_str = str(slot_path)
    else:
        slot_str = str(forge_skill)  # fall back to scope path

    return [
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
            forge_pkg_parent = repo / "operator" / "forge"
            if str(forge_pkg_parent) not in sys.path:
                sys.path.insert(0, str(forge_pkg_parent))
        from forge.security_events import write_event  # type: ignore
        audit_target = _corvin_home() / "global" / "forge" / "audit.jsonl"
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


def main() -> int:
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
