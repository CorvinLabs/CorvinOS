"""LIVE: a real ``claude -p`` (haiku) turn routed through ``resolve_worker_engine``
leaves a ``skill.executed`` record for ``os.delegation_router`` WITH a LoM.

Opt-in: ``CLAUDE_LIVE_E2E=1`` and the ``claude`` CLI on PATH (costs credits).

The path is the production one, not a re-implementation:

1. ``core.skills.boot.boot_skills`` boots the ACP registry under a throw-away
   ``CORVIN_HOME`` with the REAL core audit writer (``audit.audit_event`` →
   ``forge.security_events`` floor) — nothing mocked;
2. ``operator/bridges/shared/delegation_policy.resolve_worker_engine`` — the one
   shared routing function every surface calls — decides the engine and runs
   ``os.delegation_router`` in SHADOW mode (ADR-0613);
3. the chosen engine (``native``) is what ``claude -p --model haiku`` is asked
   for, and the model's answer is checked;
4. the tenant chain on disk carries exactly one ``skill.executed`` for the
   router with ``lom`` set to the shadow call site, its ``lom_hash`` bound to
   that function, and a content-free ``decision`` (engine + shadow + bundled).

Run: CLAUDE_LIVE_E2E=1 .venv/bin/python -m pytest -q -o addopts="" -p no:cacheprovider \
     tests/skills/test_delegation_router_live_llm.py -s
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
_SHARED = REPO / "operator" / "bridges" / "shared"

live = pytest.mark.skipif(
    os.environ.get("CLAUDE_LIVE_E2E", "") != "1" or shutil.which("claude") is None,
    reason="live delegation-router E2E needs CLAUDE_LIVE_E2E=1 and the claude CLI",
)


def _load_delegation_policy():
    for p in (str(_SHARED), str(REPO / "operator" / "forge"), str(REPO / "core" / "plugins")):
        if p not in sys.path:
            sys.path.insert(0, p)
    spec = importlib.util.spec_from_file_location("delegation_policy", _SHARED / "delegation_policy.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("delegation_policy", mod)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.mark.live
@pytest.mark.live
@live
def test_real_haiku_turn_leaves_an_attributed_shadow_router_record(tmp_path: Path, monkeypatch):
    home = tmp_path / "corvin-home"
    (home / "tenants" / "_default").mkdir(parents=True)
    chain = home / "audit.jsonl"
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(chain))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    # 1. boot the registry with the REAL core writer (audit_emit resolved lazily)
    from core.skills.boot import boot_skills
    from core.skills.skill_registry_phase1 import SkillsRegistry, get_registry

    registered = boot_skills("_default", wire_learning=False)
    assert "os.delegation_router" in registered

    # 2. the production routing decision (+ shadow record)
    dp = _load_delegation_policy()
    engine = dp.resolve_worker_engine(
        mode="native", force_delegate=False, is_big_data=False,
        tde_available=False, quota_ok=True, tenant_id="_default",
    )
    assert engine == "native"

    # 3. the real LLM turn on the engine the policy chose (native = claude -p)
    proc = subprocess.run(
        ["claude", "-p", "Reply with exactly the single word PONG and nothing else.", "--model", "haiku"],
        capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    print("\n[live] haiku said:", proc.stdout.strip()[:80])
    assert "PONG" in proc.stdout.upper()

    # 4. attribution on disk
    records = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    routed = [
        r for r in records
        if r.get("event_type") == "skill.executed"
        and (r.get("details") or {}).get("skill_id") == "os.delegation_router"
    ]
    assert len(routed) == 1, [r.get("event_type") for r in records]
    d = routed[0]["details"]
    assert d["status"] == "success"
    assert d["lom"] == "operator/bridges/shared/delegation_policy.py:_acp_shadow_route"
    src = (_SHARED / "delegation_policy.py").read_text()
    import ast
    node = next(n for n in ast.walk(ast.parse(src))
                if isinstance(n, ast.FunctionDef) and n.name == "_acp_shadow_route")
    assert d["lom_hash"] == hashlib.sha256(ast.get_source_segment(src, node).encode()).hexdigest()
    assert d["decision"]["shadow"] is True
    assert d["decision"]["bundled_engine"] == "native"
    assert d["decision"]["engine"].startswith("claude-")
    assert "PONG" not in json.dumps(records)  # content-free chain
    assert "_dropped_fields" not in d, d
    assert isinstance(get_registry(), SkillsRegistry)
    print("[live] shadow record:", json.dumps(d["decision"]), "lom_hash=", d["lom_hash"][:16])
