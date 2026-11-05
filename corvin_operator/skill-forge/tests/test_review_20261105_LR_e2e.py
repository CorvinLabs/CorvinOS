"""Adversarial review 2026-11-05 (area LR) — SkillForge grade cap + promotion gates.

Every case drives the REAL MCP server over stdio (``skill_forge.mcp_server`` in a
subprocess, the transport the worker uses) against a sandboxed CORVIN_HOME.

LR-1  a NAMESPACED persona passed ``force=True`` to ``skill_promote`` and skipped
      every promotion gate (session->user is an "undefined path" that force
      waives), although the same persona is refused project/user on create
      ("earn it through skill_promote", D-05).
LR-2  ``skill_grade`` treated ANY caller-supplied ``run_id`` as an organic grade,
      so a persona lifted its own skill over the session->project bar
      (>=3 grades, mean >= 0.5) with three fabricated run ids at score 1.0.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from test_namespace_gate import (  # noqa: E402
    _SFMCPClient, _audit_events, _call, _create_args,
)


def test_lr1_namespaced_persona_cannot_force_promote_past_the_gates():
    sandbox = Path(tempfile.mkdtemp(prefix="sf-lr1-"))
    try:
        coder = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            coder.initialize()
            r = _call(coder, "skill_create", _create_args("code.lr1_skill", scope="session"))
            assert r.get("isError") is False, r["content"][0]["text"]
            # only the capped bootstrap seed exists — no gate is met
            r = _call(coder, "skill_promote",
                      {"name": "code.lr1_skill", "to": "user", "force": True})
            assert r.get("isError") is True, f"forced promotion must be refused: {r}"
            assert "scope-gate" in r["content"][0]["text"]
            r = _call(coder, "skill_list", {})
            scopes = {s["name"]: s["scope"] for s in r["structuredContent"]["data"]["skills"]}
            assert scopes.get("code.lr1_skill") == "session", scopes
        finally:
            coder.close()
        events = _audit_events(sandbox)
        assert not [e for e in events if e["event_type"] == "skill.promote"], events
        denied = [e for e in events if e["event_type"] == "skill.scope_denied"
                  and e.get("tool") == "code.lr1_skill"]
        assert denied and denied[-1]["details"]["operation"] == "skill_promote", denied
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_lr1_operator_force_still_works():
    """The NAMED wildcard (operator) caller keeps its audited force=True override."""
    sandbox = Path(tempfile.mkdtemp(prefix="sf-lr1b-"))
    try:
        op = _SFMCPClient(corvin_home=sandbox, caller_persona="skill-forge")
        try:
            op.initialize()
            r = _call(op, "skill_create", _create_args("ops.lr1_skill", scope="session"))
            assert r.get("isError") is False, r["content"][0]["text"]
            r = _call(op, "skill_promote", {"name": "ops.lr1_skill", "to": "user", "force": True})
            assert r.get("isError") is False, r["content"][0]["text"]
        finally:
            op.close()
        promotes = [e for e in _audit_events(sandbox) if e["event_type"] == "skill.promote"]
        assert len(promotes) == 1 and promotes[0]["details"]["force"] is True
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def test_lr2_persona_self_grades_are_capped_and_cannot_promote():
    sandbox = Path(tempfile.mkdtemp(prefix="sf-lr2-"))
    try:
        coder = _SFMCPClient(corvin_home=sandbox, caller_persona="coder")
        try:
            coder.initialize()
            r = _call(coder, "skill_create", _create_args("code.lr2_skill", scope="session"))
            assert r.get("isError") is False, r["content"][0]["text"]
            for n in range(3):
                r = _call(coder, "skill_grade",
                          {"name": "code.lr2_skill", "run_id": f"made-up-{n}", "score": 1.0})
                assert r.get("isError") is False, r["content"][0]["text"]
            mean = r["structuredContent"]["data"]["mean_score"]
            assert mean <= 0.3 + 1e-9, f"self-awarded grades lifted the mean to {mean}"
            r = _call(coder, "skill_promote", {"name": "code.lr2_skill", "to": "project"})
            assert r.get("isError") is True, f"promotion on self-grades must be refused: {r}"
            assert "promotion gate" in r["content"][0]["text"]
        finally:
            coder.close()
        grades = [e for e in _audit_events(sandbox) if e["event_type"] == "skill.grade"
                  and str(e["details"].get("run_id", "")).startswith("made-up-")]
        assert len(grades) == 3
        assert all(g["details"]["organic"] is False and g["details"]["capped"] is True
                   for g in grades), grades
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)
