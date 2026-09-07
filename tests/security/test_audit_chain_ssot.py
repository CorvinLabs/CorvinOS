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
PY = str(REPO / ".venv" / "bin" / "python")


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
        sys.path.insert(0, str(R / "operator" / "forge"))
        sys.path.insert(0, str(R / "operator" / "skill-forge"))
        sys.path.append(str(R / "operator" / "bridges" / "shared"))
        OUT = {{}}
    """)
    proc = subprocess.run(
        [PY, "-c", preamble + textwrap.dedent(code) + "\nprint('@@' + json.dumps(OUT))"],
        capture_output=True, text=True, env=env, cwd=str(REPO), timeout=180,
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
        from forge.paths import tenant_audit_chain as forge_chain
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_bp", R / "operator" / "bridges" / "shared" / "paths.py")
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
        from forge.paths import tenant_audit_chain, all_audit_chains

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
        sys.path.insert(0, str(R)); sys.path.insert(0, str(R / "operator" / "forge"))
        from core.paths import tenant_audit_chain as c
        from forge.paths import tenant_audit_chain as f
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "_bp", R / "operator" / "bridges" / "shared" / "paths.py")
        bp = importlib.util.module_from_spec(spec); spec.loader.exec_module(bp)
        print("@@" + json.dumps({{"core": str(c("_default")), "forge": str(f("_default")),
                                 "bridges": str(bp.tenant_audit_chain("_default"))}}))
    """)
    proc = subprocess.run([PY, "-c", code], capture_output=True, text=True,
                          env=env, cwd=str(REPO), timeout=120)
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
        from forge.paths import tenant_audit_chain
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
        from forge.paths import tenant_audit_chain
        reg = SkillRegistry(Path({str(sandbox)!r}))
        OUT["resolved"] = str(reg.audit_path())
        OUT["canonical"] = str(tenant_audit_chain("_default"))
    """, home)
    assert out["resolved"] != out["canonical"], out
    assert out["resolved"] == str(sandbox.parent / "audit.jsonl"), out
