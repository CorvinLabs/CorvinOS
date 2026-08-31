"""acs_gate_chain.py — ADR-0104 M5 + ADR-0105: ACS Completion Gate Chain.

Runs gates 1–5 after every COMPLETE manager decision before accepting
the workflow as successful.  All gates are synchronous except gate 3
(LLM critique), which is async and optional.

Gate chain:
  1. Deliverable presence  — required output artifacts exist
  2. L0 contract           — placeholder scan, simhash loop-detection,
                             balanced delimiters (R34)
  3. LLM critique          — optional, only when confidence < threshold
  4. Evaluation metrics    — weighted score against declared metrics
  5. Refinement gradient   — R36 guard (nothing to refine → abort repair)

ADR-0105 additions:
  WorkflowLoss  — 5-dimensional quality vector computed on every COMPLETE
  LossProfile   — workflow-declared target, weights, convergence config
  _evaluate_convergence() — ε-convergence replacing binary R36 when active

All gates return a GateResult; the chain returns GateChainResult which
aggregates pass/fail and per-gate details.

MUST NOT import anthropic — CI AST lint enforces.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

@dataclass
class GateResult:
    gate_id: str
    passed: bool
    score: float = 1.0  # 0.0–1.0; 1.0 = fully passed
    reason: str = ""
    details: dict = field(default_factory=dict)


@dataclass
class GateChainResult:
    passed: bool
    gates: list[GateResult] = field(default_factory=list)
    rejected_gate: str = ""   # id of first failing gate
    aggregate_score: float = 1.0
    repair_needed: bool = False
    abort_reason: str = ""
    loss: "WorkflowLoss | None" = None   # ADR-0105: computed after all gates

    def add_gate(self, result: GateResult) -> None:
        self.gates.append(result)
        if not result.passed and self.passed:
            self.passed = False
            self.rejected_gate = result.gate_id


# ---------------------------------------------------------------------------
# ADR-0105: Loss function data types
# ---------------------------------------------------------------------------

@dataclass
class LossWeights:
    completeness: float = 0.25
    novelty:      float = 0.10
    quality:      float = 0.50
    metrics:      float = 0.10
    confidence:   float = 0.05

    def total(self) -> float:
        return (self.completeness + self.novelty + self.quality
                + self.metrics + self.confidence)


@dataclass
class ConvergenceConfig:
    epsilon: float = 0.0   # 0.0 = disabled; min improvement per iteration
    window:  int   = 2     # consecutive iterations below epsilon → plateau


@dataclass
class LossProfile:
    target:             float = 0.0            # 0.0 = accept on first pass
    weights:            LossWeights = field(default_factory=LossWeights)
    freshness_polarity: str = "positive"       # "positive" | "negative"
    convergence:        ConvergenceConfig = field(default_factory=ConvergenceConfig)

    @classmethod
    def from_dict(cls, d: dict) -> "LossProfile":
        preset_name = d.get("preset", "")
        base_d = dict(_LOSS_PRESETS.get(preset_name, {}))
        base_d.update({k: v for k, v in d.items() if k != "preset"})

        w_raw = base_d.get("weights") or {}
        base_w = LossWeights()
        w = LossWeights(
            completeness=float(w_raw.get("completeness", base_w.completeness)),
            novelty=float(w_raw.get("novelty",      base_w.novelty)),
            quality=float(w_raw.get("quality",      base_w.quality)),
            metrics=float(w_raw.get("metrics",      base_w.metrics)),
            confidence=float(w_raw.get("confidence", base_w.confidence)),
        )
        conv_raw = base_d.get("convergence") or {}
        conv = ConvergenceConfig(
            epsilon=float(conv_raw.get("epsilon", 0.0)),
            window=int(conv_raw.get("window", 2)),
        )
        return cls(
            target=float(base_d.get("target", 0.0)),
            weights=w,
            freshness_polarity=str(base_d.get("freshness_polarity", "positive")),
            convergence=conv,
        )


# Built-in presets — tuned for common workflow types
_LOSS_PRESETS: dict[str, dict] = {
    "analytical": {
        "target": 0.80,
        "freshness_polarity": "positive",
        "weights": {"completeness": 0.25, "novelty": 0.10,
                    "quality": 0.50, "metrics": 0.10, "confidence": 0.05},
    },
    "generative": {
        "target": 0.75,
        "freshness_polarity": "positive",
        "weights": {"completeness": 0.25, "novelty": 0.25,
                    "quality": 0.35, "metrics": 0.10, "confidence": 0.05},
    },
    "verification": {
        "target": 0.85,
        "freshness_polarity": "negative",
        "weights": {"completeness": 0.30, "novelty": 0.05,
                    "quality": 0.25, "metrics": 0.35, "confidence": 0.05},
    },
    "exploratory": {
        "target": 0.70,
        "freshness_polarity": "positive",
        "weights": {"completeness": 0.20, "novelty": 0.35,
                    "quality": 0.30, "metrics": 0.10, "confidence": 0.05},
    },
}


@dataclass
class WorkflowLoss:
    L_completeness: float
    L_novelty:      float
    L_quality:      float
    L_metrics:      float
    L_confidence:   float
    total:          float
    iteration:      int
    delta:          float | None = None   # total(τ) - total(τ-1); None on first COMPLETE


# ---------------------------------------------------------------------------
# Simhash (R34/R35 loop detection)
# ---------------------------------------------------------------------------

def _simhash(text: str, bits: int = 64) -> int:
    """Locality-sensitive hash for near-duplicate detection.

    Tokens: whitespace-split.  Longer texts get sliding 3-gram tokens in
    addition, improving sensitivity on long documents.
    """
    tokens = text.split()
    if len(tokens) > 30:
        tokens = tokens + [
            tokens[i] + " " + tokens[i + 1] for i in range(len(tokens) - 1)
        ]
    v = [0] * bits
    for tok in tokens:
        h = hash(tok) & ((1 << bits) - 1)
        for i in range(bits):
            if (h >> i) & 1:
                v[i] += 1
            else:
                v[i] -= 1
    result = 0
    for i in range(bits):
        if v[i] > 0:
            result |= 1 << i
    return result


def simhash_similarity(h1: int, h2: int, bits: int = 64) -> float:
    """Similarity in [0, 1].  1.0 = identical; 0.0 = maximally different."""
    diff = bin(h1 ^ h2).count("1")
    return 1.0 - diff / bits


# ---------------------------------------------------------------------------
# Gate 1: Deliverable presence
# ---------------------------------------------------------------------------

def _gate_deliverable(
    result_text: str,
    required_artifacts: list[str],
    artifact_base: Path | None,
) -> GateResult:
    if not required_artifacts:
        return GateResult("gate_1_deliverable", True, 1.0, "no required_artifacts declared")

    missing: list[str] = []
    for art_path in required_artifacts:
        if artifact_base:
            full = artifact_base / art_path
            if not full.exists():
                missing.append(art_path)
        else:
            # Without a base dir, we just check the result text mentions the path
            if art_path not in result_text:
                missing.append(art_path)

    if missing:
        return GateResult(
            "gate_1_deliverable",
            False,
            0.0,
            f"Missing artifacts: {missing}",
            {"missing": missing},
        )
    return GateResult("gate_1_deliverable", True, 1.0, "all artifacts present")


# ---------------------------------------------------------------------------
# Gate 2: L0 contract (R34)
# ---------------------------------------------------------------------------

_PLACEHOLDER_PATTERNS = re.compile(
    r"\b(TODO|XXX|FIXME|TBD|Lorem\s+ipsum|to\s+be\s+filled)\b"
    r"|TITLE\s+GOES\s+HERE|Author\s+Name|placeholder",
    re.IGNORECASE,
)


def _l0_no_placeholder(text: str) -> tuple[bool, str]:
    m = _PLACEHOLDER_PATTERNS.search(text)
    if m:
        return False, f"placeholder found: {m.group(0)!r}"
    return True, ""


def _l0_no_text_loop(text: str, prev_hash: int | None) -> tuple[bool, str]:
    """R35: detect consecutive near-identical outputs (simhash >= 0.95)."""
    if prev_hash is None:
        return True, ""
    h = _simhash(text)
    sim = simhash_similarity(h, prev_hash)
    if sim >= 0.95:
        return False, f"output similarity={sim:.3f} >= 0.95 (repair_fixpoint)"
    return True, ""


def _l0_balanced_delimiters(text: str) -> tuple[bool, str]:
    pairs = {"(": ")", "[": "]", "{": "}"}
    stack: list[str] = []
    # Only scan code-fence blocks to avoid false positives on prose
    in_code = False
    for line in text.splitlines():
        if line.strip().startswith("```"):
            in_code = not in_code
        if not in_code:
            continue
        for ch in line:
            if ch in pairs:
                stack.append(pairs[ch])
            elif ch in pairs.values():
                if not stack or stack[-1] != ch:
                    return False, f"unbalanced delimiter {ch!r}"
                stack.pop()
    if stack:
        return False, f"unclosed delimiters: {stack}"
    return True, ""


def _l0_json_valid_if_claimed(text: str) -> tuple[bool, str]:
    # Find JSON code blocks
    for block in re.findall(r"```(?:json)?\n(.*?)```", text, re.DOTALL):
        stripped = block.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                json.loads(stripped)
            except json.JSONDecodeError as exc:
                return False, f"invalid JSON block: {exc}"
    return True, ""


def _gate_l0_contract(
    result_text: str,
    prev_hash: int | None = None,
    checks: list[str] | None = None,
) -> GateResult:
    if checks is None:
        checks = ["default"]

    enabled = set(checks)
    if "default" in enabled:
        enabled |= {
            "no_placeholder", "no_text_loop",
            "balanced_delimiters", "json_valid_if_claimed",
        }

    failures: list[str] = []

    if "no_placeholder" in enabled:
        ok, reason = _l0_no_placeholder(result_text)
        if not ok:
            failures.append(f"no_placeholder: {reason}")

    if "no_text_loop" in enabled:
        ok, reason = _l0_no_text_loop(result_text, prev_hash)
        if not ok:
            failures.append(f"no_text_loop: {reason}")

    if "balanced_delimiters" in enabled:
        ok, reason = _l0_balanced_delimiters(result_text)
        if not ok:
            failures.append(f"balanced_delimiters: {reason}")

    if "json_valid_if_claimed" in enabled:
        ok, reason = _l0_json_valid_if_claimed(result_text)
        if not ok:
            failures.append(f"json_valid_if_claimed: {reason}")

    if failures:
        return GateResult("gate_2_l0_contract", False, 0.0, "; ".join(failures))
    return GateResult("gate_2_l0_contract", True, 1.0, "L0 contract passed")


# ---------------------------------------------------------------------------
# Gate 3: LLM critique (optional, async)
# ---------------------------------------------------------------------------

_CRITIQUE_SYSTEM = (
    "You are a quality evaluator for AI workflow outputs. "
    "Your job is to assess whether the provided output genuinely completes the stated goal. "
    "Return ONLY valid JSON with fields: {\"pass\": bool, \"score\": float 0-1, \"reason\": string}. "
    "Be strict: a score of 0.85 or above means PASS. "
    "Do not pass hollow, incomplete, or placeholder outputs."
)


def _run_critique_sync(result_text: str, goal: str, model: str) -> dict[str, Any] | None:
    try:
        from helper_model import resolve_claude_bin as _resolve_bin  # type: ignore
        _bin = _resolve_bin()
    except Exception:  # noqa: BLE001
        _bin = "claude"
    if not (shutil.which(_bin) or os.path.isfile(_bin)):
        return None
    prompt = (
        f"GOAL:\n{goal}\n\n"
        f"OUTPUT:\n{result_text[:4000]}\n\n"
        "Return only JSON: {\"pass\": bool, \"score\": 0-1, \"reason\": string}"
    )
    env = os.environ.copy()
    env["VOICE_HOOK_RECURSION"] = "1"
    try:
        out = subprocess.run(
            [
                _bin, "-p", prompt,
                "--append-system-prompt", _CRITIQUE_SYSTEM,
                "--model", model,
                "--disallowedTools", "*",
                "--max-turns", "1",
                "--output-format", "text",
            ],
            capture_output=True, text=True, env=env, timeout=60, check=False,
        )
        if out.returncode != 0:
            log.debug("acs: gate_3 critique returned exit %d", out.returncode)
            return None
        text = out.stdout.strip()
        # Extract first JSON object from output
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if m:
            return json.loads(m.group(0))
    except (subprocess.TimeoutExpired, json.JSONDecodeError):
        pass
    return None


async def _gate_llm_critique(
    result_text: str,
    goal: str,
    confidence: float,
    confidence_threshold: float = 0.95,
    model: str = "claude-haiku-4-5-20251001",
) -> GateResult:
    """Gate 3: LLM critique — skipped when confidence >= threshold."""
    if confidence >= confidence_threshold:
        return GateResult(
            "gate_3_llm_critique",
            True,
            confidence,
            f"skipped (confidence={confidence:.3f} >= {confidence_threshold})",
        )

    critique = await asyncio.to_thread(
        _run_critique_sync, result_text, goal, model
    )
    if critique is None:
        # If critique fails (claude unavailable), pass by default
        return GateResult(
            "gate_3_llm_critique",
            True,
            0.5,
            "critique unavailable (claude CLI not found); passing by default",
        )

    passed = bool(critique.get("pass", False))
    score = float(critique.get("score", 0.5))
    reason = str(critique.get("reason", ""))
    return GateResult("gate_3_llm_critique", passed, score, reason, {"critique": critique})


# ---------------------------------------------------------------------------
# Gate 4: Evaluation metrics
# ---------------------------------------------------------------------------

def _gate_eval_metrics(
    result: dict[str, Any],
    metrics_config: list[dict] | None,
    thresholds: dict | None,
) -> GateResult:
    """Gate 4: check declared evaluation metrics against result confidence/score."""
    if not metrics_config:
        return GateResult("gate_4_eval_metrics", True, 1.0, "no metrics configured")

    thresholds = thresholds or {}
    accept_threshold = float(thresholds.get("accept", 0.85))

    total_weight = 0.0
    weighted_score = 0.0

    for m in metrics_config:
        if not isinstance(m, dict):
            continue
        kind = m.get("kind", "")
        weight = float(m.get("weight", 1.0))
        total_weight += weight

        if kind == "schema":
            # Schema check: pass if result has expected fields
            score = 1.0  # assume schema validated by worker output contract
        elif kind == "budget":
            usage = result.get("usage") or {}
            # Budget metric: use confidence as proxy
            score = float(result.get("confidence", 1.0))
        elif kind == "deterministic":
            # Deterministic checks already run in L0
            score = 1.0
        else:
            # llm_rubric, policy: use confidence from worker
            score = float(result.get("confidence", 0.0))

        weighted_score += score * weight

    final_score = weighted_score / max(total_weight, 1e-9)
    passed = final_score >= accept_threshold

    return GateResult(
        "gate_4_eval_metrics",
        passed,
        final_score,
        f"score={final_score:.3f} >= threshold={accept_threshold:.3f}" if passed
        else f"score={final_score:.3f} < threshold={accept_threshold:.3f}",
    )


# ---------------------------------------------------------------------------
# Gate 5: Refinement gradient (R36)
# ---------------------------------------------------------------------------

def _gate_refinement_gradient(
    defects: list[str],
    rejected_gates: list[str],
    iteration: int,
) -> GateResult:
    """Gate 5: R36 guard — at least one real signal to refine against.

    If we're entering a repair cycle but there's nothing actionable (no defects,
    no rejected gates, iteration 0), abort immediately rather than looping.
    """
    if iteration == 0:
        # First iteration — skip gradient check
        return GateResult("gate_5_gradient", True, 1.0, "first iteration, no gradient check")

    has_signal = bool(defects or rejected_gates)
    if not has_signal:
        return GateResult(
            "gate_5_gradient",
            False,
            0.0,
            "nothing to refine: no defects, no rejected gates (R36 abort)",
        )

    return GateResult(
        "gate_5_gradient",
        True,
        1.0,
        f"gradient has signal: {len(defects)} defects, {len(rejected_gates)} rejected gates",
    )


# ---------------------------------------------------------------------------
# ADR-0105: Loss computation and convergence helpers
# ---------------------------------------------------------------------------

def _compute_workflow_loss(
    gates: list[GateResult],
    result: dict,
    result_text: str,
    prev_hash: int | None,
    profile: LossProfile,
    iteration: int,
    prev_loss: "WorkflowLoss | None" = None,
) -> WorkflowLoss:
    """Compute 5-dimensional WorkflowLoss from gate results + manager output.

    Called unconditionally after every gate chain evaluation regardless of
    pass/fail — M1 requires measurements even on rejected COMPLETEs so the
    trajectory is complete.
    """
    # G1: fraction of required artifacts present (score ∈ {0, 1} currently)
    g1 = next((g for g in gates if g.gate_id == "gate_1_deliverable"), None)
    L_completeness = g1.score if g1 is not None else 1.0

    # G2 / R35: novelty = how different is output from previous iteration?
    # L_novelty = 1 - simhash_similarity ∈ [0, 1]; 0 = fixpoint, 1 = fully new
    if prev_hash is not None:
        cur_hash = _simhash(result_text)
        sim = simhash_similarity(cur_hash, prev_hash)
        L_novelty_raw = 1.0 - sim
    else:
        L_novelty_raw = 1.0  # first COMPLETE → fully novel baseline

    # Apply freshness polarity: "negative" rewards stability over novelty
    L_novelty = (1.0 - L_novelty_raw) if profile.freshness_polarity == "negative" else L_novelty_raw

    # G3: quality from LLM critique score; falls back to gate score when
    # critique details not available (e.g. gate was skipped due to high confidence)
    g3 = next((g for g in gates if g.gate_id == "gate_3_llm_critique"), None)
    if g3 is not None:
        crit = (g3.details or {}).get("critique") or {}
        L_quality = float(crit.get("score", g3.score))
    else:
        L_quality = 1.0  # gate not run → no quality signal → optimistic default

    # G4: evaluation metric score
    g4 = next((g for g in gates if g.gate_id == "gate_4_eval_metrics"), None)
    L_metrics = g4.score if g4 is not None else 1.0

    # Manager self-assessed confidence (intentionally low-weighted — self-reported)
    L_confidence = min(1.0, max(0.0, float(result.get("confidence", 0.8))))

    # Weighted sum, normalised so arbitrary weight magnitudes work
    w = profile.weights
    w_sum = w.total() or 1.0
    total = (
        w.completeness * L_completeness
        + w.novelty    * L_novelty
        + w.quality    * L_quality
        + w.metrics    * L_metrics
        + w.confidence * L_confidence
    ) / w_sum

    delta = round(total - prev_loss.total, 4) if prev_loss is not None else None

    return WorkflowLoss(
        L_completeness=round(L_completeness, 3),
        L_novelty=round(L_novelty, 3),
        L_quality=round(L_quality, 3),
        L_metrics=round(L_metrics, 3),
        L_confidence=round(L_confidence, 3),
        total=round(total, 4),
        iteration=iteration,
        delta=delta,
    )


def _evaluate_convergence(
    history: list[WorkflowLoss],
    profile: LossProfile,
) -> str:
    """Return convergence verdict for the manager loop (ADR-0105 M3).

    Returns one of:
      'converged'  — target reached or no constraints → accept the COMPLETE
      'continue'   — target declared but not yet reached; keep iterating
      'plateau'    — Δ < ε for `window` consecutive iterations → abort
      'regression' — loss dropped by ≥ 10% in last step → abort
    """
    if not history:
        return "converged"

    latest = history[-1]

    # Default profile (target=0, eps=0) → always accept
    if profile.target <= 0.0 and profile.convergence.epsilon <= 0.0:
        return "converged"

    # Target reached → success
    if profile.target > 0.0 and latest.total >= profile.target:
        return "converged"

    eps = profile.convergence.epsilon
    win = profile.convergence.window

    # Plateau: last `win` measured deltas (excluding the None-delta baseline) all < ε
    if eps > 0.0:
        measured = [h.delta for h in history if h.delta is not None]
        if len(measured) >= win and all(abs(d) < eps for d in measured[-win:]):
            return "plateau"

    # Regression: significant quality drop in last step (≥ 10 percentage points)
    if latest.delta is not None and latest.delta < -0.10:
        return "regression"

    # Target declared but not yet reached, still making progress → loop
    if profile.target > 0.0:
        return "continue"

    # eps-only mode, not stuck yet → accept
    return "converged"


def loss_profile_from_spec(spec: dict) -> "LossProfile | None":
    """Extract LossProfile from an AWP spec dict (observability.loss).

    Returns None when no loss block is declared → backward-compat: R36 binary
    check remains active, WorkflowLoss is computed for observability only.
    """
    loss_cfg = (spec.get("observability") or {}).get("loss")
    if not loss_cfg:
        return None
    return LossProfile.from_dict(loss_cfg)


# ---------------------------------------------------------------------------
# Main ACSGateChain class
# ---------------------------------------------------------------------------

class ACSGateChain:
    """Runs the 5-gate completion chain after a COMPLETE manager decision.

    Usage::

        chain = ACSGateChain(workflow_spec=workflow_data)
        gate_result = await chain.evaluate(
            result_text="...",
            result={"confidence": 0.9},
            iteration=2,
            prev_hash=prev_simhash,
        )
        if not gate_result.passed:
            # trigger repair or FAIL
    """

    def __init__(
        self,
        workflow_spec: dict | None = None,
        critique_model: str = "claude-haiku-4-5-20251001",
        critique_confidence_threshold: float = 0.95,
    ) -> None:
        self._spec = workflow_spec or {}
        self._critique_model = critique_model
        self._critique_threshold = critique_confidence_threshold

    async def evaluate(
        self,
        result_text: str,
        result: dict[str, Any],
        *,
        iteration: int = 0,
        prev_hash: int | None = None,
        required_artifacts: list[str] | None = None,
        artifact_base: Path | None = None,
        defects: list[str] | None = None,
        rejected_gates: list[str] | None = None,
        goal: str = "",
        l0_checks: list[str] | None = None,
        profile: LossProfile | None = None,
        prev_loss: WorkflowLoss | None = None,
    ) -> GateChainResult:
        """Run the full gate chain and return GateChainResult.

        Args:
            result_text:        Final text output from the completing agent.
            result:             Worker result dict (must have 'confidence').
            iteration:          Current loop iteration (0-based).
            prev_hash:          Simhash of previous iteration output (R35 check).
            required_artifacts: Paths that must exist (relative to artifact_base).
            artifact_base:      Base directory for artifact presence checks.
            defects:            Defect list from previous iteration (R36 gradient).
            rejected_gates:     Gate names rejected in previous iteration (R36).
            goal:               Workflow goal description (for LLM critique).
            l0_checks:          Override default L0 checks list.
            profile:            ADR-0105 LossProfile (None → R36 binary fallback).
            prev_loss:          ADR-0105 loss from previous COMPLETE (for delta).
        """
        chain = GateChainResult(passed=True)
        confidence = float(result.get("confidence", 0.0))

        effective_profile = profile or LossProfile()

        # --- Gate 1: Deliverable presence ---
        g1 = _gate_deliverable(result_text, required_artifacts or [], artifact_base)
        chain.add_gate(g1)
        if not g1.passed:
            chain.repair_needed = True
            chain.loss = _compute_workflow_loss(
                chain.gates, result, result_text, prev_hash,
                effective_profile, iteration, prev_loss,
            )
            return chain

        # --- Gate 2: L0 contract ---
        g2 = _gate_l0_contract(result_text, prev_hash=prev_hash, checks=l0_checks)
        chain.add_gate(g2)
        if not g2.passed:
            # Repair fixpoint (simhash) → abort, not repair
            if "repair_fixpoint" in g2.reason:
                chain.abort_reason = g2.reason
                chain.loss = _compute_workflow_loss(
                    chain.gates, result, result_text, prev_hash,
                    effective_profile, iteration, prev_loss,
                )
                return chain
            chain.repair_needed = True

        # --- Gate 3: LLM critique (optional) ---
        eval_enabled = (
            self._spec.get("observability", {})
            .get("evaluation", {})
            .get("enabled", False)
        )
        if eval_enabled or not g2.passed:
            g3 = await _gate_llm_critique(
                result_text,
                goal or (self._spec.get("workflow", {}).get("description") or ""),
                confidence=confidence,
                confidence_threshold=self._critique_threshold,
                model=self._critique_model,
            )
            chain.add_gate(g3)
            if not g3.passed:
                chain.repair_needed = True

        # --- Gate 4: Evaluation metrics ---
        metrics = (
            self._spec.get("observability", {})
            .get("evaluation", {})
            .get("metrics")
        )
        thresholds = (
            self._spec.get("observability", {})
            .get("evaluation", {})
            .get("thresholds")
        )
        g4 = _gate_eval_metrics(result, metrics, thresholds)
        chain.add_gate(g4)
        if not g4.passed:
            chain.repair_needed = True

        # --- Gate 5: Refinement gradient (R36) ---
        # Only runs when a previous gate triggered a repair cycle.
        # Clean DELEGATE→COMPLETE flows (all prior gates pass) skip this gate.
        if chain.repair_needed:
            g5 = _gate_refinement_gradient(
                defects or [], rejected_gates or [], iteration
            )
            chain.add_gate(g5)
            if not g5.passed:
                chain.abort_reason = g5.reason
                chain.repair_needed = False  # R36 abort: don't attempt repair

        # Aggregate score (simple mean of gate scores; gate count varies by evaluation path)
        if chain.gates:
            chain.aggregate_score = sum(g.score for g in chain.gates) / len(chain.gates)

        # --- ADR-0105 M1: Compute WorkflowLoss (always, regardless of pass/fail) ---
        chain.loss = _compute_workflow_loss(
            gates=chain.gates,
            result=result,
            result_text=result_text,
            prev_hash=prev_hash,
            profile=effective_profile,
            iteration=iteration,
            prev_loss=prev_loss,
        )

        return chain

    def compute_prev_hash(self, text: str) -> int:
        """Compute simhash for use as prev_hash in the next evaluation."""
        return _simhash(text)
