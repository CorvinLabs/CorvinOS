"""ACS-X — Autonomous Command Selector Extended (ADR-0155).

Classifies an incoming task into one of six execution primitives and
produces an ``ACSBlueprint`` that the adapter injects as an
``<acs_directive>`` block into the system prompt before the OS-turn spawn.

Two-stage classification:
  Stage 1 — Heuristic fast-path: keyword × weight matrix, < 1 ms, no API.
             Returns immediately when max(score) >= HEURISTIC_THRESHOLD.
  Stage 2 — LLM fallback: Haiku-4.5 subprocess (helper_model.py pattern).
             Fires only when Stage-1 confidence < HEURISTIC_THRESHOLD.

Fail-open contract: any exception in classify() returns
ACSBlueprint(primitive="DIRECT", confidence=0.5, path="error").
The adapter wraps the call in try/except and proceeds without the
directive on any failure.

MUST NOT import anthropic (CI AST lint enforces).
"""
from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Final

# ── Constants ────────────────────────────────────────────────────────────────

HEURISTIC_THRESHOLD: Final[float] = 0.70
LLM_TIMEOUT_S: Final[float] = 10.0

PRIMITIVE_GOAL:     Final[str] = "GOAL"
PRIMITIVE_LOOP:     Final[str] = "LOOP"
PRIMITIVE_WORKFLOW: Final[str] = "WORKFLOW"
PRIMITIVE_COMPUTE:  Final[str] = "COMPUTE"
PRIMITIVE_DELEGATE: Final[str] = "DELEGATE"
PRIMITIVE_DIRECT:   Final[str] = "DIRECT"

ALL_PRIMITIVES: Final[tuple[str, ...]] = (
    PRIMITIVE_GOAL, PRIMITIVE_LOOP, PRIMITIVE_WORKFLOW,
    PRIMITIVE_COMPUTE, PRIMITIVE_DELEGATE, PRIMITIVE_DIRECT,
)

# ── Signal table ─────────────────────────────────────────────────────────────
# Each entry: (compiled regex, target_primitive, weight)
# Weight ∈ (0, 1]. Additive per primitive; max(scores) vs HEURISTIC_THRESHOLD.

_RAW_SIGNALS: Final[list[tuple[str, str, float]]] = [
    # ── LOOP ────────────────────────────────────────────────────────────────
    # Temporal recurrence patterns
    # "every N min/hour/day" and German "jede X" are strong LOOP signals (0.90).
    # Span bounded to 30 non-sentence chars (was unbounded `.*`): "jede 5
    # Minuten" still matches, but "für JEDEN der fünf Anbieter die Lieferzeit
    # in MINUTEN" (a per-item fan-out, ~35 chars apart, crossing the whole
    # clause) no longer false-fires LOOP (review F2 — the unbounded span
    # spanned an entire sentence).
    (r"\bjede[rn]?\b[^.!?]{0,30}\b(minute[n]?|stunde[n]?|sekunde[n]?|tag[e]?)\b",
                                                                          PRIMITIVE_LOOP, 0.90),
    (r"\bevery\s+\d+\s*(minute|hour|second|day)s?\b",                     PRIMITIVE_LOOP, 0.90),
    # Explicit scheduling intent (0.80): "on a schedule", "periodically"
    (r"\b(periodisch|regelmäßig|periodically|on a schedule)\b",           PRIMITIVE_LOOP, 0.80),
    # Pure temporal adjectives (0.65): "stündlich/hourly/daily" are ambiguous
    # when combined with COMPUTE verbs — COMPUTE signals (0.75+) win in that case
    (r"\b(stündlich|minütlich)\b",                                        PRIMITIVE_LOOP, 0.65),
    (r"\b(hourly)\b",                                                     PRIMITIVE_LOOP, 0.65),
    # "täglich/daily" intentionally lowered: "compute daily stats" → COMPUTE
    (r"\b(täglich|daily)\b",                                              PRIMITIVE_LOOP, 0.60),
    # Monitoring / watching
    (r"\b(überwache|beobachte|watch|monitor|keep.{0,15}(watching|checking|eye))\b",
                                                                           PRIMITIVE_LOOP, 0.85),
    # Retry / convergence
    (r"\b(fix\s+bis\s+grün|retry\s+until|iterate\s+until\s+green|wiederhole\s+bis)\b",
                                                                           PRIMITIVE_LOOP, 0.85),
    (r"\b(schleife|loop\s+(over|through|until)|keep\s+(trying|running))\b",
                                                                           PRIMITIVE_LOOP, 0.80),

    # ── WORKFLOW ─────────────────────────────────────────────────────────────
    # Codebase-wide / all-subsystems scope
    (r"\b(audit\s+(all|alle|den\s+ganzen|die\s+ganze|complete)|vollständig\s+(prüf|review))\b",
                                                                           PRIMITIVE_WORKFLOW, 0.90),
    (r"\b(review\s+(the\s+)?(whole|entire|all|complete)\s+(codebase|repo|project|code))\b",
                                                                           PRIMITIVE_WORKFLOW, 0.90),
    (r"\b(alle\s+subsystem|alle\s+layer|every\s+(module|layer|subsystem|component))\b",
                                                                           PRIMITIVE_WORKFLOW, 0.85),
    (r"\b(comprehensive\s+(review|audit|scan|check))\b",                   PRIMITIVE_WORKFLOW, 0.85),
    (r"\b(parallel\s+(review|agent|scan|analyse))\b",                      PRIMITIVE_WORKFLOW, 0.80),
    (r"\b(multi.agent|multi-agent|mehrere\s+agenten)\b",                   PRIMITIVE_WORKFLOW, 0.80),
    (r"\b(security\s+audit|compliance\s+audit|iterative[rn]?\s+(code.)?review)\b",
                                                                           PRIMITIVE_WORKFLOW, 0.85),

    # ── GOAL ─────────────────────────────────────────────────────────────────
    (r"\b(langfristig(es|en|er)?\s+(ziel|aufgabe|projekt)|long.term\s+(goal|objective|task))\b",
                                                                           PRIMITIVE_GOAL, 0.90),
    (r"\b(merke\s+(dir|als\s+ziel)|remember\s+as\s+goal|set\s+as\s+(ongoing\s+)?goal)\b",
                                                                           PRIMITIVE_GOAL, 0.90),
    (r"\b(ongoing\s+(objective|task|mission)|über\s+mehrere\s+sessions?|multi.session)\b",
                                                                           PRIMITIVE_GOAL, 0.85),
    (r"\b(dauerhaftes?\s+ziel|persistent\s+(goal|objective))\b",           PRIMITIVE_GOAL, 0.88),

    # ── COMPUTE ──────────────────────────────────────────────────────────────
    (r"\b(berechne?\s+(statistik|mittelwert|median|varianz)|calculate\s+(stat|mean|median))\b",
                                                                           PRIMITIVE_COMPUTE, 0.90),
    (r"\b(plot|chart|graph|histogram|scatter)\b",                          PRIMITIVE_COMPUTE, 0.80),
    (r"\b(csv|xlsx?|large\s+dataset|datensatz|tabelle|dataframe)\b",       PRIMITIVE_COMPUTE, 0.75),
    (r"\b(machine\s+learning|ml\s+model|trainiere?|regression|clustering)\b",
                                                                           PRIMITIVE_COMPUTE, 0.85),
    (r"\b(batch\s+(transform|process|convert)|data\s+pipeline)\b",         PRIMITIVE_COMPUTE, 0.80),

    # ── DELEGATE ─────────────────────────────────────────────────────────────
    (r"\bdelegiere?\b",                                                     PRIMITIVE_DELEGATE, 0.85),
    (r"\b(delegiere?\s+(an|to)|(pass|hand)\s+this\s+(to|off))\b",          PRIMITIVE_DELEGATE, 0.95),
    (r"\b(frag\s+(hermes|copilot|codex|opencode))\b",                      PRIMITIVE_DELEGATE, 0.95),
    (r"\b(ask\s+(hermes|copilot|codex|opencode)|use\s+(hermes|copilot)\s+for)\b",
                                                                           PRIMITIVE_DELEGATE, 0.95),
    (r"\b(via\s+hermes|with\s+hermes|mit\s+hermes|hermes-\w+)\b",          PRIMITIVE_DELEGATE, 0.90),
]

# Compile once at import.
_SIGNALS: Final[list[tuple[re.Pattern[str], str, float]]] = [
    (re.compile(pat, re.IGNORECASE | re.UNICODE), prim, weight)
    for pat, prim, weight in _RAW_SIGNALS
]

# ── LLM prompt ───────────────────────────────────────────────────────────────

_LLM_CLASSIFY_PROMPT: Final[str] = """\
Classify the following task into exactly one execution primitive.
Return ONLY a single JSON object on one line — no markdown, no explanation.

Schema: {"primitive": "<PRIMITIVE>", "confidence": <0.0-1.0>, "reason": "<max 120 chars>"}

Primitives:
  GOAL      — persistent multi-session objective ("set as ongoing goal", "remember this task")
  LOOP      — recurring / time-based iteration, monitoring, or retry-until-green
  WORKFLOW  — codebase-wide parallel multi-agent sweep, comprehensive audit, or parallel review
  COMPUTE   — deterministic data processing: statistics, charts, CSV/dataset transforms
  DELEGATE  — explicit delegation to a named engine or persona (hermes, copilot, codex, opencode)
  DIRECT    — everything else (default; single-turn, straightforward request)

Task:
{task}"""

# ── ACSBlueprint dataclass ───────────────────────────────────────────────────

@dataclass
class ACSBlueprint:
    """Classification result from classify()."""
    primitive:  str   = PRIMITIVE_DIRECT   # one of ALL_PRIMITIVES
    confidence: float = 0.5
    path:       str   = "heuristic"        # "heuristic" | "llm" | "llm_opt_out" | "error"
    reason:     str   = ""
    params:     dict  = field(default_factory=dict)

    # LDD skill mapping (ADR-0155 §LDD integration contract)
    @property
    def ldd_skills(self) -> list[str]:
        return {
            PRIMITIVE_GOAL:     ["loop-driven-engineering", "drift-detection"],
            PRIMITIVE_LOOP:     ["e2e-driven-iteration", "reproducibility-first"],
            PRIMITIVE_WORKFLOW: ["dialectical-reasoning", "docs-as-definition-of-done"],
            PRIMITIVE_COMPUTE:  ["docs-as-definition-of-done"],
            PRIMITIVE_DELEGATE: ["root-cause-by-layer"],
            PRIMITIVE_DIRECT:   [],
        }.get(self.primitive, [])

# ── Stage 1: heuristic classifier ────────────────────────────────────────────

def heuristic_classify(task: str) -> ACSBlueprint:
    """Fast-path keyword classifier. < 1 ms, no API call.

    Returns ACSBlueprint with path="heuristic".
    confidence is max(scores); primitive is the argmax.
    """
    scores: dict[str, float] = {p: 0.0 for p in ALL_PRIMITIVES}

    for pattern, primitive, weight in _SIGNALS:
        if pattern.search(task):
            scores[primitive] = max(scores[primitive], weight)

    best_primitive = max(scores, key=lambda p: scores[p])
    best_score = scores[best_primitive]

    if best_score < 0.01:
        return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.95, path="heuristic",
                            reason="no signal matched")

    return ACSBlueprint(
        primitive=best_primitive,
        confidence=round(best_score, 3),
        path="heuristic",
        reason=f"signal match (score={best_score:.2f})",
    )

# ── Stage 2: LLM fallback (M3) ───────────────────────────────────────────────

def _llm_classify(task: str) -> ACSBlueprint:
    """Haiku-4.5 fallback for ambiguous tasks (confidence < HEURISTIC_THRESHOLD).

    Uses helper_model.py subprocess pattern — MUST NOT call anthropic SDK directly.
    Returns ACSBlueprint with path="llm" or path="llm_opt_out" / "llm_error" on failure.
    """
    try:
        import helper_model as _hm  # type: ignore[import]
        model_args = _hm.claude_args(_hm.SITE_ACS_CLASSIFY)
        if not model_args:
            return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                                path="llm_opt_out", reason="helper model opted out")

        bin_path = _hm.resolve_claude_bin()
        prompt = _LLM_CLASSIFY_PROMPT.replace("{task}", task[:2000])
        cmd = (
            [bin_path, "-p", prompt, "--max-turns", "1", "--no-tools",
             "--output-format", "text"]
            + model_args
        )
        proc = subprocess.run(
            cmd, capture_output=True, text=True,
            timeout=LLM_TIMEOUT_S, check=False,
        )
        if proc.returncode != 0:
            return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                                path="llm_error", reason=f"rc={proc.returncode}")

        raw = proc.stdout.strip()
        # Two-stage extraction: try direct parse first (model returned clean
        # JSON), then fall back to rfind for preamble-wrapped output.
        # rfind anchors to the LAST { ... LAST } so trailing prose after the
        # object is excluded. Note: rfind can fail when `reason` contains `}`
        # (e.g. "uses {...} pattern") — the direct-parse fast-path handles this
        # case correctly, so well-formed responses are never misextracted.
        try:
            data = json.loads(raw)
        except (ValueError, TypeError):
            start, end = raw.rfind("{"), raw.rfind("}")
            if start < 0 or end <= start:
                return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                                    path="llm_error", reason="no JSON in output")
            try:
                data = json.loads(raw[start:end + 1])
            except (ValueError, TypeError):
                return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                                    path="llm_error", reason="bad_json_in_fallback")
        primitive = str(data.get("primitive", PRIMITIVE_DIRECT)).upper()
        if primitive not in ALL_PRIMITIVES:
            primitive = PRIMITIVE_DIRECT
        confidence = min(1.0, max(0.0, float(data.get("confidence", 0.7))))
        reason = str(data.get("reason", ""))[:200]
        return ACSBlueprint(primitive=primitive, confidence=round(confidence, 3),
                            path="llm", reason=reason)

    except Exception as exc:  # noqa: BLE001
        return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                            path="llm_error", reason=type(exc).__name__)

# ── Public entry point ────────────────────────────────────────────────────────

def classify(
    task: str,
    *,
    channel: str = "",
    chat_key: str = "",
    force_heuristic: bool = False,
) -> ACSBlueprint:
    """Classify task → ACSBlueprint. Fail-open: always returns a blueprint.

    Args:
        task: The raw user message / prompt to classify.
        channel: Bridge channel name (for audit metadata).
        chat_key: Chat identifier (for audit metadata).
        force_heuristic: Skip LLM fallback even at low confidence (tests/perf).

    Returns:
        ACSBlueprint with primitive, confidence, path, reason, ldd_skills.
    """
    if not task or not task.strip():
        return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.95,
                            path="heuristic", reason="empty task")

    try:
        bp = heuristic_classify(task)
        if bp.primitive == PRIMITIVE_DIRECT and bp.reason == "no signal matched":
            # No signals fired → confidence is effectively 0.95 for DIRECT
            return bp
        if bp.confidence >= HEURISTIC_THRESHOLD or force_heuristic:
            return bp
        # Stage 2: LLM fallback
        llm_bp = _llm_classify(task)
        return llm_bp
    except Exception as exc:  # noqa: BLE001
        return ACSBlueprint(primitive=PRIMITIVE_DIRECT, confidence=0.5,
                            path="error", reason=type(exc).__name__)

# ── Directive renderer (M2 + M4) ─────────────────────────────────────────────

_CONVERGENCE_BY_PRIMITIVE: Final[dict[str, str]] = {
    PRIMITIVE_LOOP:     "dry_streak=2 OR max_rounds=3",
    PRIMITIVE_WORKFLOW: "dry_streak=2 OR max_rounds=3",
    PRIMITIVE_GOAL:     "user-confirmed completion or /goal clear",
}

_SEVERITY_GATE: Final[str] = (
    "CRITICAL + HIGH block convergence. "
    "MED/LOW → collect as backlog, report in final summary only."
)

# ADR-0160 M4a: worker personas whose engine cannot execute WORKFLOW or DELEGATE.
# Suppression is a rendering decision only — classify() still returns the true primitive.
_WORKER_PERSONAS: Final[frozenset[str]] = frozenset({
    "hermes-worker",
    "copilot-worker",
})

_SUPPRESSED_FOR_WORKERS: Final[frozenset[str]] = frozenset({
    PRIMITIVE_WORKFLOW,
    PRIMITIVE_DELEGATE,
})


def render_directive_block(
    bp: ACSBlueprint,
    *,
    persona: str = "",
    convergence_override: "dict[str, str] | None" = None,
) -> str:
    """Render the <acs_directive> XML block for system-prompt injection.

    Args:
        bp: Classification result from classify().
        persona: Active persona name (ADR-0160 M4a). When a worker persona
            (hermes-worker, copilot-worker) is active and the primitive is
            WORKFLOW or DELEGATE, returns "" — the engine cannot execute these
            primitives. The caller should emit acs_x.persona_suppressed.
        convergence_override: Per-tenant convergence strings keyed by primitive
            (ADR-0160 M4b). Falls back to _CONVERGENCE_BY_PRIMITIVE when None
            or when the primitive is not in the override dict.

    Returns empty string when:
      - primitive is DIRECT (no directive needed)
      - confidence < 0.50 (classifier not confident enough)
      - persona is a worker persona AND primitive cannot be executed by it
    """
    if bp.primitive == PRIMITIVE_DIRECT:
        return ""
    if bp.confidence < 0.50:
        return ""
    # ADR-0160 M4a — persona suppression
    if persona in _WORKER_PERSONAS and bp.primitive in _SUPPRESSED_FOR_WORKERS:
        return ""

    skills_str = ", ".join(bp.ldd_skills) if bp.ldd_skills else "per CLAUDE.md LDD-MAX table"
    # ADR-0160 M4b — tenant convergence override (None → use built-in default)
    if convergence_override and bp.primitive in convergence_override:
        convergence = str(convergence_override[bp.primitive])
    else:
        convergence = _CONVERGENCE_BY_PRIMITIVE.get(bp.primitive, "task-specific")

    lines = [
        f'<acs_directive primitive="{bp.primitive}" confidence="{bp.confidence:.2f}"'
        f' path="{bp.path}">',
        f"Execution primitive selected: {bp.primitive}",
    ]
    if convergence != "task-specific":
        lines.append(f"Convergence: {convergence}")
        lines.append(f"Severity gate: {_SEVERITY_GATE}")
    lines.append(f"Active LDD skills: {skills_str}")
    if bp.primitive == PRIMITIVE_LOOP:
        lines.append(
            "Use /loop or self-paced iteration. Stop immediately when convergence "
            "criteria are met. Do NOT continue iterating after convergence."
        )
        # ADR-0164 M2 — loop engineering invariants
        lines.append(
            "Loop engineering (ADR-0164): "
            "(1) Capture loss signal BEFORE first edit — run test/E2E/measure now. "
            "(2) State convergence criterion explicitly in /goal before iteration 1. "
            "(3) K_MAX=5 default — at K_MAX produce escalation report, never silently iterate further. "
            "(4) Deduplicate findings centrally before fixing — never fix per-sub-agent."
        )
    elif bp.primitive == PRIMITIVE_WORKFLOW:
        lines.append(
            "Use the Workflow tool for parallel multi-agent execution. "
            "Apply adversarial verification (CONFIRMED/PLAUSIBLE/REFUTED) per finding. "
            "Post exactly ONE final report on convergence."
        )
        # ADR-0164 M2 — workflow engineering invariants
        lines.append(
            "Workflow engineering (ADR-0164): "
            "(1) Define JSON output schema for every agent — no unstructured text output. "
            "(2) Fan-out discovery agents first (parallel), synthesis after ALL complete. "
            "(3) Each CRITICAL/HIGH finding must pass >=1 independent verifier agent. "
            "(4) Dry-streak convergence: stop when 2 consecutive rounds add no new CRITICAL/HIGH."
        )
    elif bp.primitive == PRIMITIVE_GOAL:
        lines.append(
            "A session_goal has been or should be set for this objective. "
            "Apply LDD-MAX for all iterations. Run drift-detection after each session."
        )
    elif bp.primitive == PRIMITIVE_COMPUTE:
        lines.append(
            "Use L25 compute_run or A2A DeterministicComputeEngine for data processing. "
            "Register results via artifact_register. Apply docs-as-definition-of-done."
        )
    elif bp.primitive == PRIMITIVE_DELEGATE:
        lines.append(
            "Delegate to the specified engine/persona. "
            "Apply root-cause-by-layer before spawning."
        )
    lines.append("</acs_directive>")
    return "\n".join(lines)
