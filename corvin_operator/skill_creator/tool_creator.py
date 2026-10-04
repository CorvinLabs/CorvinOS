"""Tool Forge orchestrator — describe a tool, get a tested, registered Forge tool (ADR-2217 D3).

Same phase contract as the Skill-Creator (plan → validate with one repair →
test loop → adversarial review → promote), with one difference that matters:
a tool is executable, so its test loop RUNS the tool in the Forge sandbox
against engine-written test cases instead of asking an engine to grade it, and
promotion is fail-closed — a tool reaches the registry only when every test
case passed and no reviewer CONFIRMED a security finding.

What a passing test proves: the tool executes, honours its input schema and
returns the outputs the test cases state. The cases are engine-written, so it
does not prove semantic correctness; the operator sees the cases.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

from . import artifact_review as ar

logger = logging.getLogger(__name__)

TOOL_PHASES = ("planning", "validation", "sandbox_test", "review", "promotion")
#: The console mints as persona ``assistant`` (as Skill Forge does), so every
#: generated tool lives in that persona's namespace.
CALLER_PERSONA = "assistant"
NAMESPACE = "assistant."
K_MAX = 3
MAX_TEST_CASES = 6
MAX_IMPL_CHARS = 64 * 1024
_NAME_OK = re.compile(r"^[a-z0-9_.]{1,128}$")


class ToolCreatorError(Exception):
    """A tool run failed; ``str()`` is shown to the operator."""


@dataclass
class ToolDraft:
    name: str
    description: str
    input_schema: Dict[str, Any]
    impl: str
    test_cases: List[Dict[str, Any]] = field(default_factory=list)


_CONTRACT = """Tool contract (hard rules):
- Python 3, standard library only.
- Read ONE JSON object from stdin, print ONE JSON object to stdout, exit 0.
- Never import socket, subprocess, ctypes or multiprocessing; no network, no shell, no file writes.
- input_schema is a JSON Schema object ({"type": "object", "properties": {...}, "required": [...]}).
- test_cases: 2 to 5 entries {"input": {...}, "expect": {...}}; every input must satisfy input_schema,
  and "expect" lists keys whose values must appear exactly like that in the printed JSON object."""

_PLAN_PROMPT = """Design a small, single-purpose tool for this request:

{request}

{contract}

Reply with ONE JSON object and nothing else:
{{"name": "assistant.<snake_case_name>", "description": "<one sentence>",
  "input_schema": {{...}}, "impl": "<full python source>", "test_cases": [...]}}"""

_REPAIR_PROMPT = """This tool draft violates its contract:
{problems}

Draft:
{draft}

{contract}

Reply with ONE corrected JSON object with the same keys and nothing else."""

_FIX_PROMPT = """The tool below fails its own test cases when run in the sandbox.

Draft:
{draft}

Failures:
{failures}

{contract}

Fix the implementation (keep the name, input_schema and test_cases unless a test case itself
contradicts the purpose). Reply with ONE JSON object with the same keys and nothing else."""


def normalize_tool_name(raw: Any) -> str:
    """``assistant.<snake>`` from whatever the engine proposed."""
    text = str(raw or "").strip().lower()
    if text.startswith(NAMESPACE):
        text = text[len(NAMESPACE):]
    text = re.sub(r"[^a-z0-9_]+", "_", text).strip("_") or "tool"
    return (NAMESPACE + text)[:128]


def _draft_from(data: Dict[str, Any]) -> ToolDraft:
    cases = data.get("test_cases")
    return ToolDraft(
        name=normalize_tool_name(data.get("name")),
        description=" ".join(str(data.get("description") or "").split())[:300],
        input_schema=data.get("input_schema") if isinstance(data.get("input_schema"), dict) else {},
        impl=str(data.get("impl") or ""),
        test_cases=[c for c in cases if isinstance(c, dict)][:MAX_TEST_CASES] if isinstance(cases, list) else [],
    )


def _draft_json(draft: ToolDraft) -> str:
    return json.dumps({
        "name": draft.name, "description": draft.description,
        "input_schema": draft.input_schema, "impl": draft.impl,
        "test_cases": draft.test_cases,
    }, indent=2)


def _subset_mismatch(expect: Any, data: Any) -> Optional[str]:
    if not isinstance(expect, dict) or not expect:
        return None
    if not isinstance(data, dict):
        return f"output is {type(data).__name__}, expected an object"
    for key, value in expect.items():
        if key not in data:
            return f"missing key {key!r}"
        if data[key] != value:
            return f"{key!r}: expected {value!r}, got {data[key]!r}"
    return None


class ToolCreatorOrchestrator:
    """Five-phase tool generation for one tenant."""

    def __init__(self, *, tenant_id: str,
                 progress_cb: Optional[Callable[[str, int, str], None]] = None,
                 client: Any = None):
        self.tenant_id = tenant_id
        self.progress_cb = progress_cb
        self.client = client if client is not None else ar.resolve_engine()
        self.engine_id = ar.engine_id(self.client)

    # -- plumbing --------------------------------------------------------

    def _progress(self, phase: str, pct: int, message: str) -> None:
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(phase, pct, message)
        except Exception as exc:  # noqa: BLE001 — a broken callback must never fail a run
            logger.warning("progress callback failed: %s", exc)

    def _multi(self):
        from forge.multi_registry import MultiRegistry  # noqa: PLC0415
        return MultiRegistry(tenant_id=self.tenant_id)

    def _policy(self):
        from forge.policy import Policy  # noqa: PLC0415
        return Policy.load(self._multi()._registry("user").root)

    # -- phase 2: deterministic contract check ---------------------------

    def collect_violations(self, draft: ToolDraft) -> List[str]:
        from forge.runner import SchemaError, _validate  # noqa: PLC0415
        from forge.static_check import check_imports  # noqa: PLC0415

        problems: List[str] = []
        if not _NAME_OK.match(draft.name) or ".." in draft.name or draft.name.endswith("."):
            problems.append(f"name {draft.name!r} must be lowercase alphanumerics, '_' and '.'")
        policy = self._policy()
        ok, reason = policy.name_allowed(draft.name)
        if not ok:
            problems.append(f"name refused by forge policy ({reason})")
        ok, reason = policy.namespace_check(CALLER_PERSONA, draft.name)
        if not ok:
            problems.append(reason)
        if len(draft.description) < 10:
            problems.append("description must be one real sentence")
        if not draft.impl.strip():
            problems.append("impl is empty")
        elif len(draft.impl) > MAX_IMPL_CHARS:
            problems.append(f"impl exceeds {MAX_IMPL_CHARS} characters")
        else:
            bad = check_imports(draft.impl, forbidden=policy.forbidden_imports)
            if bad:
                problems.append(f"forbidden or unparseable imports: {', '.join(bad)}")
        schema = draft.input_schema
        if schema.get("type") != "object" or not isinstance(schema.get("properties", {}), dict):
            problems.append('input_schema must be a JSON Schema object with "type": "object"')
        if not 1 <= len(draft.test_cases) <= MAX_TEST_CASES:
            problems.append(f"test_cases must hold 1 to {MAX_TEST_CASES} cases")
        for i, case in enumerate(draft.test_cases):
            payload = case.get("input")
            if not isinstance(payload, dict):
                problems.append(f"test_cases[{i}].input must be an object")
                continue
            try:
                _validate(payload, schema)
            except SchemaError as exc:
                problems.append(f"test_cases[{i}].input violates input_schema: {exc}")
        return problems

    # -- phase 3: real sandbox execution ---------------------------------

    def run_test_cases(self, draft: ToolDraft) -> Dict[str, Any]:
        """Register the draft in a throw-away registry and run every case in the sandbox.

        The staging registry lives in a temp dir outside ``CORVIN_HOME``: it is
        a test bench, not the tenant's tool store, so the draft never reaches
        the tenant registry. Each sandbox execution is still audited as
        ``forge.tool_executed`` by the runner, like any tool run — generated
        code did run on this host. ``registry.create`` still runs its
        ``forge.create`` licence gate — the route already required it.
        """
        from forge.registry import Registry  # noqa: PLC0415
        from forge.runner import run_tool  # noqa: PLC0415

        results: List[Dict[str, Any]] = []
        with tempfile.TemporaryDirectory(prefix="tool-forge-bench-") as bench:
            registry = Registry(Path(bench))
            registry.create(name=draft.name, description=draft.description,
                            input_schema=draft.input_schema, impl=draft.impl)
            for i, case in enumerate(draft.test_cases):
                entry: Dict[str, Any] = {"index": i, "input": case.get("input"),
                                         "expect": case.get("expect", {})}
                try:
                    res = run_tool(registry, draft.name, case.get("input") or {},
                                   permission_mode="yes", policy=self._policy())
                    entry["sandbox"] = res.sandbox
                    entry["output"] = res.data
                    if not res.ok:
                        entry["error"] = (res.stderr or "tool returned ok=false")[-400:]
                    else:
                        mismatch = _subset_mismatch(case.get("expect"), res.data)
                        if mismatch:
                            entry["error"] = mismatch
                except Exception as exc:  # noqa: BLE001 — a crashing case is a failing case
                    entry["error"] = f"{type(exc).__name__}: {str(exc)[-400:]}"
                entry["passed"] = "error" not in entry
                results.append(entry)
        return {"cases": results, "passed": sum(1 for r in results if r["passed"]),
                "total": len(results)}

    # -- the run ----------------------------------------------------------

    async def create_tool(self, user_request: str) -> Dict[str, Any]:
        if self.client is None:
            raise ToolCreatorError(
                "No engine available — Tool Forge writes code with the engine and never "
                "fabricates it. Log in with `claude` or set ANTHROPIC_API_KEY.")

        self._progress("planning", 10, f"Designing the tool via {self.engine_id}…")
        draft = _draft_from(await asyncio.to_thread(
            ar.ask_json, self.client,
            _PLAN_PROMPT.format(request=user_request, contract=_CONTRACT)))

        self._progress("validation", 30, f"Checking '{draft.name}' against the forge contract…")
        problems = self.collect_violations(draft)
        if problems:
            self._progress("validation", 35, f"Repairing ({len(problems)} issue(s))…")
            draft = _draft_from(await asyncio.to_thread(
                ar.ask_json, self.client,
                _REPAIR_PROMPT.format(problems="\n".join(f"- {p}" for p in problems),
                                      draft=_draft_json(draft), contract=_CONTRACT)))
            problems = self.collect_violations(draft)
            if problems:
                raise ToolCreatorError("Tool draft violates the forge contract: " + "; ".join(problems))

        tests: Dict[str, Any] = {}
        converged = False
        for k in range(1, K_MAX + 1):
            self._progress("sandbox_test", 45 + 8 * (k - 1),
                           f"Running {len(draft.test_cases)} test case(s) in the sandbox (k={k})…")
            tests = await asyncio.to_thread(self.run_test_cases, draft)
            if tests["passed"] == tests["total"]:
                converged = True
                break
            if k == K_MAX:
                break
            failures = [c for c in tests["cases"] if not c["passed"]]
            fixed = _draft_from(await asyncio.to_thread(
                ar.ask_json, self.client,
                _FIX_PROMPT.format(draft=_draft_json(draft),
                                   failures=json.dumps(failures, indent=2, default=str)[:8000],
                                   contract=_CONTRACT)))
            fixed.name = draft.name  # a fix never renames the tool under test
            if self.collect_violations(fixed):
                break  # a "fix" that breaks the contract is not a fix
            draft = fixed
        iterations = k

        self._progress("review", 75, "Adversarial review (correctness, security, scope)…")
        findings = await ar.review(
            self.client, kind="tool", name=draft.name, purpose=draft.description,
            artifact=_draft_json(draft))
        score = ar.quality(findings, converged=converged, dimensions=len(ar.DIMENSIONS))
        summary = {
            "name": draft.name,
            "description": draft.description,
            "input_schema": draft.input_schema,
            "tests": tests,
            "iterations": iterations,
            "quality": score,
            "findings": ar.findings_out(findings),
            "sandbox": sorted({c.get("sandbox", "") for c in tests.get("cases", []) if c.get("sandbox")}),
        }

        if not converged:
            raise ToolCreatorError(
                f"Not registered: {tests.get('total', 0) - tests.get('passed', 0)} of "
                f"{tests.get('total', 0)} sandbox test case(s) still fail after {iterations} iteration(s).")
        security = [f for f in findings
                    if f.dimension == "security" and f.verdict.value == "confirmed"]
        if security:
            raise ToolCreatorError(
                "Not registered: the security reviewer confirmed a finding — " + security[0].summary)

        self._progress("promotion", 90, f"Registering '{draft.name}'…")
        spec = self._multi().create(
            scope="user", name=draft.name, description=draft.description,
            input_schema=draft.input_schema, impl=draft.impl,
            meta={"generated_by": "tool-forge", "engine": self.engine_id,
                  "test_cases": draft.test_cases},
        )
        summary["registry_path"] = spec.impl_path
        summary["sha256"] = spec.sha256
        return summary
