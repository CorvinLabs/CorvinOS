"""A gate skill bound through the CEL skill channel names THIS tenant's knowledge base (ADR-2265).

The console chat receives skill bodies through ``render_skill_bindings``; the gate skills carry
``{{KB_PY}}`` / ``{{KB_DIR}}`` / ``{{PYTHON}}`` placeholders instead of a maintainer's path.
"""
from __future__ import annotations

from types import SimpleNamespace

from context_engineering.stages import binding


def _bundle(tenant: str, body: str):
    ref = SimpleNamespace(skill_id="adr_gate", body=body)
    return SimpleNamespace(skills_to_bind=[ref], scratch={"_ctx": SimpleNamespace(tenant_id=tenant)})


def test_placeholders_render_to_the_bundles_tenant_kb(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CORVIN_KB_REPO", raising=False)
    out = binding.render_skill_bindings(_bundle("acme", "Run `{{PYTHON}} {{KB_PY}} check` in {{KB_DIR}}."))
    kb = (tmp_path / "home" / "tenants" / "acme" / "knowledge").as_posix()
    assert f"{kb}/scripts/kb.py check" in out and f"in {kb}." in out
    assert "{{" not in out


def test_a_body_without_placeholders_is_unchanged():
    out = binding.render_skill_bindings(_bundle("acme", "Plain guidance."))
    assert out.endswith("### adr_gate\nPlain guidance.")
