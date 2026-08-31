"""ato_classify.py — Importable ATO dispatch hint generator (ADR-0165 M5/M6/M7).

Mirrors the heuristics in code.task_intake.py so the adapter can call this
inline (no subprocess, no MCP round-trip) before engine spawn, exactly like
acs_classify.py is used for ACS-X.

MUST NOT import anthropic (CI AST lint enforces).
MUST NOT make any network call.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any

# ── Constants ────────────────────────────────────────────────────────────────

_LOOP_SIGNALS = re.compile(
    r"\b(fix|bug|fehler\w*|broken|fail|failing|test|e2e|error|debug|iterat\w*|"
    r"reparier\w*|korrigier\w*|round\s+\d|runde|review|audit)\b", re.I)

_WORKFLOW_SIGNALS = re.compile(
    r"\b(all\s+files?|codebase|sweep|migrat\w*|research|find\s+all|"
    r"survey|analys[ie]\w*|parallel|multi[- ]agent|workflow|fan[- ]out)\b", re.I)

_GOAL_SIGNALS = re.compile(
    r"\b(adr|entscheid\w*|decide|design|plan|architecture|trade[- ]?off|"
    r"strateg\w*|approach|bewerte\w*|compare|recommend)\b", re.I)

_AUTO_SIGNALS = re.compile(
    r"\b(schedule|monitor|watch|cron|background|recurring|periodic|alert|"
    r"überwach\w*|beobacht\w*)\b", re.I)

_COMPUTE_SIGNALS = re.compile(
    r"\b(optimize|optimier\w*|grid\s+search|bayesian|parameter\s+sweep|"
    r"simulation|simulier\w*|numeric|berechn\w*|calculate|statistik|"
    r"mittelwert|median|varianz|mean|regression|clustering|ml\s+model|"
    r"machine\s+learning|traini\w*|plot|histogram|scatter|chart|graph|"
    r"csv|xlsx?|dataframe|datensatz|batch\s+(transform|process)|"
    r"data\s+pipeline|large\s+dataset)\b", re.I)

# Question-word pattern: knowledge questions should not be scored as loop tasks
# even when "fix" / "error" appear in them.
# Match: "What is the fix for...", "How do you fix...", "Was ist...", etc.
_QUESTION_START = re.compile(
    r"^\s*(what|how|why|where|which|who|when|what'?s|was|wie|warum|welch|"
    r"wer|wann|wo|can\s+you\s+tell|explain|define|what\s+is)\b",
    re.I,
)

# Data-classification values that force local-only delegation.
_LOCAL_ONLY_CLASSES = frozenset({"CONFIDENTIAL", "SECRET"})


# ── Result dataclass ─────────────────────────────────────────────────────────

@dataclass
class ATOPlan:
    task_type: str
    confidence: float
    execution_strategy: str
    delegation_target: str | None          # M5
    recommended_model: str | None          # M6: "haiku" | "sonnet" | None
    compute_params: dict[str, Any] | None  # M7
    loop_params: dict[str, Any] = field(default_factory=dict)
    required_ldd_skills: list[str] = field(default_factory=list)


# ── Heuristic classifier ─────────────────────────────────────────────────────

def classify(
    prompt: str,
    *,
    data_classification: str = "CONFIDENTIAL",
    engine_id: str = "claude_code",
    haiku_allowed: bool | None = None,
) -> ATOPlan:
    """Classify *prompt* and return an ATOPlan with M5/M6/M7 hints.

    Args:
        prompt: Raw task text (≤2000 chars used).
        data_classification: L34 classification string ("INTERNAL", "CONFIDENTIAL", etc.).
        engine_id: Current OS engine — affects which dispatch options are available.
        haiku_allowed: Override for CORVIN_OS_MODEL_ALLOW_HAIKU env var.
                       None → read from environment.
    """
    if not prompt:
        return ATOPlan(
            task_type="one_shot",
            confidence=0.0,
            execution_strategy="default",
            delegation_target=None,
            recommended_model=None,
            compute_params=None,
        )
    task = prompt[:2000]
    dc = data_classification.upper()

    if haiku_allowed is None:
        haiku_allowed = os.environ.get("CORVIN_OS_MODEL_ALLOW_HAIKU", "") == "1"

    # ── Question-word suppression (loop + goal only) ─────────────────────────
    # Knowledge questions (starting with Wh-/W- + "?") suppress LOOP and GOAL
    # signals only, so "What is the fix for X?" → one_shot, not iterative_fix.
    # workflow_score is NOT penalised: action questions like "Which files need
    # migration?" are genuine multi_agent tasks phrased as questions.
    _is_question = bool(_QUESTION_START.match(task) and "?" in task)
    _q_penalty   = 0.35 if _is_question else 1.0

    # ── Score each task type ────────────────────────────────────────────────
    # workflow_score: NOT penalised — action questions like "Which files need
    # migration?" are genuine multi_agent tasks. All other non-trivial task
    # types are penalised: a question about a concept or operation is a
    # lookup, not a task to execute.
    loop_score     = min(1.0, len(_LOOP_SIGNALS.findall(task))     * 0.35) * _q_penalty
    workflow_score = min(1.0, len(_WORKFLOW_SIGNALS.findall(task)) * 0.40)  # no penalty
    goal_score     = min(1.0, len(_GOAL_SIGNALS.findall(task))     * 0.40) * _q_penalty
    auto_score     = min(1.0, len(_AUTO_SIGNALS.findall(task))     * 0.50) * _q_penalty
    compute_score  = min(1.0, len(_COMPUTE_SIGNALS.findall(task))  * 0.45) * _q_penalty

    scores: dict[str, float] = {
        "iterative_fix": loop_score,
        "multi_agent":   workflow_score,
        "exploration":   goal_score,
        "autonomous":    auto_score,
        "compute":       compute_score,
        "one_shot":      0.30,
    }

    best_type  = max(scores, key=lambda k: scores[k])
    confidence = round(scores[best_type], 3)

    # ── M5: delegation_target ────────────────────────────────────────────────
    # Only trigger from the CC OS turn — workers don't re-delegate.
    delegation_target: str | None = None
    if engine_id == "claude_code":
        if dc in _LOCAL_ONLY_CLASSES:
            delegation_target = "delegate_hermes"
        elif best_type == "one_shot" and len(task) < 1500:
            delegation_target = "delegate_copilot"

    # ── M6: recommended_model ────────────────────────────────────────────────
    recommended_model: str | None = None
    if haiku_allowed and best_type == "one_shot" and engine_id == "claude_code":
        recommended_model = "haiku"

    # ── M7: compute_params ───────────────────────────────────────────────────
    compute_params: dict[str, Any] | None = None
    if best_type == "compute":
        compute_params = {"strategy": "bayesian", "datasources": []}

    # ── Execution strategy ───────────────────────────────────────────────────
    _strategies = {
        "iterative_fix": "goal + loop",
        "multi_agent":   "workflow",
        "exploration":   "goal + dialectical-reasoning",
        "autonomous":    "schedule + session-isolation",
        "compute":       "compute_worker",
        "one_shot":      "direct",
    }
    _loop_params: dict[str, dict[str, Any]] = {
        "iterative_fix": {"k_max": 5, "convergence": "test passing + no regressions",
                          "loss_signal_command": "bash operator/bridges/run-all-tests.sh"},
        "multi_agent":   {"k_max": 3, "convergence": "dry-streak 2 rounds without new CRITICAL/HIGH",
                          "severity_gate": "CRITICAL + HIGH block convergence; MED/LOW to backlog"},
        "exploration":   {"k_max": 1, "convergence": "thesis + antithesis + synthesis complete"},
        "autonomous":    {"k_max": None, "convergence": "operator stop-signal"},
        "compute":       {"k_max": 1, "convergence": "worker returns result"},
        "one_shot":      {"k_max": 1, "convergence": "single pass"},
    }
    _ldd_skills: dict[str, list[str]] = {
        "iterative_fix": ["reproducibility-first", "e2e-driven-iteration"],
        "multi_agent":   ["dialectical-reasoning", "docs-as-definition-of-done"],
        "exploration":   ["dialectical-reasoning"],
        "autonomous":    ["docs-as-definition-of-done"],
        "compute":       [],
        "one_shot":      [],
    }

    return ATOPlan(
        task_type=best_type,
        confidence=confidence,
        execution_strategy=_strategies[best_type],
        delegation_target=delegation_target,
        recommended_model=recommended_model,
        compute_params=compute_params,
        loop_params=_loop_params.get(best_type, {}),
        required_ldd_skills=_ldd_skills.get(best_type, []),
    )
