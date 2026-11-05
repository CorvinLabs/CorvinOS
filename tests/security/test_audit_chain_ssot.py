"""There is exactly ONE hash-chained audit file per tenant, and every writer finds it.

R4 finding (2026-09-07, measured on the maintainer install): the GDPR Art. 30/32
chain for tenant ``_default`` was being appended to SIX distinct files across TWO
roots — none of them a symlink of another — because
``forge.security_events.write_event(path, ...)`` takes its path from the CALLER
and every caller composed its own::

    <root>/global/forge/audit.jsonl                   315 MB   bridge adapter, forge
    <root>/tenants/_default/global/forge/audit.jsonl  4.6 MB   console + gateway  (canonical)
    <root>/tenants/_default/global/audit.jsonl        2.0 MB   ACO repair, packaging
    <root>/forge/audit.jsonl                          475 KB   ToolForge, global scope
    <root>/tenants/_default/forge/audit.jsonl          12 KB   ToolForge, tenant scope
    <root>/tenants/_default/audit.jsonl               533 KB   SkillForge

A reader of any single chain saw a fraction of the events.

These tests drive REAL writers in an isolated ``CORVIN_HOME`` (never the live
roots) and assert they land in the SAME file. Every one of them fails on the
pre-R4 code.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
# The interpreter running this suite — NOT a hardcoded <repo>/.venv/bin/python,
# which does not exist on an installed system (uv tool venv; measured on a
# fresh Ubuntu 24.04 install 2026-10-21: five of these tests failed on it with
# FileNotFoundError before they could check anything).
PY = sys.executable


def _run(code: str, home: Path, *, extra_env: dict[str, str] | None = None) -> dict:
    """Run *code* in a fresh interpreter with an isolated CORVIN_HOME.

    A subprocess, not an import: the resolvers cache module state and the point
    of the test is what a REAL process resolves at boot.
    """
    env = dict(os.environ)
    env["CORVIN_HOME"] = str(home)
    env["CORVIN_AUDIT_ANCHOR_KEY"] = str(home / "anchor.key")
    env.pop("VOICE_AUDIT_PATH", None)
    env.pop("FORGE_ROOT", None)
    env.pop("CORVIN_TENANT_ID", None)
    if extra_env:
        env.update(extra_env)
    preamble = textwrap.dedent(f"""
        import sys, json
        from pathlib import Path
        R = Path({str(REPO)!r})
        sys.path.insert(0, str(R))
        sys.path.insert(0, str(R / "corvin_operator" / "forge"))
        sys.path.insert(0, str(R / "corvin_operator" / "skill-forge"))
        sys.path.append(str(R / "corvin_operator" / "bridges" / "shared"))
        OUT = {{}}
    """)
    proc = subprocess.run(
        [PY, "-c", preamble + textwrap.dedent(code) + "\nprint('@@' + json.dumps(OUT))"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180, encoding="utf-8", errors="replace",
    )
    assert proc.returncode == 0, f"subprocess failed:\n{proc.stdout}\n{proc.stderr}"
    line = [l for l in proc.stdout.splitlines() if l.startswith("@@")][-1]
    return json.loads(line[2:])


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "corvin-home"
    h.mkdir()
    return h


def test_three_paths_modules_agree_on_the_chain(home: Path):
    """``core.paths``, ``forge.paths`` and ``bridges/shared/paths`` are mirrors.

    Three copies of the resolver exist by design (core/ cannot import forge/ at
    every call site; the bridge daemons do not have core/ on sys.path). They must
    never drift — that drift is what produced the split.
    """
    out = _run("""
        from core.paths import tenant_audit_chain as core_chain
        from corvin_operator.forge.forge.paths import tenant_audit_chain as forge_chain
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_bp", R / "corvin_operator" / "bridges" / "shared" / "paths.py")
        bp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bp)
        OUT["core"] = str(core_chain("_default"))
        OUT["forge"] = str(forge_chain("_default"))
        OUT["bridges"] = str(bp.tenant_audit_chain("_default"))
    """, home)
    assert out["core"] == out["forge"] == out["bridges"], out
    assert out["core"].endswith("tenants/_default/global/forge/audit.jsonl"), out


def test_two_real_writers_land_in_one_chain(home: Path):
    """The bridge audit module and the ToolForge registry — two entry points that
    wrote two DIFFERENT files before R4 — now append to the same one."""
    out = _run("""
        import audit as bridge_audit
        from forge.registry import Registry
        from corvin_operator.forge.forge.paths import tenant_audit_chain, all_audit_chains

        bridge_audit.audit_event("bridge.message_received", channel="test", chat_key="k")
        reg = Registry(Path(sys.argv[0]).parent if False else None) if False else None
        OUT["bridge_path"] = str(bridge_audit.audit_path())
        OUT["canonical"] = str(tenant_audit_chain("_default"))

        import os
        home = Path(os.environ["CORVIN_HOME"])
        r = Registry(home / "tenants" / "_default" / "forge")
        OUT["toolforge_path"] = str(r._audit_chain())

        OUT["files"] = sorted(str(p.relative_to(home))
                              for p in home.rglob("audit.jsonl"))
        OUT["written"] = {}
        for label, p in all_audit_chains("_default").items():
            if p.exists():
                OUT["written"][label] = sum(1 for _ in p.open())
    """, home)
    assert out["bridge_path"] == out["canonical"], out
    assert out["toolforge_path"] == out["canonical"], out
    assert out["files"] == ["tenants/_default/global/forge/audit.jsonl"], out
    assert list(out["written"]) == ["canonical"], out
    assert out["written"]["canonical"] >= 1, out


def test_source_checkout_branch_with_corvin_home_unset(home: Path):
    """With ``CORVIN_HOME`` unset, all three resolvers must pick the SAME root.

    In a source checkout that is ``<repo>/.corvin`` — the branch the 203
    hard-wired ``Path.home()/".corvin"`` copies do not have, and the reason they
    diverge from both canonical resolvers whenever the env var is absent.
    """
    env = dict(os.environ)
    env.pop("CORVIN_HOME", None)
    env.pop("VOICE_AUDIT_PATH", None)
    env.pop("FORGE_ROOT", None)
    code = textwrap.dedent(f"""
        import sys, json
        from pathlib import Path
        R = Path({str(REPO)!r})
        sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "corvin_operator" / "forge"))
        from core.paths import tenant_audit_chain as c
        from corvin_operator.forge.forge.paths import tenant_audit_chain as f
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_bp", R / "corvin_operator" / "bridges" / "shared" / "paths.py")
        bp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bp)
        print("@@" + json.dumps({{"core": str(c("_default")), "forge": str(f("_default")),
                                 "bridges": str(bp.tenant_audit_chain("_default"))}}))
    """)
    proc = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                          env=env, cwd=str(REPO), timeout=120, encoding="utf-8", errors="replace")
    assert proc.returncode == 0, proc.stderr
    out = json.loads([l for l in proc.stdout.splitlines() if l.startswith("@@")][-1][2:])
    assert out["core"] == out["forge"] == out["bridges"], out
    assert out["core"] == str(REPO / ".corvin" / "tenants" / "_default"
                              / "global" / "forge" / "audit.jsonl"), out


def test_skillforge_writes_the_tenant_chain_not_a_sibling(home: Path):
    """``SkillRegistry(<tenant_home>/skill-forge)`` — the shape
    ``context_engineering`` and the skill-creator bridge construct — used to
    append to ``<tenant_home>/audit.jsonl``."""
    out = _run("""
        import os
        from skill_forge.registry import SkillRegistry
        from corvin_operator.forge.forge.paths import tenant_audit_chain
        home = Path(os.environ["CORVIN_HOME"])
        reg = SkillRegistry(home / "tenants" / "_default" / "skill-forge")
        OUT["resolved"] = str(reg.audit_path())
        OUT["canonical"] = str(tenant_audit_chain("_default"))
    """, home)
    assert out["resolved"] == out["canonical"], out


def test_a_workspace_outside_corvin_home_keeps_its_sibling_chain(tmp_path: Path):
    """A test/standalone workspace must NEVER be redirected onto the operator's
    real chain — that would make every unit test append to an append-only,
    never-erasable compliance file."""
    home = tmp_path / "h"; home.mkdir()
    sandbox = tmp_path / "elsewhere" / "skill-forge"
    out = _run(f"""
        from skill_forge.registry import SkillRegistry
        from corvin_operator.forge.forge.paths import tenant_audit_chain
        reg = SkillRegistry(Path({str(sandbox)!r}))
        OUT["resolved"] = str(reg.audit_path())
        OUT["canonical"] = str(tenant_audit_chain("_default"))
    """, home)
    assert out["resolved"] != out["canonical"], out
    assert out["resolved"] == str(sandbox.parent / "audit.jsonl"), out


# ── core/orchestration (DLG-4, 2026-11-05) ──────────────────────────────────
#
# The orchestration MCP server (``workflow_run`` / ``workflow_resume``) composed
# ``<home>/tenants/<tid>/audit.jsonl`` by hand — one more sibling no reader
# opened. The caller scan covers the whole package tree so the next
# hand-composed chain path fails here, not in an auditor's report.

_ORCHESTRATION = REPO / "core" / "orchestration"
_CHAIN_LITERAL = "audit" + ".jsonl"


def test_orchestration_composes_no_chain_path_by_hand():
    """No production module under core/orchestration names the chain file
    itself — every writer resolves ``tenant_audit_chain()``."""
    scanned = 0
    offenders = []
    for p in sorted(_ORCHESTRATION.rglob("*.py")):
        if "tests" in p.relative_to(_ORCHESTRATION).parts:
            continue
        scanned += 1
        for n, line in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            code = line.split("#", 1)[0]
            if _CHAIN_LITERAL in code:
                offenders.append(f"{p.relative_to(REPO)}:{n}: {line.strip()}")
    # Positive control: the scan reached the module this guard exists for.
    assert (_ORCHESTRATION / "corvin_orchestration" / "mcp_server.py").is_file()
    assert scanned > 5, scanned
    assert not offenders, (
        "hand-composed audit-chain path(s) — resolve tenant_audit_chain() instead:\n"
        + "\n".join(offenders)
    )


def test_orchestration_mcp_sink_writes_the_tenant_chain(home: Path):
    """A real ``workflow.*`` record from the MCP server's sink lands on THE
    tenant chain and nowhere else."""
    out = _run("""
        import os
        from core.orchestration.corvin_orchestration import mcp_server
        from corvin_operator.forge.forge.paths import tenant_audit_chain
        home = Path(os.environ["CORVIN_HOME"])
        OUT["forge"] = mcp_server._FORGE_AVAILABLE
        sink = mcp_server._audit_sink_for("_default", "workflow")
        sink({"event": "node_started", "run_id": "r1"})
        OUT["canonical"] = str(tenant_audit_chain("_default").relative_to(home))
        OUT["files"] = sorted(str(p.relative_to(home)) for p in home.rglob("audit.jsonl"))
        lines = tenant_audit_chain("_default").read_text().splitlines()
        OUT["events"] = [json.loads(l)["event_type"] for l in lines]
    """, home)
    assert out["forge"] is True, out
    assert out["files"] == [out["canonical"]], out
    assert "workflow.node_started" in out["events"], out


def test_stray_orchestration_chain_is_seamed_not_merged(home: Path):
    """The pre-fix file ``<home>/tenants/<tid>/audit.jsonl`` already exists on
    installs. It is ``legacy_audit_chains()['tenant_root']``, so the boot
    tripwire links it with an ``audit.chain_supersedes`` seam — its bytes stay
    untouched (append-only history is never merged, rewritten or deleted)."""
    out = _run("""
        import os
        from forge.security_events import write_event, chain_seam_links
        from corvin_operator.forge.forge.paths import tenant_audit_chain, legacy_audit_chains
        from core.compliance.corvin_compliance_reports import tripwire
        home = Path(os.environ["CORVIN_HOME"])
        stray = home / "tenants" / "_default" / "audit.jsonl"
        write_event(stray, "workflow.node_started", severity="INFO", details={"run_id": "old"})
        before = stray.read_bytes()
        OUT["is_legacy"] = legacy_audit_chains("_default")["tenant_root"] == stray
        tripwire._check_audit_unification()
        OUT["unchanged"] = stray.read_bytes() == before
        links = chain_seam_links(tenant_audit_chain("_default"))
        OUT["forward"] = [l for l in links if l["direction"] == "supersedes"]
        OUT["backward"] = [l["direction"] for l in chain_seam_links(stray)]
    """, home)
    assert out["is_legacy"] is True, out
    assert out["unchanged"] is True, out
    assert len(out["forward"]) == 1 and out["forward"][0]["records"] == 1, out
    assert out["backward"] == ["superseded_by"], out


# ── Review 2026-11-05 (SEC-7): no writer composes the host-wide chain ─────────
#
# incident_tracker (EU AI Act Art. 73), anthropic_batch, the forge runner's
# fallback and the corvinid erasure fallback each built
# ``corvin_home()/"global"/.../audit.jsonl`` by hand and so wrote records no
# verifier, report or panel reads. This scan finds every such expression in
# PRODUCTION source: a ``/`` chain rooted at a corvin-home resolver (the call,
# or a local name bound to it) whose first literal is ``"global"`` and which
# names ``audit.jsonl`` — directly, or through a name bound to
# ``<home>/"global"/"forge"``. The only modules allowed such an expression
# READ the historical file or protect it; each is listed with its reason.

import ast as _ast

_HOME_RESOLVERS = frozenset({"corvin_home", "_corvin_home", "get_corvin_home",
                             "_default_corvin_home"})
_SCAN_DIRS = ("core", "corvin_operator", "scripts", "ops", "corvinOS")
_HOST_CHAIN_READERS = {
    "corvin_operator/bridges/shared/audit_view.py": "/audit me|chat reads the pre-tenancy history for _default",
    "corvin_operator/voice/hooks/path_gate.py": "protects the historical chain from writes",
    "ops/launcher/corvin/migrate_cmd.py": "migration reads the historical chain",
    "scripts/e2e_full_audit_fixture.py": "builds a throwaway fixture home, never a live root",
}


def _call_name(node):
    if isinstance(node, _ast.Call):
        f = node.func
        return f.id if isinstance(f, _ast.Name) else (f.attr if isinstance(f, _ast.Attribute) else None)
    return None


def _div_chain(node):
    """``(base, literals, wrapped)``: the root of a ``/`` chain, its string literals, and
    whether the root sat inside ``Path(...)``. ``Path.home() / ".corvin"`` is one root."""
    parts = []
    cur = node
    while isinstance(cur, _ast.BinOp) and isinstance(cur.op, _ast.Div):
        parts.append(cur.right)
        cur = cur.left
    parts.reverse()
    wrapped = False
    if _call_name(cur) == "Path" and getattr(cur, "args", None):
        cur = cur.args[0]
        wrapped = True
    lits = [p.value if isinstance(p, _ast.Constant) and isinstance(p.value, str) else None
            for p in parts]
    if _call_name(cur) == "home" and lits[:1] == [".corvin"]:  # Path.home() / ".corvin"
        lits = lits[1:]
        wrapped = True
    return cur, lits, wrapped


def _is_env_home(node) -> bool:
    """``os.environ.get("CORVIN_HOME")`` / ``os.getenv("CORVIN_HOME")`` /
    ``os.environ["CORVIN_HOME"]`` (RS-9: the ``Path(env)`` shape was invisible)."""
    if isinstance(node, _ast.Call) and _call_name(node) in ("get", "getenv") and node.args:
        a = node.args[0]
        return isinstance(a, _ast.Constant) and a.value == "CORVIN_HOME"
    if isinstance(node, _ast.Subscript):
        sl = node.slice
        return isinstance(sl, _ast.Constant) and sl.value == "CORVIN_HOME"
    return False


def _host_chain_lines(src: str) -> list[int]:
    try:
        tree = _ast.parse(src)
    except SyntaxError:
        return []
    homes: set[str] = set()
    forge_dirs: set[str] = set()
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign) and (_call_name(node.value) in _HOME_RESOLVERS
                                              or _is_env_home(node.value)):
            homes |= {t.id for t in node.targets if isinstance(t, _ast.Name)}

    def _is_home(base, wrapped=False):
        # A resolver call, a name bound to one or to the CORVIN_HOME env value, the env
        # value itself, and - inside ``Path(...)`` - ANY call (``Path(_ch())``, RS-9).
        if _call_name(base) in _HOME_RESOLVERS or _is_env_home(base):
            return True
        if isinstance(base, _ast.Name) and base.id in homes:
            return True
        return wrapped and isinstance(base, _ast.Call)
    for node in _ast.walk(tree):
        if isinstance(node, _ast.Assign) and isinstance(node.value, _ast.BinOp):
            base, lits, wrapped = _div_chain(node.value)
            if _is_home(base, wrapped) and lits == ["global", "forge"]:
                forge_dirs |= {t.id for t in node.targets if isinstance(t, _ast.Name)}
    hits = []
    for node in _ast.walk(tree):
        if not (isinstance(node, _ast.BinOp) and isinstance(node.op, _ast.Div)):
            continue
        base, lits, wrapped = _div_chain(node)
        if _is_home(base, wrapped) and lits[:1] == ["global"] and "audit.jsonl" in lits:
            hits.append(node.lineno)
        elif isinstance(base, _ast.Name) and base.id in forge_dirs and "audit.jsonl" in lits:
            hits.append(node.lineno)
    return sorted(set(hits))


def _is_test_source(p: Path) -> bool:
    s = p.as_posix()
    return ("/tests/" in s or "/test/" in s or p.name.startswith("test_")
            or p.name.endswith("_test.py") or p.name == "conftest.py")


def test_scanner_finds_the_hand_built_host_chain_shapes():
    """Positive control: both shapes the review found are detected."""
    direct = 'from p import corvin_home\nx = corvin_home() / "global" / "forge" / "audit.jsonl"\n'
    bound = ('def f():\n    audit_dir = _corvin_home() / "global" / "forge"\n'
             '    return str(audit_dir / "audit.jsonl")\n')
    local = 'home = corvin_home()\ny = home / "global" / "audit.jsonl"\n'
    canonical = 'z = corvin_home() / "tenants" / t / "global" / "forge" / "audit.jsonl"\n'
    assert _host_chain_lines(direct) == [2]
    assert _host_chain_lines(bound) == [3]
    assert _host_chain_lines(local) == [2]
    assert _host_chain_lines(canonical) == []
    # RS-9 (refutation 2026-11-05): the shapes user_style.py used were invisible.
    env_bound = ('import os\nfrom pathlib import Path\ndef p():\n'
                 '    env = os.environ.get("CORVIN_HOME")\n'
                 '    return Path(env) / "global" / "forge" / "audit.jsonl"\n')
    env_direct = 'z = Path(os.environ["CORVIN_HOME"]) / "global" / "forge" / "audit.jsonl"\n'
    any_call = 'z = Path(_ch()) / "global" / "forge" / "audit.jsonl"\n'
    user_home = 'z = Path.home() / ".corvin" / "global" / "forge" / "audit.jsonl"\n'
    assert _host_chain_lines(env_bound) == [5]
    assert _host_chain_lines(env_direct) == [1]
    assert _host_chain_lines(any_call) == [1]
    assert _host_chain_lines(user_home) == [1]
    assert _host_chain_lines('z = Path(x) / "tenants" / t / "global" / "audit.jsonl"\n') == []


def test_no_production_writer_composes_the_host_wide_chain():
    scanned = 0
    offenders = []
    for d in _SCAN_DIRS:
        for p in sorted((REPO / d).rglob("*.py")):
            if _is_test_source(p) or "node_modules" in p.parts:
                continue
            scanned += 1
            rel = p.relative_to(REPO).as_posix()
            if rel in _HOST_CHAIN_READERS:
                continue
            for n in _host_chain_lines(p.read_text(encoding="utf-8", errors="replace")):
                offenders.append(f"{rel}:{n}")
    # The scan reached the modules the review named.
    assert (REPO / "corvin_operator/bridges/shared/incident_tracker.py").is_file()
    assert scanned > 500, scanned
    assert not offenders, (
        "hand-built <corvin_home>/global/.../audit.jsonl — resolve tenant_audit_chain(tid) "
        "instead (or, for a READER of the historical file, add it to _HOST_CHAIN_READERS "
        "with the reason):\n" + "\n".join(offenders))


def test_allowlisted_readers_still_need_their_exemption():
    """A stale exemption hides the next writer added to that module."""
    stale = [rel for rel in _HOST_CHAIN_READERS
             if not _host_chain_lines((REPO / rel).read_text(encoding="utf-8"))]
    assert not stale, f"exemption no longer needed, remove it: {stale}"


def test_incident_opened_lands_in_the_tenant_chain(home: Path):
    """SEC-7 repro: incident.opened (EU AI Act Art. 73) is written to THE chain,
    not to <home>/global/forge/audit.jsonl."""
    out = _run("""
        import os, sys
        sys.path.insert(0, str(R / "corvin_operator" / "bridges" / "shared"))
        os.environ.pop("VOICE_AUDIT_PATH", None)
        import incident_tracker
        from corvin_operator.forge.forge.paths import tenant_audit_chain
        home = Path(os.environ["CORVIN_HOME"])
        incident_tracker.open_incident(category="chain_integrity", trigger_event="x.y",
                                       trigger_chain_hash="ab" * 16, description="d")
        chain = tenant_audit_chain("_default")
        OUT["canonical"] = chain.is_file() and "incident.opened" in chain.read_text()
        OUT["host"] = (home / "global" / "forge" / "audit.jsonl").exists()
    """, home)
    assert out == {"canonical": True, "host": False}, out
