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
# dangerous attribute names in the `x.attr(...)` form (finding #2). `modules` +
# `import_module` close the R2 bypass `sys.modules["os"].execv(...)` — `sys` is
# allowed for stdin/stdout, but `.modules` hands back EVERY loaded module (os,
# subprocess, importlib) with no import statement (review R2 finding #1).
_FORBIDDEN_ATTRS = {"system", "popen", "spawn", "spawnl", "spawnv", "fork",
                    "remove", "unlink", "rmtree", "rename", "chmod", "startfile",
                    "modules", "import_module", "environ", "execv", "execve",
                    "execl", "dup2", "write", "load_module"}

# A safe deterministic template: reads a JSON payload on stdin, echoes it. The LLM
# `needs` only names a tool; the real impl is a reviewed template unless the
# operator explicitly opts into LLM-authored impls.
# Built-ins the worker already has — forging a namesake would hand it a second,
# useless tool under a familiar name.
_BUILTIN_TOOL_NAMES = {
    "read", "write", "edit", "multiedit", "bash", "bashoutput", "killshell",
    "grep", "glob", "ls", "webfetch", "websearch", "task", "todowrite",
    "notebookedit", "slashcommand", "skill",
}

_TEMPLATE_IMPL = (
    "import json, sys\n"
    "data = json.load(sys.stdin)\n"
    'print(json.dumps({"ok": True, "input": data}))\n'
)


def ast_allowlist_ok(impl: str) -> "tuple[bool, str]":
    """AST PRE-FILTER (ADR-0283 R1/R2). NOT a sufficient guard for executing
    untrusted code: a denylist over Python's introspection surface is provably
    incomplete (R2 showed `sys.modules["os"].execv(...)` bypassing it). As of R2
    it is NO LONGER on the same-turn execution path — ToolForge always forges the
    deterministic TEMPLATE impl (see ToolForgeStage.run); this function survives
    only as a cheap first-pass screen for a FUTURE out-of-band human/second-model
    review of LLM-authored impls, never as the sole gate before execution. The
    bwrap sandbox (no net/subprocess) remains the real isolation. Fail-closed on a
    parse error."""
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
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            # A forbidden builtin referenced ANYWHERE in load context — not just as
            # a call target (review R2 finding: `_e = eval; _e(x)`, `map(exec, …)`,
            # `sorted(x, key=eval)`, `__builtins__['eval']`, `g = getattr` all put
            # the dangerous name in a non-Call.func position the old check missed).
            if node.id in _FORBIDDEN_CALLS or node.id == "__builtins__":
                return False, f"name:{node.id}"
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


def _tool_exists(tenant_id: str, name: str) -> bool:
    """Does the tenant's Forge registry already carry this tool? Errors read as
    "no" (the create path is best-effort; a probe failure must not skip forging)."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        from forge.registry import Registry  # noqa: PLC0415
        return Registry(Path(tenant_home(tenant_id)) / "forge").get(name) is not None
    except Exception:  # noqa: BLE001
        return False


def uncreate_tools(tenant_id: str, names) -> None:
    """Roll back forged tools that Gate-2 or bind re-validation rejected (ADR-0283
    R3 / review R2 finding A4). Best-effort — a failed delete never breaks the turn;
    the un-bound tool is at worst an orphaned session artifact, not a live grant."""
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        from forge.registry import Registry  # noqa: PLC0415
        reg = Registry(Path(tenant_home(tenant_id)) / "forge")
    except Exception:  # noqa: BLE001
        return
    for n in names or []:
        try:
            reg.delete(n)
        except Exception:  # noqa: BLE001
            continue


class ToolForgeStage:
    id = "toolforge"
    requires: tuple = ("llm_synthesis",)   # consumes scratch['needs']
    effect = "forge"
    trust = "builtin"

    def run(self, bundle, ctx):
        needs = (bundle.scratch.get("needs") or {}).get("tools") or []
        bound: list = []
        skipped_shape = skipped_builtin = 0
        for t in needs[:MAX_BINDINGS]:
            # A bare string is NOT a forgeable tool (ADR-0283 amendment, 2026-08-18).
            # Accepting one is how `cel_Pythoncsvmoduleoderpandas` and `cel_pydantic`
            # got written: the model answers a "which tools?" question with a tech
            # stack, and every entry became an echo-template tool bound to the worker.
            # A real request carries a name AND what the tool does; anything less is
            # counted and skipped, so the trace shows the refusal instead of hiding it.
            if not isinstance(t, dict) or not t.get("name"):
                skipped_shape += 1
                continue
            if not (t.get("description") or t.get("input_schema")):
                skipped_shape += 1
                continue
            name = t.get("name")
            # A built-in the worker already has must never be shadowed by an
            # echo-template namesake (`mcp__forge__cel_Read`).
            if str(name).strip().lower().replace("-", "_") in _BUILTIN_TOOL_NAMES:
                skipped_builtin += 1
                continue
            safe = "".join(c for c in str(name) if c.isalnum() or c in "_")[:44]
            if not safe:
                continue
            # Namespace CEL-forged tools so a task-derived name can never clobber a
            # manually-forged session tool via overwrite=True (review R3 finding A4).
            safe = "cel_" + safe
            # SAME-TURN forging ALWAYS uses the deterministic template (review R2
            # finding #1): an LLM-authored impl is never executed same-turn, because
            # the AST pre-filter is provably incomplete against Python introspection
            # (`sys.modules[...]`, subclass walks). The `allow_llm_impl` config is
            # retired to a no-op here; LLM impls belong to a future out-of-band
            # human-reviewed promotion path, not the hot turn path.
            impl = _TEMPLATE_IMPL
            # PRE-EXISTING tools are bound, never re-created and never rolled back
            # (found 2026-08-18 by an unmocked E2E): `_forge_create` writes with
            # overwrite=True and `uncreate_tools` deletes by NAME, so turn B could
            # delete the artifact turn A had legitimately forged and bound — turn A's
            # worker then holds a name with nothing behind it. The `cel_` namespace
            # only separates CEL tools from MANUALLY forged ones (review R3 A4); it
            # does not separate one CEL turn from the next, and LLM-proposed names
            # (`cel_Read`, `cel_Bash`) repeat across turns constantly. Re-creating is
            # unnecessary anyway: the impl is always the same deterministic template.
            pre_existing = _tool_exists(ctx.tenant_id, safe)
            try:
                if not pre_existing:
                    _forge_create(ctx.tenant_id, safe,
                                  (t.get("description") if isinstance(t, dict) else "") or "",
                                  (t.get("input_schema") if isinstance(t, dict) else None) or {},
                                  impl)
                bound.append(ToolRef(name=f"mcp__forge__{safe}", origin="forge"))
                # Track the on-disk artifact so the pipeline can ROLL IT BACK if
                # Gate-2 or bind re-validation rejects it (review R2 finding A4: a
                # forged tool is written pre-Gate-2; a denial must un-create it, or
                # a forge_enabled persona could invoke the denied tool by name).
                if not pre_existing:
                    bundle.scratch.setdefault("_forged_tools", []).append(
                        {"name": safe, "ref": f"mcp__forge__{safe}"})
            except Exception:  # noqa: BLE001 — a forge failure is fail-safe
                continue
        bundle.tools_to_bind.extend(bound)
        # A stage that forged NOTHING used to report a bare `status=ok`, which is how
        # 166 live turns rendered as "7/7 stages ok" in the console while this stage
        # produced not one artifact. The reason makes the empty run legible.
        reason = None
        if not bound:
            reason = ("no_forgeable_tool_needs" if (skipped_shape or skipped_builtin)
                      else "no_tool_needs")
        return bundle, StageTelemetry(
            stage=self.id, status="ok", reason=reason,
            confidence_tier="high" if bound else "low",
            sources=[{"id": b.name, "score": 1.0} for b in bound])


register_stage(ToolForgeStage())
