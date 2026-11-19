#!/usr/bin/env python3
"""Audit event registry completeness check (CI gate + local tool).

Measures the REAL registries: it imports ``forge.security_events`` (stdlib
only) and reads ``EVENT_SEVERITY`` and ``_EVENT_ALLOWLIST`` as the running
writer sees them. The previous version regex-parsed the source and mis-counted
any entry that was not a one-line ``"x": frozenset(...)`` literal (dict
comprehensions, merged extension dicts), so its numbers described the file's
formatting, not the writer.

Reported:
  * total EVENT_SEVERITY entries, total _EVENT_ALLOWLIST entries;
  * gap = events with a severity but no positive allowlist, split into
      - EMITTED: a production call site passes the event name as an argument
        (AST scan of the repo, tests excluded) — these FAIL the gate, because
        their details fall to the vocabulary floor with no per-event contract;
      - FLOOR BY DESIGN: emitted, but deliberately left on the PII-scanned
        vocabulary floor (listed below, each with its reason);
      - NO EMITTER: registered, but nothing in the repo emits it — reported,
        never "fixed" by inventing fields;
  * allowlisted events with no severity (FAIL: the writer silently defaults
    them to INFO);
  * event names emitted with NO severity entry - a literal, a module constant, a
    conditional, a local variable, a dict lookup, an f-string of constants, an event
    passed third or by keyword, a writer run through ``asyncio.to_thread`` (round 3:
    only a bare literal in the first two positions used to be read) - at a fixed
    writer, at a helper that forwards a PARAMETER into a writer's event position, or
    at a generically named helper: a shrink-only ratchet against
    ``scripts/audit_unregistered_baseline.json`` (a new name FAILS; ``--init-baseline`` creates the file
    once, ``--shrink-baseline`` only removes names that HAVE a severity entry now; a name the
    scan merely stopped seeing is reported separately and stays).

``--runtime`` additionally imports every module that calls
``register_event_allowlist`` / ``audit_sink.register_events`` (best effort)
and reports what those import-time registrations add on top of the static
registry (a registration made lazily inside a function is not seen here) —
a runtime field the static entry does not list is reported as a
disagreement (not a failure: fixing it means editing the emitting module).

Exit codes: 0 = pass, 1 = gate failure, 3 = registry not importable.
"""
from __future__ import annotations

import argparse
import ast
import importlib
import json
import os
import re
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGE_DIR = REPO_ROOT / "corvin_operator" / "forge"
SECURITY_EVENTS = FORGE_DIR / "forge" / "security_events.py"
BASELINE = REPO_ROOT / "scripts" / "audit_unregistered_baseline.json"

#: Emitted events that stay on the vocabulary floor ON PURPOSE. A positive
#: allowlist switches off the floor's PII value scan for non-free-text keys, so
#: an event whose emitter passes a raw identifier / path / only free text is
#: safer on the floor than on an allowlist. Changing one of these means changing
#: its emitter first (to a code, a count or a fingerprint), then registering it.
FLOOR_BY_DESIGN: dict[str, str] = {
    "consent.toctou_drop": "emitter passes a raw `uid`",
    "engine.pref_switched": "emitter passes a raw `uid`",
    "quota.lock_timeout": "emitter passes a raw `uid`",
    "path_gate.denied": "emitter passes the denied `target` path",
    "tool.user_saved": "emitter passes the operator-chosen tool `name`",
    "tool.user_removed": "emitter passes the operator-chosen tool `name`",
    "forge.bwrap_unavailable": "emitter passes only free-text `error`",
    "secret.vault_malformed": "emitter passes only free-text `error`",
    "tool.created": "forge registry keys (sha, persona, caller_persona) are all on the floor already; "
                    "the event is the generic chain probe of ~16 test files",
    "tool.deleted": "same emitter and reason as tool.created",
    "tool.promoted": "same emitter and reason as tool.created",
    "worker.event_relayed": "a worker relays arbitrary registered events; no fixed field set",
    "license.features_url_override": "emitter passes the operator-supplied `url` (may carry userinfo)",
}

_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build",
              ".corvin", "tests", "test"}
_REGISTRY_NAMES = {"EVENT_SEVERITY", "_EVENT_ALLOWLIST", "_EVENT_ALLOWLIST_EXTENSIONS",
                   "_REVIEW_20261119_EVENTS"}


def load_registry():
    if str(FORGE_DIR) not in sys.path:
        sys.path.insert(0, str(FORGE_DIR))
    # ``corvin_operator`` on sys.path makes ``forge`` resolve to the outer
    # namespace directory first; drop that stale entry so the real package loads.
    cached = sys.modules.get("forge")
    if cached is not None and getattr(cached, "__file__", None) is None:
        del sys.modules["forge"]
    from forge import security_events  # type: ignore[import-not-found]
    return security_events


def _py_sources():
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if fn.endswith(".py") and not fn.startswith("test_") and not fn.endswith("_test.py"):
                yield Path(dirpath) / fn


def _registry_nodes(tree: ast.AST) -> set[int]:
    """ids of every node inside a registry literal in security_events.py."""
    inside: set[int] = set()
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        if any(isinstance(t, ast.Name) and t.id in _REGISTRY_NAMES for t in targets):
            inside.update(id(n) for n in ast.walk(node))
    return inside


#: A call whose callee name matches this is treated as a WRITE of the event
#: named in its arguments; any other call that mentions an event name is a
#: reader (a filter, a counter, a self-test label).
_EMIT_CALL_RE = re.compile(r"(emit|write|audit|event|record|l16|trace|signal|notify|sink|log)")


def _call_name(node: ast.Call) -> str:
    fn = node.func
    if isinstance(fn, ast.Attribute):
        return fn.attr
    if isinstance(fn, ast.Name):
        return fn.id
    return ""


def _direct_values(expr: ast.AST):
    """The expressions a call argument can evaluate to AS the event name.

    A name inside a set/tuple/dict argument (``frozenset({...})`` of event
    types a reader filters on, a severity map) is not an emission; a bare
    constant, a module constant or either branch of a conditional is.
    """
    if isinstance(expr, ast.IfExp):
        yield from _direct_values(expr.body)
        yield from _direct_values(expr.orelse)
    elif isinstance(expr, ast.BoolOp):
        for v in expr.values:
            yield from _direct_values(v)
    else:
        yield expr


def find_emitters(names: set[str]) -> dict[str, list[str]]:
    """event name → production files that pass it as a call argument."""
    if not names:
        return {}
    pat = re.compile("|".join(re.escape(n) for n in sorted(names, key=len, reverse=True)))
    found: dict[str, list[str]] = {}
    for path in _py_sources():
        try:
            src = path.read_text("utf-8", errors="ignore")
        except OSError:
            continue
        if not pat.search(src):
            continue
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        skip = _registry_nodes(tree) if path == SECURITY_EVENTS else set()
        consts = {  # NAME = "event" module constants
            t.id: n.value.value
            for n in ast.walk(tree) if isinstance(n, ast.Assign)
            and isinstance(n.value, ast.Constant) and n.value.value in names
            for t in n.targets if isinstance(t, ast.Name)
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or id(node) in skip:
                continue
            if not _EMIT_CALL_RE.search(_call_name(node).lower()):
                continue  # a reader (``counts.prefix(...)``, ``CheckResult(...)``)
            for arg in list(node.args) + [kw.value for kw in node.keywords]:
                for sub in _direct_values(arg):
                    val = None
                    if isinstance(sub, ast.Constant) and sub.value in names:
                        val = sub.value
                    elif isinstance(sub, ast.Name) and sub.id in consts:
                        val = consts[sub.id]
                    if val is not None:
                        rel = str(path.relative_to(REPO_ROOT))
                        if rel not in found.setdefault(val, []):
                            found[val].append(rel)
    return found


#: Writer callees whose first/second positional argument (or ``event_type=`` /
#: ``event=`` keyword) is an event name. Review 2026-11-05 (SEC-9): the bridge
#: adapter writes through ``_audit_event`` / ``_audit_write``, the plugin layer
#: through ``audit_emit``, others through ``_audit_log`` / ``_emit_audit`` /
#: ``_write_audit`` / ``emit`` / ``audit`` — none of which this scan saw, so
#: security events such as ``engine_trust.gate_fail_open`` were written at the
#: default severity with floored details and never reached the ratchet.
_WRITER_CALLEES = {"write_event", "_write_event", "_log_security_event", "log_security_event",
                   "audit_event", "_audit", "_emit", "emit_event",
                   "_audit_event", "audit_emit", "_audit_log", "_emit_audit",
                   "_audit_write", "_write_audit", "emit", "audit"}
#: Injected writer CALLABLES (a parameter or attribute holding the chain writer, never a
#: ``def``), so :func:`derive_writer_callees` cannot see them by definition (refutation
#: 2026-11-05, RS-10: ``awp_walker``'s ``audit_writer``, ``rollback_manager``'s
#: ``audit_callback``, ``browser.compliance``'s ``audit_fn``).
_INJECTED_WRITERS = {"audit_writer", "_audit_writer", "audit_callback", "audit_fn", "_audit_fn",
                     # Round 3 (R3-SEC-3): the console's per-turn chain writer is a closure
                     # handed around as ``os_audit`` - never a ``def`` with that name.
                     "os_audit"}
_EVENT_KEYWORDS = {"event_type", "event", "event_name"}
#: ``name=`` carries the event only at a FIXED writer; on a derived helper it is
#: any object's name.
_FIXED_EVENT_KEYWORDS = _EVENT_KEYWORDS | {"name"}
_FACTORY_RE = re.compile(r"audit|emit", re.IGNORECASE)
#: A dotted literal ending like this is a FILE name a helper was handed, not an event.
_FILE_SUFFIXES = (".jsonl", ".json", ".ndjson", ".py", ".md", ".yaml", ".yml", ".toml", ".txt",
                  ".sig", ".sha256",
                  # Review 2026-11-19 (GOV-1): media / document / data / config file names
                  # (``title.png``, ``audio.aac``, ``final.mp4``, ``slides.pptx``, ``verify.cfg``).
                  ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico",
                  ".aac", ".mp3", ".mp4", ".m4a", ".wav", ".ogg", ".opus", ".flac", ".webm",
                  ".mkv", ".mov", ".avi", ".srt", ".vtt",
                  ".pptx", ".docx", ".xlsx", ".pdf", ".html", ".htm", ".css", ".js", ".ts",
                  ".tsx", ".csv", ".tsv", ".parquet", ".db", ".sqlite", ".sqlite3",
                  ".log", ".cfg", ".ini", ".conf", ".env", ".lock", ".pid", ".sock", ".tmp",
                  ".bak", ".key", ".pem", ".crt", ".pub", ".zip", ".tar", ".gz", ".whl",
                  ".sh", ".ps1", ".bat", ".service", ".timer", ".xml", ".tex", ".bib")
_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")

#: Review 2026-11-19 (GOV-1). :func:`derive_writer_callees` promotes ANY function that
#: hands a parameter to a writer - by NAME. One helper called ``get`` / ``join`` /
#: ``add`` / ``subscribe`` therefore turned every ``sys.modules.get("core.x.y")``,
#: ``os.path.join(d, "title.png")``, ``report.add(ERROR, "class.id_mismatch")`` and
#: ``hub.subscribe("health.stall_detected")`` in the repo into an "emitted audit event":
#: 60+ false FAILs on a gate that was red for that reason alone. A DERIVED callee is
#: refused only when its name is a container / stdlib / bus method or reads like a
#: reader or subscriber. Every other helper is still derived, and the fixed
#: :data:`_WRITER_CALLEES` and the injected writers are unaffected, so a real new name
#: passed to a real writer still fails (positive control:
#: ``tests/security/test_audit_completeness_scanner.py``).
_READER_CALLEE_RE = re.compile(
    r"^_*(read|get|load|query|find|filter|count|list|iter|subscribe|unsubscribe|on|is|has)(_|$)")
_GENERIC_CALLEES = frozenset({
    "get", "join", "add", "append", "extend", "insert", "update", "setdefault", "pop",
    "remove", "open", "read", "write", "subscribe", "unsubscribe", "publish", "execute",
    "invoke", "run", "call", "send", "put", "post", "format", "print", "log",
})


def _derived_callee_ok(name: str) -> bool:
    """A DENY list on purpose: a wrapper with an arbitrary name (``_inner``, ``outer``,
    ``_notice``) that forwards its parameter to a writer is still derived (RS-10)."""
    low = name.lower()
    return low not in _GENERIC_CALLEES and not _READER_CALLEE_RE.match(low)


#: Module search roots: a dotted literal that resolves to a module or package under one
#: of these is an import path (``core.skills.os_skills.model_selector``,
#: ``corvin_console.routes.skill_creator_api``, ``corvin_plugins.health``), not an event.
_MODULE_ROOTS = ("", "core/console", "core/plugins", "core/compute", "core/gateway",
                 "core/orchestration", "core/workflows", "corvin_operator",
                 "corvin_operator/forge", "corvin_operator/bridges/shared", "ops/launcher")
_STDLIB_TOP = frozenset(getattr(sys, "stdlib_module_names", ())) | {"os", "sys"}
_VERSION_RE = re.compile(r"^v\d+(\.\d+)+$")
#: cgroup controller interface files (``cpu.max``, ``memory.limit_in_bytes``).
_CGROUP_TOP = frozenset({"cpu", "cpuacct", "cpuset", "memory", "pids", "io", "blkio", "cgroup"})
_CGROUP_LEAF_RE = re.compile(r"^(max|min|high|low|stat|current|events|weight|pressure|peak"
                             r"|cfs_[a-z_]+|limit_in_bytes|usage_in_bytes|swap\.[a-z]+"
                             r"|procs|controllers|subtree_control|effective_cpus|cpus|mems)$")


def _is_module_path(v: str) -> bool:
    rel = v.replace(".", "/")
    for root in _MODULE_ROOTS:
        base = REPO_ROOT / root / rel
        if base.with_suffix(".py").is_file() or (base / "__init__.py").is_file():
            return True
    return False


#: Stdlib modules never imported just to resolve an attribute path (import side effects).
_STDLIB_NO_IMPORT = frozenset({"antigravity", "this", "idlelib", "tkinter", "turtle", "turtledemo",
                               "__main__", "__phello__"})
_STDLIB_ATTR_CACHE: dict[str, bool] = {}


def _is_stdlib_attr_path(v: str) -> bool:
    """True only for a dotted literal that RESOLVES as an attribute path of a stdlib
    module (``os.path.join``, ``sys.modules.get``). Review 2026-11-19 round 2 (R2-SEC-1):
    the filter used to drop every name whose first segment merely equals a stdlib module
    name, which swallowed whole event namespaces - ``secrets.*``, ``token.*``,
    ``platform.*``, ``queue.*``, ``code.*``, ``http.*``, ``operator.*``, ``signal.*``."""
    cached = _STDLIB_ATTR_CACHE.get(v)
    if cached is not None:
        return cached
    top, _, rest = v.partition(".")
    ok = False
    if top in _STDLIB_TOP and top not in _STDLIB_NO_IMPORT:
        try:
            obj = importlib.import_module(top)
            for part in rest.split("."):
                obj = getattr(obj, part)
            ok = True
        except Exception:  # noqa: BLE001 - not an attribute path: it can be an event name
            ok = False
    _STDLIB_ATTR_CACHE[v] = ok
    return ok


def looks_like_event_name(v: object, *, fixed_writer: bool = False,
                          namespaces: "frozenset[str] | set[str]" = frozenset()) -> bool:
    """A dotted literal that can be an audit event name.

    Two strengths (R2-SEC-1). For a FIXED writer (:data:`_WRITER_CALLEES`, the injected
    writers) the literal IS what goes into the chain, so only two shapes are refused: a
    version (``v1.0``) and a path that really resolves as a stdlib attribute
    (``os.path.join``). The file-suffix, module-path and cgroup filters used to apply
    there too and hid real events named ``license.key``, ``session.lock``,
    ``erasure.log``, ``a2a.pub``, ``forge.policy`` and ``memory.high``.

    For a DERIVED callee (a helper promoted by name, which may be a same-named function
    of another subsystem) those three noise filters stay - unless the name sits in a
    namespace the registry already knows. Deliberately a DENY list either
    way: a name with an unknown first segment still counts, so a new subsystem cannot
    slip past the ratchet by being new."""
    if not isinstance(v, str) or not _EVENT_NAME_RE.match(v):
        return False
    if _VERSION_RE.match(v) or _is_stdlib_attr_path(v):
        return False
    if fixed_writer:
        return True
    # Round 3 (R3-SEC-3): the noise filters below hid 14 of 20 planted names at derived
    # helpers - ``license.key``, ``session.lock``, ``memory.high`` are file / cgroup
    # shapes AND event names. A name whose first segment is a namespace the registry
    # already knows (``namespaces`` = first segments of EVENT_SEVERITY) is an event.
    if v.partition(".")[0] in namespaces:
        return True
    if v.lower().endswith(_FILE_SUFFIXES):
        return False
    top, _, rest = v.partition(".")
    if top in _CGROUP_TOP and _CGROUP_LEAF_RE.match(rest):
        return False
    return not _is_module_path(v)


def _passes_param_as_event(call: ast.Call, params: set[str]) -> bool:
    cands = list(call.args[:2]) + [kw.value for kw in call.keywords if kw.arg in _EVENT_KEYWORDS]
    return any(isinstance(a, ast.Name) and a.id in params for a in cands)


_TREE_INDEX_CACHE: "dict[int, tuple]" = {}

#: Callables that run ANOTHER callable with the arguments that follow it. The console
#: must hand a blocking chain write to a thread (``await asyncio.to_thread(
#: security_events.write_event, path, "x.y", details=...)``), which moved the writer's
#: name out of the call position and its event out of the scan (round 3).
_TRAMPOLINES = {"to_thread": 0, "run_in_executor": 1, "partial": 0, "submit": 0,
                "run_sync": 0, "call_soon_threadsafe": 0, "start_soon": 0}


def _trampolined_call(node: ast.Call) -> "ast.Call | None":
    """``to_thread(writer, a, b, k=v)`` as the direct call ``writer(a, b, k=v)``; the
    synthetic node remembers the real one in ``_origin`` (scope lookup, line number)."""
    at = _TRAMPOLINES.get(_call_name(node))
    if at is None or len(node.args) <= at:
        return None
    target = node.args[at]
    if not isinstance(target, (ast.Name, ast.Attribute)):
        return None
    direct = ast.Call(func=target, args=list(node.args[at + 1:]), keywords=list(node.keywords))
    ast.copy_location(direct, node)
    direct._origin = node  # type: ignore[attr-defined]
    return direct


def _tree_index(tree: ast.AST) -> tuple:
    """``(functions, classes, calls)`` of ``tree`` from ONE pass, memoised.

    ``functions``: every function with ALL calls in its body (nested functions
    included, as ``ast.walk(fn)`` would give). Walking each function separately
    re-visited every nested body once per enclosing level and was most of the scan's
    runtime."""
    hit = _TREE_INDEX_CACHE.get(id(tree))
    if hit is not None and hit[0] is tree:
        return hit[1]
    fns: list[tuple[ast.AST, list[ast.Call]]] = []
    classes: list[ast.ClassDef] = []
    calls: list[ast.Call] = []
    # Iterative: (node, enclosing call lists).
    stack: list[tuple[ast.AST, tuple]] = [(tree, ())]
    while stack:
        node, open_lists = stack.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            mine: list[ast.Call] = []
            fns.append((node, mine))
            open_lists = open_lists + (mine,)
        elif isinstance(node, ast.ClassDef):
            classes.append(node)
        elif isinstance(node, ast.Call):
            calls.append(node)
            for lst in open_lists:
                lst.append(node)
            direct = _trampolined_call(node)
            if direct is not None:
                calls.append(direct)
                for lst in open_lists:
                    lst.append(direct)
        for child in ast.iter_child_nodes(node):
            stack.append((child, open_lists))
    out = (fns, classes, calls)
    _TREE_INDEX_CACHE[id(tree)] = (tree, out)
    return out


def _fn_index(tree: ast.AST) -> "list[tuple[ast.AST, list[ast.Call]]]":
    return _tree_index(tree)[0]


def _ordered_params(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> list[str]:
    a = fn.args
    out = [x.arg for x in a.posonlyargs + a.args]
    if out and out[0] in ("self", "cls"):
        out = out[1:]
    return out


def _kwonly_params(fn: "ast.FunctionDef | ast.AsyncFunctionDef") -> list[str]:
    return [x.arg for x in fn.args.kwonlyargs]


def _is_writer_call(c: ast.Call, callees: "set[str] | dict") -> bool:
    if _call_name(c) in callees:
        return True
    # A writer factory called inline: ``_default_audit_emit(tid)(event_type, details)``.
    inner = c.func
    return isinstance(inner, ast.Call) and bool(_FACTORY_RE.search(_call_name(inner)))


def _event_args(call: ast.Call, event_params: "dict[str, tuple[set[int], set[str]]]"
                ) -> list[ast.AST]:
    """The argument expressions of ``call`` that sit in an EVENT position of its callee.

    A fixed writer, an injected writer and an inline factory take the event as one of
    their first two positional arguments or as ``event_type=`` / ``event=``. A DERIVED
    helper takes it exactly where :func:`derive_event_params` saw it forward a parameter
    to a writer - by position or by that parameter's name."""
    entry = event_params.get(_call_name(call))
    if entry is None:
        return list(call.args[:2]) + [kw.value for kw in call.keywords
                                      if kw.arg in _EVENT_KEYWORDS]
    idx, names = entry
    return ([call.args[i] for i in sorted(idx) if i < len(call.args)
             and not isinstance(call.args[i], ast.Starred)]
            + [kw.value for kw in call.keywords if kw.arg in names])


def derive_event_params(trees: "list[ast.AST]", *, generic: bool = False,
                        base: "dict[str, tuple[set[int], set[str]]] | None" = None,
                        fixed: "set[str] | None" = None
                        ) -> "dict[str, tuple[set[int], set[str]]]":
    """helper name -> (positional indices, parameter names) of the parameters it forwards
    to a writer AS THE EVENT NAME, to a fixpoint.

    Round 3 (R3-SEC-3). The derivation used to be by NAME only: any function that handed
    any parameter to a writer made every same-named call in the repo a "writer", with its
    first two arguments read as event names. Once constants were resolved that turned
    ``gate.validate(_CLOUD_HOST)`` into the event ``api.anthropic.com`` and
    ``require_capability(CAPABILITY)`` into ``a2a.network``. Tracking WHICH parameter
    flows into the event position keeps the real wrappers (also an event passed third:
    ``_audit(audit_fn, tenant_id, "x.y")``) and drops the look-alikes.

    ``generic=False``: helpers :func:`_derived_callee_ok` admits. ``generic=True``: the
    generically named ones (``log``, ``send``, ``on_*``), computed on top of ``base`` and
    never fed back into the fixpoint."""
    fixed = set(_WRITER_CALLEES) | _INJECTED_WRITERS if fixed is None else fixed
    known: dict[str, tuple[set[int], set[str]]] = {} if base is None else dict(base)
    out: dict[str, tuple[set[int], set[str]]] = {} if generic else known
    defs = []
    for tree in trees:
        for fn, calls in _fn_index(tree):
            if fn.name in fixed:
                continue
            if _derived_callee_ok(fn.name) == generic:
                continue
            if generic and fn.name in known:
                continue
            ordered = _ordered_params(fn)
            every = set(ordered) | set(_kwonly_params(fn))
            if not every:
                continue
            defs.append((fn.name, ordered, every, calls))

    changed = True
    while changed:
        changed = False
        writers = fixed | set(known)
        for name, ordered, every, calls in defs:
            for c in calls:
                if not _is_writer_call(c, writers):
                    continue
                for arg in _event_args(c, known):
                    if isinstance(arg, ast.Name) and arg.id in every:
                        idx, names = out.setdefault(name, (set(), set()))
                        before = (len(idx), len(names))
                        names.add(arg.id)
                        if arg.id in ordered:
                            idx.add(ordered.index(arg.id))
                        if (len(idx), len(names)) != before:
                            changed = not generic or changed
    return out


def derive_writer_callees(trees: "list[ast.AST]") -> set[str]:
    """Every function that hands one of its parameters to a writer callee as the event
    name, to a fixpoint (``_hr_classifier_audit(event_type, …)`` → ``_audit_write`` →
    ``write_event``). A fixed name list missed 40+ such helpers - L44's
    ``house_rules.denied``, ``social.signature_invalid``, ``plugin.load_refused``,
    ``license.key_integrity_mismatch`` were written at INFO with floored details and the
    gate passed (RS-10)."""
    return set(_WRITER_CALLEES) | _INJECTED_WRITERS | set(derive_event_params(trees))


#: A receiver that names the audit trail: ``self._audit.log(...)``, ``chain.send(...)``.
_AUDIT_RECEIVER_RE = re.compile(r"audit|security_event|sec_event|chain|trail", re.IGNORECASE)


def derive_generic_writers(parsed: "list[tuple[Path, ast.AST]]", callees: set[str]) -> dict[str, set[Path]]:
    """Helpers with a GENERIC name (``log``, ``publish``, ``send``, ``on_*``, ``get_*``,
    ``is_*``) that hand a parameter to a writer as the event name: name -> defining files.

    R2-SEC-1: :func:`_derived_callee_ok` refuses those names globally (one ``get`` helper
    must not turn every ``dict.get("a.b")`` into an event), which also dropped the real
    ones - ``AuditLog.log("x.y")`` reaches the chain. They are matched NARROWLY instead:
    see :func:`_generic_call_matches`."""
    out: dict[str, set[Path]] = {}
    for path, tree in parsed:
        for fn, calls in _fn_index(tree):
            if fn.name in callees or _derived_callee_ok(fn.name):
                continue
            a = fn.args
            params = {x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} - {"self", "cls"}
            if not params:
                continue
            if any(_is_writer_call(c, callees) and _passes_param_as_event(c, params)
                   for c in calls):
                out.setdefault(fn.name, set()).add(path)
    return out


def _receiver_text(call: ast.Call) -> str:
    fn = call.func
    if not isinstance(fn, ast.Attribute):
        return ""
    try:
        return ast.unparse(fn.value)
    except Exception:  # noqa: BLE001
        return ""


#: Receivers that are a message bus, a logger or a transport - never the audit trail.
#: ``hub.publish("health.stall_detected")`` and ``logger.log(...)`` stay out even when
#: the name sits in a known event namespace.
_NON_AUDIT_RECEIVER_RE = re.compile(
    r"(^|[._])(hub|bus|event_bus|broker|pubsub|logger|logging|log|_log|queue|socket|sock|ws"
    r"|websocket|redis|client|session|conn|connection|channel|stream|topic|signal|signals"
    r"|emitter|dispatcher|registry|metrics|stats|telemetry|tracer|span|console|stdout|stderr"
    r"|requests|httpx|http|subprocess|os|sys|re|json|parser|ap|sub)$", re.IGNORECASE)


def _generic_writer_classes(parsed: "list[tuple[Path, ast.AST]]",
                            generic: dict[str, set[Path]]) -> dict[str, set[str]]:
    """generic helper name -> classes that define it as a method in a defining file."""
    out: dict[str, set[str]] = {}
    for path, tree in parsed:
        for cls in _tree_index(tree)[1]:
            for fn in cls.body:
                if (isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and path in generic.get(fn.name, ())):
                    out.setdefault(fn.name, set()).add(cls.name)
    return out


def _generic_call_matches(call: ast.Call, path: Path, defined_in: set[Path], *,
                          classes: "set[str] | frozenset[str]" = frozenset(),
                          known_namespace: bool = False) -> bool:
    """A call to a generically named writer helper counts when

      * its receiver names the audit trail, or
      * it is a bare / ``self.`` / ``cls.`` call in the defining file, or
      * its receiver is an instance built in place of a class that defines the helper
        (``Recorder().log("x.y")``), or
      * (round 3) the literal sits in a known event namespace and the receiver is not a
        bus, a logger or a transport - the cross-file ``recorder.log("consent.x")`` /
        ``gate.on_denied("egress.x")`` / ``store.get_and_record("secret.x")`` forms.

    ``hub.subscribe("x.y")`` next to a local ``subscribe`` helper is still the hub's
    method."""
    recv = _receiver_text(call)
    if recv and _AUDIT_RECEIVER_RE.search(recv):
        return True
    if path in defined_in and (isinstance(call.func, ast.Name) or recv in ("self", "cls")):
        return True
    fn = call.func
    if isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Call):
        if _call_name(fn.value) in classes:
            return True
    if not known_namespace:
        return False
    if isinstance(fn, ast.Name):
        return True
    if not recv or isinstance(fn.value, ast.Constant):
        return False  # ``"sep".join(...)``, a call on a literal
    last = recv.rsplit(".", 1)[-1].split("(")[0]
    return not _NON_AUDIT_RECEIVER_RE.search(recv) and not _NON_AUDIT_RECEIVER_RE.search(last)


def _local_buffer_helpers(tree: ast.AST, callees: set[str]) -> set[str]:
    """Functions of THIS file that carry a writer's name but reach no writer.

    ``DAGRunner._audit(event, **details)`` appends to a buffer and hands the record to
    an injected sink; the sink writes ``f"{prefix}.{event}"`` - ``workflow.run.started``,
    which is registered. The un-prefixed ``run.started`` never reaches the chain, so
    registering it would describe a record that does not exist. Used only for a name
    whose prefixed form IS registered, and only for a bare / ``self.`` call in the
    defining file."""
    reaches: dict[str, bool] = {}
    for fn, calls in _fn_index(tree):
        hit = any(_call_name(c) != fn.name and _is_writer_call(c, callees) for c in calls)
        reaches[fn.name] = reaches.get(fn.name, False) or hit
    return {n for n, r in reaches.items() if not r}


class _Scopes:
    """String values a NAME can hold, per lexical scope of one file.

    Round 3 (R3-SEC-3): the unregistered-emission scan read bare string constants only,
    so every other spelling of the same emission was invisible - 0 of 16 planted forms:
    a module constant (``SEAM_EVENT = "compliance.chain_discontinuity"``), a conditional
    (``"a.x" if ok else "a.y"``), a local variable, a dict lookup
    (``{...}.get(decision, "proactive.denied")``) and an f-string of constants."""

    def __init__(self, tree: ast.AST):
        self._assigns: dict[int, dict[str, list[ast.AST]]] = {}
        self._parent: dict[int, int | None] = {}
        self._scope_of_call: dict[int, int] = {}
        self._index(tree, None)

    def _index(self, scope: ast.AST, parent: "int | None") -> None:
        sid = id(scope)
        self._parent[sid] = parent
        table = self._assigns.setdefault(sid, {})
        stack = list(ast.iter_child_nodes(scope))
        while stack:
            node = stack.pop()
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                self._index(node, sid)
                continue
            if isinstance(node, ast.Call):
                self._scope_of_call[id(node)] = sid
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        table.setdefault(t.id, []).append(node.value)
            elif isinstance(node, ast.AnnAssign) and node.value is not None:
                if isinstance(node.target, ast.Name):
                    table.setdefault(node.target.id, []).append(node.value)
            elif isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name):
                table.setdefault(node.target.id, []).append(node.value)
            stack.extend(ast.iter_child_nodes(node))

    def values(self, expr: ast.AST, call: ast.Call, _depth: int = 0) -> set[str]:
        """Every string ``expr`` can evaluate to at ``call`` (empty: not resolvable)."""
        return self._values(expr, self._scope_of_call.get(id(call)), _depth)

    def _values(self, expr: ast.AST, sid: "int | None", depth: int) -> set[str]:
        if depth > 6:
            return set()
        if isinstance(expr, ast.Constant):
            return {expr.value} if isinstance(expr.value, str) else set()
        if isinstance(expr, ast.IfExp):
            return self._values(expr.body, sid, depth + 1) | self._values(expr.orelse, sid, depth + 1)
        if isinstance(expr, ast.BoolOp):
            out: set[str] = set()
            for v in expr.values:
                out |= self._values(v, sid, depth + 1)
            return out
        if isinstance(expr, ast.NamedExpr):
            return self._values(expr.value, sid, depth + 1)
        if isinstance(expr, ast.Name):
            cur = sid
            while cur is not None:
                exprs = self._assigns.get(cur, {}).get(expr.id)
                if exprs:
                    out = set()
                    for e in exprs:
                        out |= self._values(e, cur, depth + 1)
                    return out
                cur = self._parent.get(cur)
            return set()
        if isinstance(expr, ast.JoinedStr):
            parts: list[set[str]] = []
            for piece in expr.values:
                if isinstance(piece, ast.Constant) and isinstance(piece.value, str):
                    parts.append({piece.value})
                elif isinstance(piece, ast.FormattedValue) and piece.format_spec is None:
                    vals = self._values(piece.value, sid, depth + 1)
                    if not vals:
                        return set()
                    parts.append(vals)
                else:
                    return set()
            combos = {""}
            for vals in parts:
                combos = {a + b for a in combos for b in vals}
                if len(combos) > 64:
                    return set()
            return combos
        if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
            left = self._values(expr.left, sid, depth + 1)
            right = self._values(expr.right, sid, depth + 1)
            if left and right and len(left) * len(right) <= 64:
                return {a + b for a in left for b in right}
            return set()
        # ``{...}.get(key, default)`` / ``{...}[key]``: any value of the literal.
        if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Attribute)
                and expr.func.attr == "get"):
            table = self._dict_literal(expr.func.value, sid)
            if table is not None:
                out = set()
                for v in table.values:
                    out |= self._values(v, sid, depth + 1)
                for a in expr.args[1:2]:
                    out |= self._values(a, sid, depth + 1)
                return out
        if isinstance(expr, ast.Subscript):
            table = self._dict_literal(expr.value, sid)
            if table is not None:
                out = set()
                for v in table.values:
                    out |= self._values(v, sid, depth + 1)
                return out
        return set()

    def _dict_literal(self, expr: ast.AST, sid: "int | None") -> "ast.Dict | None":
        if isinstance(expr, ast.Dict):
            return expr
        if isinstance(expr, ast.Name):
            cur = sid
            while cur is not None:
                exprs = self._assigns.get(cur, {}).get(expr.id)
                if exprs:
                    return exprs[0] if len(exprs) == 1 and isinstance(exprs[0], ast.Dict) else None
                cur = self._parent.get(cur)
        return None


def find_unregistered_emissions(severity: set[str],
                                sources: "list[Path] | None" = None) -> dict[str, list[str]]:
    """Heuristic: event names passed to a writer callee - the fixed
    :data:`_WRITER_CALLEES`, the injected writers, every helper
    :func:`derive_writer_callees` finds and the narrowly matched generic helpers of
    :func:`derive_generic_writers` - that have NO ``EVENT_SEVERITY`` entry (the writer
    defaults them to INFO and floors their details). The name is resolved the way
    :func:`find_emitters` resolves it (:func:`_direct_values`) and through
    :class:`_Scopes`: a module constant, a conditional, a local variable, a dict lookup,
    an f-string of constants. A scan of the AST cannot tell a ``security_events`` writer
    from a same-named helper of another sink; the shrink-only baseline absorbs those.

    ``sources`` replaces the repository walk (the scanner's own positive control)."""
    found: dict[str, list[str]] = {}
    parsed: list[tuple[Path, ast.AST]] = []
    for path in (sources if sources is not None else _py_sources()):
        try:
            parsed.append((path, ast.parse(path.read_text("utf-8", errors="ignore"))))
        except SyntaxError:
            continue
    trees = [t for _p, t in parsed]
    fixed = set(_WRITER_CALLEES) | _INJECTED_WRITERS
    derived_params = derive_event_params(trees)
    callees = fixed | set(derived_params)
    generic_params = derive_event_params(trees, generic=True, base=derived_params)
    generic = {n: paths for n, paths in derive_generic_writers(parsed, callees).items()
               if n in generic_params}
    classes = _generic_writer_classes(parsed, generic)
    namespaces = frozenset(n.partition(".")[0] for n in severity if "." in n)
    # ``workflow.run.started`` -> ``run.started``: the un-prefixed form a prefixing sink
    # is handed (see :func:`_local_buffer_helpers`).
    prefixed = frozenset(n.split(".", 1)[1] for n in severity if n.count(".") >= 2)
    for path, tree in parsed:
        scopes: "_Scopes | None" = None
        buffers: "set[str] | None" = None
        skip = _registry_nodes(tree) if path == SECURITY_EVENTS else set()
        for node in _tree_index(tree)[2]:
            if id(node) in skip:
                continue
            name = _call_name(node)
            is_generic = False
            if name in callees:
                is_fixed = name in fixed
            elif name in generic:
                is_fixed = False
                is_generic = True
            else:
                continue
            # A fixed writer takes the event as its 1st, 2nd or 3rd positional argument
            # (``write_event(path, "x.y")``, ``_audit(tenant, session, "x.y")``).
            if is_fixed:
                cands = list(node.args[:3]) + [kw.value for kw in node.keywords
                                               if kw.arg in _FIXED_EVENT_KEYWORDS]
            else:
                cands = _event_args(node, generic_params if is_generic else derived_params)
            if not cands:
                continue
            for arg in cands:
                if isinstance(arg, ast.Constant):  # the common case needs no scope table
                    values = {arg.value} if isinstance(arg.value, str) else ()
                elif isinstance(arg, (ast.Dict, ast.List, ast.Tuple, ast.Set, ast.Lambda,
                                      ast.Attribute, ast.Starred)):
                    values = ()
                else:
                    if scopes is None:
                        scopes = _Scopes(tree)
                    values = scopes.values(arg, getattr(node, "_origin", node))
                for v in values:
                    if v in severity or not looks_like_event_name(
                            v, fixed_writer=is_fixed, namespaces=namespaces):
                        continue
                    if is_fixed and v in prefixed:
                        if buffers is None:
                            buffers = _local_buffer_helpers(tree, callees)
                        if name in buffers and (isinstance(node.func, ast.Name)
                                                or _receiver_text(node) in ("self", "cls")):
                            continue  # written to the chain as ``<prefix>.<v>``, registered
                    if is_generic and not _generic_call_matches(
                            node, path, generic[name], classes=classes.get(name, frozenset()),
                            known_namespace=v.partition(".")[0] in namespaces):
                        continue
                    try:
                        rel = str(path.relative_to(REPO_ROOT))
                    except ValueError:
                        rel = str(path)
                    if rel not in found.setdefault(v, []):
                        found[v].append(rel)
    return found


def runtime_registrations(se) -> tuple[dict[str, set[str]], list[str]]:
    """Import every runtime registrar; return (event → fields, import failures)."""
    recorded: dict[str, set[str]] = {}
    original = se.register_event_allowlist

    def spy(event_type, fields):
        recorded.setdefault(str(event_type), set()).update(map(str, fields))
        return original(event_type, fields)

    se.register_event_allowlist = spy
    failures: list[str] = []
    for p in (REPO_ROOT, REPO_ROOT / "corvin_operator", REPO_ROOT / "corvin_operator" / "bridges" / "shared"):
        if str(p) not in sys.path:
            sys.path.append(str(p))
    try:
        for path in _py_sources():
            src = path.read_text("utf-8", errors="ignore")
            if "register_event_allowlist(" not in src and "register_events(" not in src:
                continue
            if path == SECURITY_EVENTS or path.name == Path(__file__).name:
                continue
            mod = ".".join(path.relative_to(REPO_ROOT).with_suffix("").parts)
            try:
                importlib.import_module(mod)
            except BaseException as exc:  # noqa: BLE001 — optional deps, sys.exit in scripts
                failures.append(f"{mod} ({type(exc).__name__})")
        try:
            sink = importlib.import_module("core.deployment.audit_sink")
            for et, fields in sink.registered_events().items():
                recorded.setdefault(et, set()).update(fields)
        except BaseException as exc:  # noqa: BLE001
            failures.append(f"core.deployment.audit_sink ({type(exc).__name__})")
    finally:
        se.register_event_allowlist = original
    return recorded, failures


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", action="store_true",
                    help="also import runtime registrars and report static/runtime disagreement")
    ap.add_argument("--list-unregistered", action="store_true",
                    help="list every literal event name emitted without an EVENT_SEVERITY entry")
    ap.add_argument("--baseline", default=str(BASELINE),
                    help="shrink-only baseline of names emitted without a severity entry (default: %(default)s)")
    ap.add_argument("--init-baseline", action="store_true",
                    help="create the baseline from the current scan; refuses when it already exists")
    ap.add_argument("--shrink-baseline", action="store_true",
                    help="drop baseline entries that are registered now; never adds an entry")
    args = ap.parse_args(argv)
    # ast.parse of repo sources re-emits their invalid-escape SyntaxWarnings.
    warnings.simplefilter("ignore", SyntaxWarning)

    try:
        se = load_registry()
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: forge.security_events not importable: {type(exc).__name__}: {exc}")
        return 3

    severity = dict(se.EVENT_SEVERITY)
    allow = {k: frozenset(v) for k, v in se._EVENT_ALLOWLIST.items()}
    gap = set(severity) - set(allow)
    no_severity = sorted(set(allow) - set(severity))

    emitters = find_emitters(gap)
    emitted = sorted(e for e in gap if e in emitters and e not in FLOOR_BY_DESIGN)
    by_design = sorted(e for e in gap if e in FLOOR_BY_DESIGN)
    no_emitter = sorted(e for e in gap if e not in emitters and e not in FLOOR_BY_DESIGN)
    stale_design = sorted(e for e in FLOOR_BY_DESIGN if e in allow or e not in severity)

    print("Audit event registry (static, as imported)")
    print(f"  EVENT_SEVERITY entries : {len(severity)}")
    print(f"  _EVENT_ALLOWLIST entries: {len(allow)}")
    print(f"  gap (severity, no allowlist): {len(gap)}")
    print(f"    emitted, unregistered : {len(emitted)}")
    print(f"    floor by design       : {len(by_design)}")
    print(f"    no emitter in repo    : {len(no_emitter)}")
    print(f"  allowlisted, no severity: {len(no_severity)}")

    for e in emitted:
        print(f"  FAIL emitted without allowlist: {e}  ({', '.join(emitters[e][:3])})")
    for e in no_severity:
        print(f"  FAIL allowlisted without severity: {e}")
    for e in by_design:
        print(f"  floor by design: {e} — {FLOOR_BY_DESIGN[e]}")
    for e in no_emitter:
        print(f"  no emitter: {e}")
    for e in stale_design:
        print(f"  NOTE FLOOR_BY_DESIGN entry is stale (now allowlisted or unregistered): {e}")

    unregistered = find_unregistered_emissions(set(severity))
    print(f"\n  emitted with no EVENT_SEVERITY entry (heuristic scan): {len(unregistered)}"
          + ("" if args.list_unregistered else "  (--list-unregistered to show)"))
    if args.list_unregistered:
        for e in sorted(unregistered):
            print(f"    {e}  ({', '.join(unregistered[e][:2])})")

    # Shrink-only ratchet (PLAN-0965 F-11): the scan above used to be informational, so the number of events
    # written with the default severity and a floored detail set could only grow. The baseline holds today's
    # names; a NEW name fails the gate, a name that got registered is a note (shrink the file).
    base_path = Path(args.baseline)
    if args.init_baseline:
        if base_path.exists():
            print(f"ERROR: {base_path} exists; the baseline is shrink-only (use --shrink-baseline)")
            return 2
        base_path.write_text(json.dumps(sorted(unregistered), indent=1) + "\n", encoding="utf-8")
        print(f"baseline written: {len(unregistered)} names -> {base_path}")
        return 0
    baseline: set[str] | None = None
    if base_path.exists():
        try:
            baseline = {str(x) for x in json.loads(base_path.read_text("utf-8"))}
        except (ValueError, OSError) as exc:
            print(f"ERROR: baseline unreadable ({type(exc).__name__}): {base_path}")
            return 2
    new_unregistered: list[str] = []
    if baseline is None:
        print("  NOTE no baseline file: the scan is informational (--init-baseline creates it)")
    else:
        new_unregistered = sorted(set(unregistered) - baseline)
        # R2-SEC-1: two different reasons a baseline name is not reported any more, and
        # only ONE of them is progress. "Registered now" has an EVENT_SEVERITY entry.
        # "Not seen" merely stopped matching the scan - its emitter was renamed, moved
        # behind a helper the scan cannot follow, or a filter swallowed it. Recording that
        # as a registration would shrink the baseline for a name that is still written
        # unregistered, so --shrink-baseline removes registered names only.
        registered_now = sorted(baseline & set(severity))
        not_seen = sorted(baseline - set(unregistered) - set(severity))
        print(f"  baseline: {len(baseline)} names, new: {len(new_unregistered)}, "
              f"now registered: {len(registered_now)}, not seen by the scan: {len(not_seen)}")
        for e in new_unregistered:
            print(f"  FAIL emitted without an EVENT_SEVERITY entry and not in the baseline: {e}  "
                  f"({', '.join(unregistered[e][:2])}) - register it in security_events.py")
        for e in registered_now:
            print(f"  NOTE registered now, remove from the baseline (--shrink-baseline): {e}")
        for e in not_seen:
            print(f"  NOTE not seen by the scan any more and NOT registered (kept in the baseline; "
                  f"remove by hand only after confirming the emitter is gone): {e}")
        if args.shrink_baseline and registered_now:
            base_path.write_text(json.dumps(sorted(baseline - set(registered_now)), indent=1) + "\n",
                                 encoding="utf-8")
            print(f"baseline shrunk by {len(registered_now)} -> {base_path}")

    if args.runtime:
        recorded, failures = runtime_registrations(se)
        wider = {e: sorted(f - allow[e]) for e, f in recorded.items()
                 if e in allow and f - allow[e]}
        runtime_only = sorted(e for e in recorded if e not in allow)
        print(f"\nRuntime registrations: {len(recorded)} event types "
              f"({len(failures)} registrar modules not importable here)")
        print(f"  runtime-only (no static entry): {len(runtime_only)}")
        for e in runtime_only:
            print(f"    {e}")
        print(f"  runtime wider than static     : {len(wider)}")
        for e, extra in sorted(wider.items()):
            print(f"    {e}: +{extra}")
        for f in failures:
            print(f"  not imported: {f}")

    ok = not emitted and not no_severity and not new_unregistered
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
