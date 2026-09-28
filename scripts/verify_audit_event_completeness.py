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
    them to INFO).

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
import os
import re
import sys
import warnings
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
FORGE_DIR = REPO_ROOT / "corvin_operator" / "forge"
SECURITY_EVENTS = FORGE_DIR / "forge" / "security_events.py"

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
_REGISTRY_NAMES = {"EVENT_SEVERITY", "_EVENT_ALLOWLIST", "_EVENT_ALLOWLIST_EXTENSIONS"}


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


#: Writer callees whose first/second positional argument is an event name.
_WRITER_CALLEES = {"write_event", "_write_event", "_log_security_event", "log_security_event",
                   "audit_event", "_audit", "_emit", "emit_event"}
_EVENT_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(\.[A-Za-z0-9_]+)+$")


def find_unregistered_emissions(severity: set[str]) -> dict[str, list[str]]:
    """Heuristic: literal event names passed to a known writer callee that have
    NO ``EVENT_SEVERITY`` entry (the writer defaults them to INFO and floors
    their details). Informational — a literal scan cannot tell a
    ``security_events`` writer from a same-named helper of another sink, so
    it reports, it does not gate."""
    found: dict[str, list[str]] = {}
    for path in _py_sources():
        try:
            tree = ast.parse(path.read_text("utf-8", errors="ignore"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or _call_name(node) not in _WRITER_CALLEES:
                continue
            for arg in node.args[:2]:
                v = arg.value if isinstance(arg, ast.Constant) else None
                if (isinstance(v, str) and _EVENT_NAME_RE.match(v) and v not in severity
                        and not v.endswith((".jsonl", ".json", ".py"))):
                    rel = str(path.relative_to(REPO_ROOT))
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
    print(f"\n  emitted with no EVENT_SEVERITY entry (heuristic, informational): {len(unregistered)}"
          + ("" if args.list_unregistered else "  (--list-unregistered to show)"))
    if args.list_unregistered:
        for e in sorted(unregistered):
            print(f"    {e}  ({', '.join(unregistered[e][:2])})")

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

    ok = not emitted and not no_severity
    print("\nPASS" if ok else "\nFAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
