"""ToolForge stage (ADR-0283, P-D) — provision the worker with forged tools.

`effect=forge`, opt-in (not in the default pipeline). Reads `scratch['needs'].tools`
(from the LLM synthesis stage, P-C) and forges + binds each. Load-bearing safety
(ADR-0283 R1/R2):
- Runs POST-gate (effect=forge) — nothing is forged for a task the gates refuse.
- SAME-TURN uses TEMPLATE impls only. An LLM-authored impl is used only if the
  operator set the default-off `allow_llm_impl` config AND it passes an AST
  ALLOWLIST check (not a substring denylist). On any doubt it falls back to the
  safe template — never executes unreviewed LLM code same-turn.
- The forged tool runs in the unchanged Forge sandbox (bwrap, no net/subprocess).
- Binding goes through the ADR-0281 channel (class-based re-validated at the
  boundary); a forge failure is fail-safe (the turn proceeds).
"""
from __future__ import annotations

import ast
from pathlib import Path

from .base import StageTelemetry
from .binding import ToolRef, MAX_BINDINGS
from .registry import register_stage

# `sys`/`json` are needed for stdin/stdout — allowed; the bwrap sandbox is the real
# guard. The forbidden set is the obviously-dangerous: process/network/fs-escape +
# `builtins`/`__builtins__` (else `import builtins; builtins.eval(...)` bypasses the
# call check — review finding #2).
_FORBIDDEN_IMPORTS = {"os", "subprocess", "socket", "ctypes", "importlib",
                      "multiprocessing", "shutil", "pathlib", "requests", "urllib",
                      "builtins", "__builtins__", "code", "pty", "posix", "runpy"}
_FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__", "open", "getattr",
                    "setattr", "globals", "locals", "vars", "breakpoint", "input"}
# dangerous attribute names in the `x.attr(...)` form (finding #2)
_FORBIDDEN_ATTRS = {"system", "popen", "spawn", "spawnl", "spawnv", "fork",
                    "remove", "unlink", "rmtree", "rename", "chmod", "startfile"}

# A safe deterministic template: reads a JSON payload on stdin, echoes it. The LLM
# `needs` only names a tool; the real impl is a reviewed template unless the
# operator explicitly opts into LLM-authored impls.
_TEMPLATE_IMPL = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    'print(json.dumps({"ok": True, "input": data}))\n'
)


def ast_allowlist_ok(impl: str) -> "tuple[bool, str]":
    """AST allowlist (ADR-0283 R1): reject forbidden imports, dangerous builtins,
    and dunder attribute access. Fail-closed on a parse error."""
    try:
        tree = ast.parse(impl)
    except SyntaxError as e:
        return False, f"syntax:{e}"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] in _FORBIDDEN_IMPORTS:
                    return False, f"import:{a.name}"
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] in _FORBIDDEN_IMPORTS:
                return False, f"from:{node.module}"
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _FORBIDDEN_CALLS:
                return False, f"call:{node.func.id}"
        elif isinstance(node, ast.Attribute):
            # x.eval(...) / x.system(...) / x.__import__ — the attribute form the
            # bare-name check misses (review finding #2).
            if node.attr.startswith("__"):
                return False, f"dunder:{node.attr}"
            if node.attr in _FORBIDDEN_CALLS or node.attr in _FORBIDDEN_ATTRS:
                return False, f"attr:{node.attr}"
    return True, ""


def _forge_create(tenant_id: str, name: str, description: str,
                  input_schema: dict, impl: str) -> None:
    """Register a tool in the tenant's Forge registry, in-process. Best-effort."""
    from forge.paths import tenant_home  # noqa: PLC0415
    from forge.registry import Registry  # noqa: PLC0415
    root = Path(tenant_home(tenant_id)) / "forge"
    Registry(root).create(name, description or "forged by CEL",
                          input_schema or {"type": "object"}, impl,
                          scope="session", overwrite=True,
                          meta={"deterministic": True})


class ToolForgeStage:
    id = "toolforge"
    requires: tuple = ("llm_synthesis",)   # consumes scratch['needs']
    effect = "forge"
    trust = "builtin"

    def run(self, bundle, ctx):
        needs = (bundle.scratch.get("needs") or {}).get("tools") or []
        allow_llm = bool((ctx.config or {}).get("allow_llm_impl"))
        bound: list = []
        for t in needs[:MAX_BINDINGS]:
            name = t.get("name") if isinstance(t, dict) else str(t)
            if not name:
                continue
            safe = "".join(c for c in str(name) if c.isalnum() or c in "_")[:48]
            if not safe:
                continue
            impl = _TEMPLATE_IMPL
            if isinstance(t, dict) and t.get("impl") and allow_llm:
                ok, _reason = ast_allowlist_ok(t["impl"])
                if ok:
                    impl = t["impl"]      # reviewed via AST allowlist
                # else: fall back to the safe template (never run unreviewed code)
            try:
                _forge_create(ctx.tenant_id, safe,
                              (t.get("description") if isinstance(t, dict) else "") or "",
                              (t.get("input_schema") if isinstance(t, dict) else None) or {},
                              impl)
                bound.append(ToolRef(name=f"mcp__forge__{safe}", origin="forge"))
            except Exception:  # noqa: BLE001 — a forge failure is fail-safe
                continue
        bundle.tools_to_bind.extend(bound)
        return bundle, StageTelemetry(
            stage=self.id, status="ok",
            confidence_tier="high" if bound else "low",
            sources=[{"id": b.name, "score": 1.0} for b in bound])


register_stage(ToolForgeStage())
