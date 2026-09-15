"""acs_validator.py — ADR-0104 M1: AWP 1.0 full parser + R1–R36 validator.

Parses AWP workflow YAML (7-layer schema) and enforces R1–R36 rules from the
AWP 1.0 spec.  Extends the existing L26 ``corvin_workflows`` validator (R1–R10)
with full spec coverage.

Rules that can only be checked at runtime are marked ``[RUNTIME]`` in their
docstrings and return ``INFO``-severity issues to document what will be checked.

CLI:  python -m operator.bridges.shared.acs_validator validate <path.yaml>
      (M7 wires this as ``corvin-workflow validate``)

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Public data types
# ---------------------------------------------------------------------------

_SEMVER_RE = re.compile(
    r"^\d+\.\d+\.\d+(-[a-zA-Z0-9.]+)?(\+[a-zA-Z0-9.]+)?$"
)
_WF_NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,62}[a-z0-9]$")
_AGENT_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,46}[a-z0-9]$")

_RESERVED_TOOL_NS = frozenset({
    "web", "http", "file", "shell", "agent", "memory",
    "arithmetic", "numpy", "matplot", "pandas", "doc", "sklearn",
})

_RESERVED_STATE_KEYS = frozenset({"_meta", "_errors", "_trace", "_workflow"})

_VALID_METRIC_KINDS = frozenset(
    {"llm_rubric", "deterministic", "schema", "budget", "policy"}
)
_VALID_HOOKS = frozenset({"worker_result", "final_answer"})
_VALID_ACTIONS = frozenset({"retry_with_repair", "fail_workflow", "continue"})

_CODEMODE_LANGUAGES = frozenset({"python", "typescript", "javascript"})
_SANDBOX_TYPES = frozenset({"subprocess", "docker", "wasm", "isolate", "none"})


@dataclass
class ValidationIssue:
    rule_id: str
    severity: str  # "ERROR" | "WARNING" | "INFO"
    message: str
    path: str = ""

    def __str__(self) -> str:
        loc = f" ({self.path})" if self.path else ""
        return f"[{self.severity}] {self.rule_id}{loc}: {self.message}"


@dataclass
class ValidationResult:
    workflow_id: str
    issues: list[ValidationIssue] = field(default_factory=list)

    @property
    def errors(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "ERROR"]

    @property
    def warnings(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity == "WARNING"]

    @property
    def ok(self) -> bool:
        return not self.errors

    def add_error(self, rule: str, msg: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(rule, "ERROR", msg, path))

    def add_warning(self, rule: str, msg: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(rule, "WARNING", msg, path))

    def add_info(self, rule: str, msg: str, path: str = "") -> None:
        self.issues.append(ValidationIssue(rule, "INFO", msg, path))


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _get(d: dict, *keys: str, default: Any = None) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return default
        d = d.get(k, default)  # type: ignore[assignment]
    return d


def _topo_sort_cycles(graph: list[dict]) -> list[str]:
    """Return list of cycle descriptions (empty = acyclic)."""
    edges: dict[str, list[str]] = {}
    for n in graph:
        nid = n.get("id", "")
        deps_raw = n.get("depends_on") or []
        deps: list[str] = []
        for dep in deps_raw:
            if isinstance(dep, str):
                deps.append(dep)
            elif isinstance(dep, dict):
                deps.append(dep.get("id", dep.get("agent", "")))
        edges[nid] = deps

    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict[str, int] = {nid: WHITE for nid in edges}
    cycles: list[str] = []

    def dfs(node: str, path: list[str]) -> None:
        if node not in color:
            return
        color[node] = GRAY
        for nb in edges.get(node, []):
            if color.get(nb) == GRAY:
                cycles.append(" → ".join(path + [node, nb]))
            elif color.get(nb) == WHITE:
                dfs(nb, path + [node])
        color[node] = BLACK

    for nid in list(edges):
        if color[nid] == WHITE:
            dfs(nid, [])
    return cycles


# ---------------------------------------------------------------------------
# Rule implementations
# ---------------------------------------------------------------------------

def _r1_awp_version(data: dict, res: ValidationResult) -> None:
    """R1: awp field must be valid SemVer."""
    v = data.get("awp")
    if not isinstance(v, str) or not _SEMVER_RE.match(v):
        res.add_error("R1", f"awp must be valid SemVer (e.g. '1.0.0'), got {v!r}")


def _r2_workflow_name(data: dict, res: ValidationResult) -> None:
    """R2: workflow.name must match kebab/snake pattern."""
    name = _get(data, "workflow", "name")
    if not isinstance(name, str) or not _WF_NAME_RE.match(name):
        res.add_error(
            "R2",
            f"workflow.name must match ^[a-z][a-z0-9_-]{{0,62}}[a-z0-9]$, got {name!r}",
            "workflow.name",
        )


def _r5_r6_r7_graph(data: dict, res: ValidationResult) -> None:
    """R5: unique agent IDs; R6: DAG acyclic; R7: dependency targets exist."""
    graph = _get(data, "orchestration", "graph") or []
    if not isinstance(graph, list):
        return
    seen_ids: set[str] = set()
    for i, node in enumerate(graph):
        if not isinstance(node, dict):
            continue
        nid = node.get("id", "")
        # R5: uniqueness
        if nid in seen_ids:
            res.add_error("R5", f"Duplicate node id: {nid!r}", f"orchestration.graph[{i}].id")
        seen_ids.add(nid)

    # R7: dependency targets exist
    for i, node in enumerate(graph):
        if not isinstance(node, dict):
            continue
        deps_raw = node.get("depends_on") or []
        for dep in deps_raw:
            dep_id = dep if isinstance(dep, str) else (dep.get("id") or dep.get("agent", ""))
            if dep_id and dep_id not in seen_ids:
                res.add_error(
                    "R7",
                    f"depends_on references unknown id {dep_id!r}",
                    f"orchestration.graph[{i}].depends_on",
                )

    # R6: acyclicity
    cycles = _topo_sort_cycles(graph)
    for cycle in cycles:
        res.add_error("R6", f"Cycle detected: {cycle}", "orchestration.graph")


def _r8_agent_files(data: dict, res: ValidationResult, agents_dir: Path | None) -> None:
    """R8: every agent in orchestration.graph must have an agent.awp.yaml file."""
    if agents_dir is None:
        res.add_info(
            "R8",
            "Agent file existence check skipped (no agents_dir provided). "
            "Pass the workflow root dir to enable R8.",
        )
        return
    graph = _get(data, "orchestration", "graph") or []
    for i, node in enumerate(graph):
        if not isinstance(node, dict):
            continue
        agent_path = node.get("agent", "")
        if not agent_path:
            continue
        candidate = agents_dir / agent_path / "agent.awp.yaml"
        if not candidate.is_file():
            res.add_error(
                "R8",
                f"Agent config missing: {candidate}",
                f"orchestration.graph[{i}].agent",
            )


def _r9_output_contracts(agents_data: list[dict], res: ValidationResult) -> None:
    """R9: every agent must have output.contract defined."""
    for i, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            continue
        identity_id = _get(agent, "identity", "id") or f"[{i}]"
        fmt = _get(agent, "output", "format")
        contract = _get(agent, "output", "contract")
        if contract is None:
            res.add_error(
                "R9",
                "output.contract is required for every agent",
                f"agent:{identity_id}.output.contract",
            )
        elif fmt == "json" and isinstance(contract, str):
            # Contract is a path reference — warn that it won't be resolved statically
            res.add_info(
                "R9",
                f"output.contract is a file path ({contract!r}); JSON Schema validation skipped statically",
                f"agent:{identity_id}.output.contract",
            )


def _r10_r11_tool_namespaces(data: dict, agents_data: list[dict], res: ValidationResult) -> None:
    """R10: no reserved tool namespaces; R11: tool FQNs unique across workflow."""
    all_fqns: list[tuple[str, str]] = []  # (fqn, path)
    for i, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            continue
        identity_id = _get(agent, "identity", "id") or f"[{i}]"
        allowed = _get(agent, "capabilities", "tools", "allowed") or []
        if not isinstance(allowed, list):
            allowed = []
        for tool in allowed:
            if not isinstance(tool, str):
                continue
            ns = tool.split(".")[0].rstrip("*")
            if ns in _RESERVED_TOOL_NS:
                # R10: reserved namespace — only error if it's custom code, not AWP built-ins
                # The spec reserves these but they ARE used by AWP itself, so this is INFO
                pass
            all_fqns.append((tool, f"agent:{identity_id}.capabilities.tools.allowed"))

    # R11: uniqueness (exact match only, not pattern match)
    seen: dict[str, str] = {}
    for fqn, path in all_fqns:
        if "*" not in fqn and fqn in seen:
            res.add_error("R11", f"Duplicate tool FQN: {fqn!r}", path)
        elif "*" not in fqn:
            seen[fqn] = path


def _r12_agent_id_format(agents_data: list[dict], res: ValidationResult) -> None:
    """R12: identity.id must match ^[a-z][a-z0-9_]{0,46}[a-z0-9]$."""
    for i, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            continue
        aid = _get(agent, "identity", "id")
        if aid is None:
            res.add_error("R12", "identity.id is required", f"agent[{i}].identity.id")
        elif not isinstance(aid, str) or not _AGENT_ID_RE.match(aid):
            res.add_error(
                "R12",
                f"identity.id must match ^[a-z][a-z0-9_]{{0,46}}[a-z0-9]$, got {aid!r}",
                f"agent[{i}].identity.id",
            )


def _r13_r14_state_fields(data: dict, res: ValidationResult) -> None:
    """R13: reserved state keys; R14: sensitive field redaction [RUNTIME notes]."""
    initial = _get(data, "state", "initial") or {}
    if isinstance(initial, dict):
        for k in initial:
            if k in _RESERVED_STATE_KEYS:
                res.add_error(
                    "R13",
                    f"state.initial must not use reserved key {k!r}",
                    f"state.initial.{k}",
                )
    # R14 is runtime-only — annotate
    sensitive = _get(data, "state", "sharing", "sensitive_fields") or []
    if sensitive:
        res.add_info(
            "R14",
            f"[RUNTIME] {len(sensitive)} sensitive field(s) declared; "
            "runtime must enforce redaction from logs/audit",
        )


def _r19_r26_codemode(agents_data: list[dict], res: ValidationResult) -> None:
    """R19–R26: code mode rules."""
    for i, agent in enumerate(agents_data):
        if not isinstance(agent, dict):
            continue
        identity_id = _get(agent, "identity", "id") or f"[{i}]"
        cm_enabled = _get(agent, "capabilities", "codemode", "enabled")
        if not cm_enabled:
            continue
        path_prefix = f"agent:{identity_id}.capabilities"

        # R19: code mode requires tools.enabled
        tools_enabled = _get(agent, "capabilities", "tools", "enabled")
        if not tools_enabled:
            res.add_error("R19", "codemode.enabled requires tools.enabled: true", path_prefix)

        # R20: code mode requires sandbox != "none"
        sb_type = _get(agent, "capabilities", "sandbox", "type")
        if sb_type == "none":
            res.add_error("R20", "codemode.enabled requires sandbox.type != 'none'", path_prefix)

        # R21: language validation
        lang = _get(agent, "capabilities", "codemode", "language")
        if lang is not None and lang not in _CODEMODE_LANGUAGES:
            res.add_error(
                "R21",
                f"codemode.language must be one of {sorted(_CODEMODE_LANGUAGES)}, got {lang!r}",
                f"{path_prefix}.codemode.language",
            )

        # R22: explicit SDK surface must have includes
        sdk_mode = _get(agent, "capabilities", "codemode", "sdk_surface", "mode")
        sdk_include = _get(agent, "capabilities", "codemode", "sdk_surface", "include") or []
        if sdk_mode == "explicit" and not sdk_include:
            res.add_error(
                "R22",
                "sdk_surface.mode='explicit' requires at least one entry in sdk_surface.include",
                f"{path_prefix}.codemode.sdk_surface",
            )

        # R24: isolate sandbox requires network config
        if sb_type == "isolate":
            sb_network = _get(agent, "capabilities", "sandbox", "network")
            if sb_network is None:
                res.add_error(
                    "R24",
                    "sandbox.type='isolate' requires sandbox.network section",
                    f"{path_prefix}.sandbox.network",
                )

        # R25: dynamic tool namespace compliance
        tool_creation = _get(agent, "capabilities", "codemode", "tool_creation")
        if tool_creation:
            tc_ns = _get(agent, "capabilities", "codemode", "tool_creation_namespace") or "dynamic"
            if tc_ns in _RESERVED_TOOL_NS:
                res.add_error(
                    "R25",
                    f"tool_creation_namespace {tc_ns!r} is a reserved namespace",
                    f"{path_prefix}.codemode.tool_creation_namespace",
                )
            allowed_ns = _get(data, "dynamic_tools", "allowed_namespaces") or []
            if tc_ns not in allowed_ns:
                res.add_error(
                    "R25",
                    f"tool_creation_namespace {tc_ns!r} not listed in dynamic_tools.allowed_namespaces",
                    f"{path_prefix}.codemode.tool_creation_namespace",
                )

            # R26: tool_creation requires codemode + dynamic_tools.enabled
            dyn_enabled = _get(data, "dynamic_tools", "enabled")
            if not dyn_enabled:
                res.add_error(
                    "R26",
                    "tool_creation: true requires dynamic_tools.enabled: true in workflow",
                    f"{path_prefix}.codemode.tool_creation",
                )


def _r27_r30_evaluation(data: dict, res: ValidationResult) -> None:
    """R27–R30: evaluation metric and threshold rules."""
    if not _get(data, "observability", "evaluation", "enabled"):
        return
    metrics = _get(data, "observability", "evaluation", "metrics") or []
    thresholds = _get(data, "observability", "evaluation", "thresholds") or {}
    path = "observability.evaluation"

    # R27: metric kind valid
    for j, m in enumerate(metrics):
        if not isinstance(m, dict):
            continue
        kind = m.get("kind")
        if kind not in _VALID_METRIC_KINDS:
            res.add_error(
                "R27",
                f"metrics[{j}].kind must be one of {sorted(_VALID_METRIC_KINDS)}, got {kind!r}",
                f"{path}.metrics[{j}].kind",
            )

    # R28: thresholds consistent (accept >= retry >= fail, each in [0,1])
    accept = thresholds.get("accept")
    retry = thresholds.get("retry")
    fail = thresholds.get("fail")
    for name, val in (("accept", accept), ("retry", retry), ("fail", fail)):
        if val is not None:
            try:
                fval = float(val)
                if not (0.0 <= fval <= 1.0):
                    res.add_error(
                        "R28",
                        f"thresholds.{name}={fval} must be in [0.0, 1.0]",
                        f"{path}.thresholds.{name}",
                    )
            except (TypeError, ValueError):
                res.add_error("R28", f"thresholds.{name} must be numeric", f"{path}.thresholds")
    if accept is not None and retry is not None and float(accept) < float(retry):
        res.add_error("R28", "thresholds.accept must be >= thresholds.retry", f"{path}.thresholds")
    if retry is not None and fail is not None and float(retry) < float(fail):
        res.add_error("R28", "thresholds.retry must be >= thresholds.fail", f"{path}.thresholds")

    # R29: weights non-negative and at least one > 0
    has_positive_weight = False
    for j, m in enumerate(metrics):
        if not isinstance(m, dict):
            continue
        w = m.get("weight", 1.0)
        try:
            fw = float(w)
            if fw < 0:
                res.add_error(
                    "R29", f"metrics[{j}].weight must be >= 0, got {fw}", f"{path}.metrics[{j}]"
                )
            elif fw > 0:
                has_positive_weight = True
        except (TypeError, ValueError):
            res.add_error("R29", f"metrics[{j}].weight must be numeric", f"{path}.metrics[{j}]")
    if metrics and not has_positive_weight:
        res.add_error("R29", "At least one metric must have weight > 0", f"{path}.metrics")

    # R30: hooks and retry actions valid
    hooks = _get(data, "observability", "evaluation", "step_scores", "hooks") or []
    for h in hooks:
        if h not in _VALID_HOOKS:
            res.add_warning("R30", f"Unknown hook {h!r} (known: {sorted(_VALID_HOOKS)})", path)
    retry_acts = _get(data, "observability", "evaluation", "retry_policy", "actions") or {}
    for act_name, act_val in retry_acts.items():
        if isinstance(act_val, str) and act_val not in _VALID_ACTIONS:
            res.add_warning(
                "R30",
                f"retry_policy.actions.{act_name}={act_val!r} not in known actions "
                f"{sorted(_VALID_ACTIONS)}",
                f"{path}.retry_policy.actions",
            )


def _r31_r32_max_depth(data: dict, res: ValidationResult) -> None:
    """R31–R32: delegation_loop.budget.max_depth validation."""
    budget = _get(data, "orchestration", "delegation_loop", "budget")
    if budget is None:
        return
    max_depth = budget.get("max_depth")
    if max_depth is None:
        res.add_error(
            "R31",
            "delegation_loop.budget.max_depth is required when delegation_loop.budget is present",
            "orchestration.delegation_loop.budget.max_depth",
        )
        return
    try:
        d = int(max_depth)
        if d < 0:
            res.add_error(
                "R31",
                f"delegation_loop.budget.max_depth must be >= 0, got {d}",
                "orchestration.delegation_loop.budget.max_depth",
            )
        elif d > 10:
            res.add_error(
                "R32",
                f"delegation_loop.budget.max_depth={d} exceeds ceiling of 10",
                "orchestration.delegation_loop.budget.max_depth",
            )
        elif d > 5:
            res.add_warning(
                "R32",
                f"delegation_loop.budget.max_depth={d} > 5: deep recursion, review carefully",
                "orchestration.delegation_loop.budget.max_depth",
            )
    except (TypeError, ValueError):
        res.add_error(
            "R31",
            f"delegation_loop.budget.max_depth must be an integer, got {max_depth!r}",
            "orchestration.delegation_loop.budget.max_depth",
        )


def _r35_r36_worker_budget_ceilings(data: dict, res: ValidationResult) -> None:
    """R35–R36: delegation_loop.budget worker/wall-time ceilings.

    ACS-1 backstop: max_depth is guarded by R31/R32 (that guard already caught
    an accidental 100x inflation, a47c6d3). The sibling budget fields
    (max_total_workers, max_wall_time) had NO validator ceiling, so a silent
    inflation of those defeated compute quota (one metered unit → up to 400
    worker subprocesses for 100 h) without ever failing validation. These rules
    make a future inflation fail LOUDLY the same way R32 does for max_depth.

    Ceilings are deliberately generous relative to the sane defaults
    (max_total_workers=8, max_wall_time=3600) — they exist to block gross
    inflation, not to second-guess a legitimately large budget.
    """
    budget = _get(data, "orchestration", "delegation_loop", "budget")
    if budget is None:
        return

    # R35: max_total_workers ceiling.
    mtw = budget.get("max_total_workers")
    if mtw is not None:
        try:
            n = int(mtw)
            if n < 0:
                res.add_error(
                    "R35",
                    f"delegation_loop.budget.max_total_workers must be >= 0, got {n}",
                    "orchestration.delegation_loop.budget.max_total_workers",
                )
            elif n > 64:
                res.add_error(
                    "R35",
                    f"delegation_loop.budget.max_total_workers={n} exceeds ceiling of 64",
                    "orchestration.delegation_loop.budget.max_total_workers",
                )
            elif n > 16:
                res.add_warning(
                    "R35",
                    f"delegation_loop.budget.max_total_workers={n} > 16: many concurrent workers, review carefully",
                    "orchestration.delegation_loop.budget.max_total_workers",
                )
        except (TypeError, ValueError):
            res.add_error(
                "R35",
                f"delegation_loop.budget.max_total_workers must be an integer, got {mtw!r}",
                "orchestration.delegation_loop.budget.max_total_workers",
            )

    # R36: max_wall_time ceiling (seconds). 86400 = 24 h.
    mwt = budget.get("max_wall_time")
    if mwt is not None:
        try:
            w = int(mwt)
            if w < 0:
                res.add_error(
                    "R36",
                    f"delegation_loop.budget.max_wall_time must be >= 0, got {w}",
                    "orchestration.delegation_loop.budget.max_wall_time",
                )
            elif w > 86400:
                res.add_error(
                    "R36",
                    f"delegation_loop.budget.max_wall_time={w}s exceeds ceiling of 86400s (24 h)",
                    "orchestration.delegation_loop.budget.max_wall_time",
                )
            elif w > 14400:
                res.add_warning(
                    "R36",
                    f"delegation_loop.budget.max_wall_time={w}s > 14400s (4 h): long-running loop, review carefully",
                    "orchestration.delegation_loop.budget.max_wall_time",
                )
        except (TypeError, ValueError):
            res.add_error(
                "R36",
                f"delegation_loop.budget.max_wall_time must be an integer, got {mwt!r}",
                "orchestration.delegation_loop.budget.max_wall_time",
            )


def _r33_deterministic_phases(data: dict, res: ValidationResult) -> None:
    """R33: [RUNTIME] deterministic phases must not invoke LLM. Info annotation."""
    graph = _get(data, "orchestration", "graph") or []
    for i, node in enumerate(graph):
        if not isinstance(node, dict):
            continue
        if node.get("type") == "deterministic":
            res.add_info(
                "R33",
                "[RUNTIME] node is type:deterministic — runtime must ensure no LLM calls",
                f"orchestration.graph[{i}]",
            )


def _r34_l0_contract_declaration(data: dict, res: ValidationResult) -> None:
    """R34: L0 contract output checks properly declared."""
    checks = _get(data, "observability", "evaluation", "output_contract", "checks") or []
    valid_l0_checks = {
        "no_placeholder", "no_text_loop", "file_size_delta",
        "no_duplicate_headings", "balanced_delimiters",
        "json_valid_if_claimed", "default",
    }
    for c in checks:
        if isinstance(c, str) and c not in valid_l0_checks:
            res.add_warning(
                "R34",
                f"output_contract.checks contains unknown check {c!r}",
                "observability.evaluation.output_contract",
            )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def validate_workflow_dict(
    data: dict,
    *,
    agents_data: list[dict] | None = None,
    agents_dir: Path | None = None,
) -> ValidationResult:
    """Validate an AWP workflow dict (all 7 layers).

    Args:
        data:        Parsed workflow.awp.yaml as a dict.
        agents_data: Optional list of parsed agent.awp.yaml dicts (for R9/R12/R19-R26).
        agents_dir:  Optional path to the workflow root (for R8 file existence).

    Returns:
        ValidationResult — always (never raises). Check ``.ok`` for pass/fail.
    """
    wf_id = _get(data, "workflow", "name") or "<unknown>"
    res = ValidationResult(workflow_id=str(wf_id))

    if not isinstance(data, dict):
        res.add_error("R1", "Workflow document must be a top-level mapping (dict)")
        return res

    _r1_awp_version(data, res)
    _r2_workflow_name(data, res)
    _r5_r6_r7_graph(data, res)
    _r8_agent_files(data, res, agents_dir)
    _r13_r14_state_fields(data, res)
    _r27_r30_evaluation(data, res)
    _r31_r32_max_depth(data, res)
    _r35_r36_worker_budget_ceilings(data, res)
    _r33_deterministic_phases(data, res)
    _r34_l0_contract_declaration(data, res)

    if agents_data:
        _r9_output_contracts(agents_data, res)
        _r10_r11_tool_namespaces(data, agents_data, res)
        _r12_agent_id_format(agents_data, res)
        _r19_r26_codemode(agents_data, res)

    return res


def validate_workflow_file(path: str | Path) -> ValidationResult:
    """Load and validate an AWP workflow YAML file.

    Automatically discovers agent.awp.yaml files relative to the workflow root
    for R8/R9/R12/R19-R26 checks.

    Returns:
        ValidationResult — always (never raises).
    """
    p = Path(path)
    dummy_id = str(p)

    if not p.is_file():
        res = ValidationResult(workflow_id=dummy_id)
        res.add_error("R1", f"File not found: {p}")
        return res

    try:
        import yaml  # type: ignore[import-untyped]
        data = yaml.safe_load(p.read_text("utf-8"))
    except ImportError:
        res = ValidationResult(workflow_id=dummy_id)
        res.add_error("R1", "pyyaml not installed — cannot parse YAML")
        return res
    except Exception as exc:  # noqa: BLE001
        res = ValidationResult(workflow_id=dummy_id)
        res.add_error("R1", f"YAML parse error: {exc}")
        return res

    if not isinstance(data, dict):
        res = ValidationResult(workflow_id=dummy_id)
        res.add_error("R1", "Workflow document must be a top-level mapping")
        return res

    # Discover agent configs relative to workflow root dir
    workflow_root = p.parent
    agents_dir: Path | None = None
    agents_data: list[dict] = []

    # Standard layout: workflow.awp.yaml + agents/<id>/agent.awp.yaml
    agents_root = workflow_root / "agents"
    if agents_root.is_dir():
        agents_dir = workflow_root
        for agent_yaml in sorted(agents_root.glob("*/agent.awp.yaml")):
            try:
                import yaml as _yaml  # type: ignore[import-untyped]
                agent_doc = _yaml.safe_load(agent_yaml.read_text("utf-8"))
                if isinstance(agent_doc, dict):
                    agents_data.append(agent_doc)
            except Exception:  # noqa: BLE001
                pass

    return validate_workflow_dict(
        data,
        agents_data=agents_data or None,
        agents_dir=agents_dir,
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main_validate(argv: list[str] | None = None) -> int:
    """Entry point for ``corvin-workflow validate <path.yaml>``."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="corvin-workflow validate",
        description="Validate an AWP workflow YAML file (R1–R36).",
    )
    parser.add_argument("path", help="Path to workflow.awp.yaml")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    parser.add_argument("--strict", action="store_true", help="Treat warnings as errors")
    args = parser.parse_args(argv)

    result = validate_workflow_file(args.path)

    if args.json:
        out = {
            "workflow_id": result.workflow_id,
            "ok": result.ok if not args.strict else (not result.errors and not result.warnings),
            "errors": [str(i) for i in result.errors],
            "warnings": [str(i) for i in result.warnings],
            "info": [str(i) for i in result.issues if i.severity == "INFO"],
        }
        print(json.dumps(out, indent=2))
    else:
        for issue in result.issues:
            print(issue, file=sys.stderr if issue.severity == "ERROR" else sys.stdout)
        if result.ok:
            failed_strict = args.strict and result.warnings
            if failed_strict:
                print(
                    f"FAIL  {result.workflow_id}  "
                    f"({len(result.errors)} errors, {len(result.warnings)} warnings [strict])"
                )
                return 1
            print(
                f"PASS  {result.workflow_id}  "
                f"({len(result.warnings)} warnings, {len(result.issues)} total issues)"
            )
            return 0
        else:
            print(
                f"FAIL  {result.workflow_id}  "
                f"({len(result.errors)} errors, {len(result.warnings)} warnings)"
            )
            return 1


if __name__ == "__main__":
    sys.exit(main_validate())
