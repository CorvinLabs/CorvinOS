"""Skill-Creator: Meta-skill that generates, validates, and refines new skills.

Orchestrates 5-phase workflow:
  1. Planning (Dialectical Reasoning)
  2. Validation (Schema/Linting)
  3. LDD-Iteration (E2E test loop, k≤5)
  4. Adversarial Review (3 reviewers, 0-finding target)
  5. Promotion (SkillForge Registration)

ADR-0325: Skill-Creator Implementation
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from uuid import uuid4

try:  # package-relative (normal import path)
    from .llm_client import resolve_llm_client, engine_id_of
    from .registry_bridge import promote_to_registry
except ImportError:  # pragma: no cover — flat sys.path insert (console route)
    from skill_creator.llm_client import resolve_llm_client, engine_id_of
    from skill_creator.registry_bridge import promote_to_registry


def _default_registry_root(tenant_id: str | None = None) -> Path:
    """`<tenant_home>/skill-forge` for `tenant_id`.

    `skill-forge` is a SIBLING of `global` in the tenant tree, not a child —
    and the sibling path is what `MultiSkillRegistry._root_for("user")`
    resolves, hence the only root `skill_inject` reads.

    Console callers pass the tenant from the authenticated session; this
    fallback exists for CLI and test use, where there is no session.
    """
    try:
        from forge.paths import tenant_home  # noqa: PLC0415
        return Path(tenant_home(tenant_id)) / "skill-forge"
    except Exception:  # noqa: BLE001 — degrade to the documented tenant tree
        home = Path(os.environ.get("CORVIN_HOME") or (Path.home() / ".corvin"))
        return home / "tenants" / (tenant_id or "_default") / "skill-forge"

logger = logging.getLogger(__name__)


class SkillScope(str, Enum):
    """Skill scope hierarchy."""
    ASSISTANT = "assistant"  # User-bound, single session
    PROJECT = "project"      # Project-scoped, persists across sessions
    GLOBAL = "global"        # Global, shared across all users (rare)


class ReviewVerdict(str, Enum):
    """Adversarial reviewer verdict."""
    REFUTED = "refuted"          # Finding is incorrect; skill is sound
    PLAUSIBLE = "plausible"      # Finding is uncertain; needs investigation
    CONFIRMED = "confirmed"      # Finding is real; skill needs iteration


@dataclass(frozen=True)
class SkillSpec:
    """Immutable skill specification (before promotion)."""
    spec_id: str
    name: str                    # Format: scope.name (e.g., assistant.validate_json)
    scope: SkillScope
    purpose: str                 # One-sentence purpose
    method: str                  # Full skill body (Markdown)
    dependencies: List[str]      # Tools/modules required
    keywords: List[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    iteration_count: int = 0
    generated_by: str = "skill-creator"


@dataclass
class ReviewFinding:
    """Adversarial review finding."""
    finding_id: str
    dimension: str               # correctness / simplification / scope_creep
    summary: str
    verdict: ReviewVerdict
    reasoning: str
    line_ref: Optional[str] = None


@dataclass
class SkillArtifact:
    """Completed skill ready for promotion."""
    artifact_id: str
    spec: SkillSpec
    quality_score: float         # 0.0-1.0 (1.0 = all reviewers REFUTED, k=1)
    review_findings: List[ReviewFinding]
    ldd_iterations: int
    created_at: datetime = field(default_factory=datetime.utcnow)
    # SkillForge registration result (path, scope, bootstrap_graded,
    # injectable). `injectable` is the honest answer to "will this skill
    # ever be used?" — a registered but ungraded skill is invisible to
    # skill_inject's eligibility gate.
    registration: Dict[str, Any] = field(default_factory=dict)


class SkillCreatorError(Exception):
    """Base exception for skill creation failures."""
    pass


class PlanningError(SkillCreatorError):
    """Phase 1 failure."""
    pass


class ValidationError(SkillCreatorError):
    """Phase 2 failure."""
    pass


class LDDIterationError(SkillCreatorError):
    """Phase 3 failure."""
    pass


class ReviewError(SkillCreatorError):
    """Phase 4 failure."""
    pass


class PromotionError(SkillCreatorError):
    """Phase 5 failure."""
    pass


# ============================================================================
# NAME NORMALISATION
# ============================================================================

# The one canonical skill-name contract, enforced fail-closed by
# SkillValidator (Phase 2) and used to normalise Phase 1 output.
SKILL_NAME_RE = r"^(assistant|project)\.[a-z_]+$"


def _coerce_scope(raw: Any) -> SkillScope:
    """Map a model-produced scope string onto SkillScope; default assistant."""
    try:
        return SkillScope(str(raw).strip().lower())
    except (ValueError, AttributeError, TypeError):
        return SkillScope.ASSISTANT


def normalize_skill_name(raw: Any, scope: Any = None) -> str:
    """Normalise a generated skill name to the `<scope>.<snake_case>` contract.

    This is a SPELLING normalisation, not a relaxation of the validator: an
    LLM reliably picks the right *words* and unreliably picks the right
    *separator* ("assistant.json-syntax-check"), and throwing away a whole
    multi-minute generation over a hyphen is a worse answer than converting
    it. Phase 2 still validates the result fail-closed, so a name that
    cannot be normalised into the contract is still rejected.
    """
    text = str(raw or "").strip().lower()
    prefix = _coerce_scope(scope).value

    if "." in text:
        head, _, tail = text.partition(".")
        if head in ("assistant", "project"):
            prefix, text = head, tail
        else:
            # A dotted name with some other prefix — keep the whole thing as
            # the local part rather than silently dropping a word.
            text = text.replace(".", "_")

    # Separators → underscore; drop everything the contract forbids.
    local = re.sub(r"[\s\-.]+", "_", text)
    local = re.sub(r"[^a-z_]", "", local)
    local = re.sub(r"_+", "_", local).strip("_")

    return f"{prefix}.{local or 'generated_skill'}"


# Bounds enforced by SkillValidator, declared once so the Phase 1 prompt, the
# normaliser and the validator cannot drift apart.
PURPOSE_LEN = (20, 200)
METHOD_LEN = (100, 5000)


def _collapse_ws(text: str) -> str:
    """Collapse runs of whitespace into single spaces and trim."""
    return re.sub(r"\s+", " ", str(text or "")).strip()


def shorten_purpose(raw: Any, max_len: int = PURPOSE_LEN[1]) -> str:
    """Trim an over-long purpose to the contract without mangling it.

    A model asked for "one sentence" regularly lands a handful of characters
    over the cap (measured: 201/200). Failing the whole run there throws away
    every phase that already succeeded, so an over-long purpose is shortened
    deterministically:

      * prefer a sentence boundary, when the resulting text still carries the
        substance (>=60% of the cap);
      * otherwise cut at a word boundary and mark the elision with "…".

    Too SHORT is not repaired here — that is a real defect in the generated
    spec, and the validator rejects it.
    """
    text = _collapse_ws(raw)
    if len(text) <= max_len:
        return text

    window = text[:max_len]
    sentence_end = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    if sentence_end >= int(max_len * 0.6):
        return window[: sentence_end + 1].strip()

    # Reserve one character for the ellipsis.
    word_window = text[: max_len - 1]
    cut = word_window.rfind(" ")
    if cut < int(max_len * 0.5):
        cut = max_len - 1
    return word_window[:cut].rstrip(" ,;:-") + "\u2026"


def normalize_method(raw: Any) -> str:
    """Strip the wrappers a model puts around a Markdown body.

    `SkillValidator` requires the method to START with a heading. A leading
    blank line or a ```markdown fence — both common in LLM output — failed
    that rule on a body that was otherwise perfectly valid.
    """
    text = str(raw or "").strip()
    fence = re.match(r"^```[a-zA-Z]*\n(.*?)\n?```$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    return text


def normalize_spec(spec: "SkillSpec") -> "SkillSpec":
    """Apply every deterministic, meaning-preserving fix before validation.

    Phase 1 generates freely and Phase 2 validates fail-closed; without this
    step in between, a formatting slip (a hyphen in the name, one character
    of purpose, a stray code fence) discards a multi-minute run. Only
    normalisations that cannot change what the skill MEANS belong here —
    everything else stays the validator's job.
    """
    return SkillSpec(
        **{
            **spec.__dict__,
            "name": normalize_skill_name(spec.name, spec.scope),
            "purpose": shorten_purpose(spec.purpose),
            "method": normalize_method(spec.method),
        }
    )


# ============================================================================
# PHASE 1: PLANNING (Dialectical Reasoning)
# ============================================================================

class SkillPlanner:
    """Phase 1: Generate skill spec via thesis → antithesis → synthesis."""

    def __init__(self, claude_client=None):
        """Initialize planner with LLM client (or fall back to local generation).

        The default engine is the Claude Code CLI (Max subscription) — see
        ``llm_client.resolve_llm_client``. Only when NO engine is reachable
        does the planner degrade to local template generation.
        """
        self.client = resolve_llm_client(claude_client)
        self.use_local = self.client is None
        logger.info(
            "SkillPlanner initialized: engine=%s use_local=%s",
            engine_id_of(self.client), self.use_local,
        )

    async def plan(self, user_request: str,
                   base: Optional[Dict[str, str]] = None) -> SkillSpec:
        """Generate SkillSpec from user request, or refine an existing skill.

        Args:
            user_request: Natural language request, or — with `base` — the
                change the operator wants made.
            base: `{"name": ..., "body": ...}` of an existing skill. Turns
                planning into a REFINE round: the current body is the
                starting point and its name is preserved, so iterating on a
                skill updates it in place instead of spawning a near-duplicate
                under a name the model happened to pick this time.

        Returns:
            SkillSpec (unvalidated, for Phase 2)

        Raises:
            PlanningError: If planning fails at any stage
        """
        try:
            if base and self.client is not None:
                spec = await self._refine_spec(user_request, base)
                logger.info("Phase 1 Refine complete: %s", spec.name)
                return spec

            if self.use_local or self.client is None:
                # Local generation mode (no Claude API needed)
                logger.info("Using local skill generation (no API key required)")
                spec = self._generate_skill_spec_locally(user_request)
            else:
                # Claude-based generation (requires API key)
                # Step 1: Generate thesis (optimistic interpretation)
                thesis = await self._generate_thesis(user_request)

                # Step 2: Generate antithesis (critical interpretation)
                antithesis = await self._generate_antithesis(thesis, user_request)

                # Step 3: Synthesize into SkillSpec
                spec = await self._synthesize_spec(thesis, antithesis, user_request)

            logger.info(f"Phase 1 Planning complete: {spec.name}")
            return spec

        except Exception as e:
            raise PlanningError(f"Planning failed: {e}") from e

    def _generate_skill_spec_locally(self, user_request: str) -> SkillSpec:
        r"""Generate SkillSpec locally without Claude API (template-based).

        This mode allows skill creation without ANTHROPIC_API_KEY.
        Uses heuristics and templates to infer skill structure.

        CRITICAL: Skill names MUST match ^(assistant|project)\.[a-z_]+$
        """
        import re
        from datetime import datetime

        # Parse user request to extract key terms
        words = user_request.lower().split()

        # Generate skill name from first meaningful words
        name_parts = [w for w in words[:3] if len(w) > 3]
        skill_name = normalize_skill_name(
            "_".join(name_parts[:2]) if name_parts else "generated_skill",
            SkillScope.ASSISTANT,
        )
        base_name = skill_name.split(".", 1)[1]

        logger.info(f"Generated local skill name (normalized): {skill_name}")

        # Infer purpose from request
        purpose = user_request[:80] if user_request else "Generated skill"

        # Generate basic skill method (template-based)
        method = f"""# {base_name.replace('_', ' ').title()} Skill

{purpose}

## Usage

This skill was generated locally without Claude API.

## Implementation

This is a template skill. Customize as needed:

- Input handling
- Processing logic
- Output formatting

## Example

```python
# Example usage in code
result = use_skill("{skill_name}", input_data)
```
"""

        spec = SkillSpec(
            spec_id=str(uuid4()),
            name=skill_name,  # NOW includes "assistant." prefix
            scope=SkillScope.ASSISTANT,
            purpose=purpose,
            method=method,
            dependencies=[],
            keywords=name_parts,
            created_at=datetime.utcnow(),
            iteration_count=0,
            generated_by="skill-creator-local",
        )

        logger.info(f"Generated local SkillSpec: {spec.name}")
        # Validate name format
        if not re.match(SKILL_NAME_RE, spec.name):
            raise ValidationError(
                f"Invalid skill name format: {spec.name}. Must match {SKILL_NAME_RE}"
            )

        return spec

    async def _refine_spec(self, instruction: str, base: Dict[str, str]) -> SkillSpec:
        """Rewrite an existing skill according to the operator's instruction."""
        base_name = base.get("name") or ""
        base_body = base.get("body") or ""

        prompt = f"""You are refining an EXISTING skill. Apply the operator's
change and keep everything else intact — this replaces the skill in place, so
anything you drop is lost.

CURRENT SKILL ({base_name}):
{base_body}

OPERATOR'S REQUESTED CHANGE:
{instruction}

Reply with the updated spec as JSON ONLY, no prose outside the object:
{{
  "name": "{base_name}",     // keep this name EXACTLY
  "scope": "assistant",
  "purpose": "<one sentence, {PURPOSE_LEN[0]}-{PURPOSE_LEN[1]} characters>",
  "method": "<Markdown body starting with '# Title', {METHOD_LEN[0]}-{METHOD_LEN[1]} characters>",
  "dependencies": ["<tool1>"],
  "keywords": ["<keyword1>"]
}}"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            raise PlanningError("Refine returned no JSON")
        try:
            data = json.loads(match.group())
        except (json.JSONDecodeError, ValueError) as exc:
            raise PlanningError(f"Refine returned unparseable JSON: {exc}") from exc

        # The name is the operator's, not the model's: a refine that renames
        # the skill would leave the original in place and register a second
        # one, which is the opposite of "modify this skill".
        return SkillSpec(
            spec_id=str(uuid4()),
            name=normalize_skill_name(base_name or data.get("name"), data.get("scope")),
            scope=_coerce_scope(data.get("scope")),
            purpose=data.get("purpose", ""),
            method=normalize_method(data.get("method", "")),
            dependencies=data.get("dependencies", []),
            keywords=data.get("keywords", []),
            generated_by="skill-creator-refine",
        )

    async def repair(self, spec: SkillSpec, problems: List[str]) -> SkillSpec:
        """Re-emit a spec that fixes the listed validator violations.

        Phase 2 is fail-closed by design, but "reject and lose the run" is
        the wrong response to a formatting miss the model can correct in one
        round. Only fields are rewritten — the skill's identity and intent
        must survive, so a repair that changes the purpose beyond recognition
        is still caught by the re-validation that follows.
        """
        if self.client is None:
            return spec

        listed = "\n".join(f"- {p}" for p in problems)
        prompt = f"""A generated skill spec failed validation. Fix ONLY the listed
problems and keep everything else — especially the skill's purpose and
intent — unchanged.

PROBLEMS:
{listed}

CURRENT SPEC:
name: {spec.name}
scope: {spec.scope.value}
purpose: {spec.purpose}
method:
{spec.method}

Reply with the corrected spec as JSON ONLY, no prose outside the object:
{{
  "name": "<{SKILL_NAME_RE}>",
  "scope": "{spec.scope.value}",
  "purpose": "<one sentence, {PURPOSE_LEN[0]}-{PURPOSE_LEN[1]} characters>",
  "method": "<Markdown body starting with '# Title', {METHOD_LEN[0]}-{METHOD_LEN[1]} characters>",
  "dependencies": {json.dumps(spec.dependencies)},
  "keywords": {json.dumps(spec.keywords)}
}}"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=2000,
            messages=[{"role": "user", "content": prompt}]
        )
        text = response.content[0].text
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            logger.warning("Spec repair returned no JSON — keeping original spec")
            return spec
        try:
            fixed = json.loads(match.group())
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning("Spec repair returned unparseable JSON (%s)", exc)
            return spec

        return SkillSpec(
            **{
                **spec.__dict__,
                "name": normalize_skill_name(fixed.get("name") or spec.name, spec.scope),
                "purpose": _collapse_ws(fixed.get("purpose") or spec.purpose),
                "method": normalize_method(fixed.get("method") or spec.method),
                "dependencies": fixed.get("dependencies", spec.dependencies),
                "keywords": fixed.get("keywords", spec.keywords),
            }
        )

    async def _generate_thesis(self, user_request: str) -> str:
        """Optimistic interpretation: literal reading of user request."""
        prompt = f"""You are a skill architect. The user wants to create a skill.
User request: "{user_request}"

THESIS: Interpret this request as literally as possible. What specific,
concrete skill can you build? What would it do? Give 3-4 bullet points."""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    async def _generate_antithesis(self, thesis: str, user_request: str) -> str:
        """Critical interpretation: risks, edge cases, scope creep."""
        prompt = f"""You are a critical reviewer. Based on this skill idea:

THESIS:
{thesis}

Original request: "{user_request}"

ANTITHESIS: What could go wrong? What are the risks, edge cases,
scope creep, dependencies, or hidden costs? Give 3-4 bullet points."""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    async def _synthesize_spec(self, thesis: str, antithesis: str,
                                user_request: str) -> SkillSpec:
        """Synthesis: merge thesis & antithesis into concrete SkillSpec YAML."""
        prompt = f"""You are a skill synthesizer. Based on:

THESIS (optimistic):
{thesis}

ANTITHESIS (critical):
{antithesis}

Original user request: "{user_request}"

SYNTHESIS: Create a SkillSpec that balances both viewpoints. Generate JSON:
{{
  "name": "assistant.snake_case_name",  // MUST match ^(assistant|project)\\.[a-z_]+$
                                        // lowercase + underscores ONLY — no hyphens,
                                        // no digits, e.g. "assistant.validate_json"
  "scope": "assistant",      // or "project"
  "purpose": "<one sentence, {PURPOSE_LEN[0]}-{PURPOSE_LEN[1]} characters INCLUDING spaces>",
  "method": "<Markdown body starting with '# Title', {METHOD_LEN[0]}-{METHOD_LEN[1]} characters>",
  "dependencies": ["<tool1>", "<tool2>"],
  "keywords": ["<keyword1>", "<keyword2>"]
}}

Make the method section concrete, actionable, and safe (no prompt injection patterns).
The character limits are hard — a spec outside them is rejected."""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )

        # Parse JSON from response
        response_text = response.content[0].text
        json_match = re.search(r"\{.*\}", response_text, re.DOTALL)
        if not json_match:
            raise PlanningError(f"Could not extract JSON from synthesis")

        spec_dict = json.loads(json_match.group())

        # Build SkillSpec
        return SkillSpec(
            spec_id=str(uuid4()),
            name=normalize_skill_name(spec_dict.get("name"), spec_dict.get("scope")),
            scope=_coerce_scope(spec_dict.get("scope")),
            purpose=spec_dict["purpose"],
            method=spec_dict["method"],
            dependencies=spec_dict.get("dependencies", []),
            keywords=spec_dict.get("keywords", []),
        )


# ============================================================================
# PHASE 2: VALIDATION (Schema + Linting)
# ============================================================================

class SkillValidator:
    """Phase 2: Validate SkillSpec against schema and linting rules."""

    def __init__(self):
        """Initialize validator with rules."""
        self.rules = self._build_rules()

    def _build_rules(self):
        """Build validation rules."""
        return {
            "name_format": SKILL_NAME_RE,
            "purpose_length": PURPOSE_LEN,
            "method_length": METHOD_LEN,
            # NOTE: these are REGEXes. `<|im_start|>` unescaped is the
            # alternation `<` | `im_start` | `>`, which rejected any skill
            # containing a single angle bracket — e.g. a `<placeholder>` in
            # an example — so Phase 2 failed most generated skills. The
            # markers are literals; escape them.
            "forbidden_patterns": [
                re.escape("<|im_start|>"),   # Prompt injection
                re.escape("<|im_end|>"),
                # Role markers at the START of a line only: a pseudo-turn
                # injected into the skill body. "## Instructions:" inside
                # ordinary Markdown prose is legitimate and must pass.
                r"^\s*instructions\s*:",
                r"^\s*system\s*:",
            ]
        }

    def collect_violations(self, spec: SkillSpec) -> List[str]:
        """Return EVERY rule violation, in rule order (empty = valid).

        `validate` raises on the first one, which is the right behaviour for
        a fail-closed gate but useless as a repair signal: fixing one
        violation only to fail on the next costs another engine call per
        round. The orchestrator feeds this whole list back to the model in
        one shot.
        """
        problems: List[str] = []

        # Rule 1: Name format
        if not re.match(self.rules["name_format"], spec.name):
            problems.append(f"Invalid name format: {spec.name}. "
                            f"Must match: {self.rules['name_format']}")

        # Rule 2: Purpose length
        min_len, max_len = self.rules["purpose_length"]
        if not (min_len <= len(spec.purpose) <= max_len):
            problems.append(f"Purpose length {len(spec.purpose)} outside "
                            f"range [{min_len}, {max_len}]")

        # Rule 3: Method length
        min_len, max_len = self.rules["method_length"]
        if not (min_len <= len(spec.method) <= max_len):
            problems.append(f"Method length {len(spec.method)} outside "
                            f"range [{min_len}, {max_len}]")

        # Rule 4: Forbidden patterns
        for pattern in self.rules["forbidden_patterns"]:
            if re.search(pattern, spec.method, re.IGNORECASE | re.MULTILINE):
                problems.append(f"Forbidden pattern detected in method: {pattern}")

        # Rule 5: Markdown structure
        if not spec.method.startswith("#"):
            problems.append("Method must start with a Markdown heading (# title)")

        return problems

    def validate(self, spec: SkillSpec) -> None:
        """Validate SkillSpec; raise ValidationError if invalid.

        Args:
            spec: SkillSpec to validate

        Raises:
            ValidationError: If any validation rule fails (fail-closed)
        """
        problems = self.collect_violations(spec)
        if problems:
            raise ValidationError(problems[0])

        # Rule 6: Dependencies check (basic) — a warning, not a gate.
        builtin_tools = {"bash", "python3", "read", "write", "edit"}
        for dep in spec.dependencies:
            if dep not in builtin_tools:
                logger.warning(f"Dependency not in builtin list: {dep} "
                              f"(will fail at promotion if unavailable)")

        logger.info(f"Validation passed: {spec.name}")


# ============================================================================
# PHASE 3: LDD-ITERATION (E2E test loop, k ≤ 5)
# ============================================================================

# Rubric dimensions scored by the LDD tester, and their weight in the loss.
# Clarity and executability dominate: a skill nobody can follow is worthless,
# while mild scope drift is a quality issue, not a blocker.
_RUBRIC_WEIGHTS = {
    "clarity": 0.35,
    "executability": 0.35,
    "scope": 0.20,
    "coupling": 0.10,
}


class SkillTester:
    """Phase 3: Test skill via E2E scenarios; measure loss; diagnose & fix."""

    # Loss at or below which the skill is considered good enough. The scored
    # rubric in `_measure_loss` is 0.0 for a clean verdict, so this admits a
    # single minor remark without demanding a perfect score.
    convergence_threshold = 0.15

    def __init__(self, claude_client=None, max_iterations: int = 5,
                 escalate_on_k_max: bool = True):
        """Initialize tester (Claude Code CLI by default — no API key needed).

        ``escalate_on_k_max``: raise ``LDDIterationError`` when the loop hits
        k_max without converging (the LDD contract — default). The console
        orchestrator sets it False and prices non-convergence into
        ``quality_score`` instead, so an operator-facing run returns the best
        iterate rather than discarding k_max cloud calls.
        """
        self.client = resolve_llm_client(claude_client)
        self.max_iterations = max_iterations
        self.escalate_on_k_max = escalate_on_k_max
        # Set by ldd_iterate(); read by the orchestrator for quality scoring.
        self.converged: bool = True
        self.final_loss: float = 0.0

    async def ldd_iterate(self, spec: SkillSpec) -> SkillSpec:
        """Run LDD inner loop: test → measure → diagnose → fix (k ≤ 5).

        Args:
            spec: SkillSpec to iterate on

        Returns:
            SkillSpec (refined), with iteration_count updated

        Raises:
            LDDIterationError: If k_max reached without convergence
        """
        if self.client is None:
            # No engine reachable (local template mode). The LDD loop is an
            # LLM-driven measurement; without an engine there is no loss
            # signal to descend, so run zero iterations rather than crash.
            logger.warning("LDD skipped: no LLM engine available (local mode)")
            return spec

        current_spec = spec
        loss_history = []
        best_spec, best_loss = spec, 1.0

        for k in range(1, self.max_iterations + 1):
            logger.info(f"LDD Iteration k={k}")

            # 1. Generate test scenario
            scenario = await self._generate_test_scenario(current_spec)

            # 2. Test skill in scenario
            test_result = await self._test_skill(current_spec, scenario)

            # 3. Measure loss
            loss = self._measure_loss(test_result, current_spec)
            loss_history.append(loss)
            if loss <= best_loss:
                best_spec, best_loss = current_spec, loss

            logger.info(f"  k={k}: loss={loss:.3f}, scenario={scenario[:50]}...")

            # 4. Check convergence
            if loss <= self.convergence_threshold:  # Converged
                logger.info(f"  Converged at k={k}")
                self.converged = True
                self.final_loss = loss
                current_spec = current_spec.__class__(
                    **{**current_spec.__dict__, "iteration_count": k}
                )
                return current_spec

            # 5. Diagnose & fix
            if k < self.max_iterations:
                diagnosis = await self._diagnose_loss(loss, test_result, current_spec)
                current_spec = await self._apply_fix(current_spec, diagnosis)
            else:
                # k == max_iterations without convergence.
                #
                # A hard raise here throws away k_max cloud calls' worth of
                # refinement and hands the operator nothing. LDD's escalation
                # signal is preserved as a NON-converged result the caller
                # prices into `quality_score` (see SkillCreatorOrchestrator),
                # not as a lost run.
                self.converged = False
                self.final_loss = best_loss
                if self.escalate_on_k_max:
                    logger.warning(
                        "k_max reached without convergence. Loss history: %s", loss_history
                    )
                    raise LDDIterationError(
                        f"LDD did not converge after {self.max_iterations} iterations. "
                        f"Loss history: {loss_history}. "
                        f"Consider architectural change or larger step size."
                    )
                logger.warning(
                    "k_max reached without convergence (best_loss=%.3f). "
                    "Loss history: %s — returning best iterate.",
                    best_loss, loss_history,
                )
                return SkillSpec(
                    **{**best_spec.__dict__, "iteration_count": self.max_iterations}
                )

        return current_spec

    async def _generate_test_scenario(self, spec: SkillSpec) -> str:
        """Generate a realistic test scenario for the skill."""
        prompt = f"""Based on this skill:
Name: {spec.name}
Purpose: {spec.purpose}

Generate a realistic test scenario (1-2 sentences) that would exercise this skill.
Example format: "User wants to validate a JSON file with 500 lines and nested objects"
Scenario:"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()

    async def _test_skill(self, spec: SkillSpec, scenario: str) -> Dict[str, Any]:
        """Test skill in scenario and collect a SCORED verdict.

        The reviewer returns a JSON rubric rather than prose. Prose was the
        original design and it could not converge: `_measure_loss` matched
        substrings ("scope", "dependencies") on an answer to a prompt that
        ASKED about scope and dependencies, so those words were present in
        every well-formed evaluation and the loss floor sat above the
        convergence threshold by construction. The rubric measures the
        skill, not the vocabulary of the review.
        """
        prompt = f"""You are testing a skill. The skill is:

Name: {spec.name}
Purpose: {spec.purpose}
Method:
{spec.method}

Test Scenario: {scenario}

Pretend you are a user following this skill's instructions in that scenario,
then score it. Reply with JSON ONLY, no prose outside the object:

{{
  "clarity": 0.0,      // 0.0 = instructions are unambiguous, 1.0 = incomprehensible
  "executability": 0.0,// 0.0 = every step is actionable, 1.0 = cannot be executed
  "scope": 0.0,        // 0.0 = matches the stated purpose, 1.0 = drifts badly
  "coupling": 0.0,     // 0.0 = no undeclared dependencies, 1.0 = many
  "notes": "<one sentence of the single most important weakness, or 'none'>"
}}"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=400,
            messages=[{"role": "user", "content": prompt}]
        )

        text = response.content[0].text
        rubric = self._parse_rubric(text)

        return {
            "scenario": scenario,
            "evaluation": text,
            "rubric": rubric,
            "test_passed": rubric is not None and self._rubric_loss(rubric) <= self.convergence_threshold,
        }

    @staticmethod
    def _parse_rubric(text: str) -> Optional[Dict[str, Any]]:
        """Extract the JSON rubric from a reviewer reply (raw or fenced)."""
        if not isinstance(text, str):
            return None
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if not match:
            return None
        try:
            parsed = json.loads(match.group())
        except (json.JSONDecodeError, ValueError):
            return None
        if not isinstance(parsed, dict):
            return None
        # A rubric must carry at least one scored dimension; otherwise the
        # model replied with some other JSON and we must not read it as 0.0.
        if not any(k in parsed for k in _RUBRIC_WEIGHTS):
            return None
        return parsed

    @staticmethod
    def _score(rubric: Dict[str, Any], key: str) -> float:
        """Read one rubric dimension, clamped to [0, 1]; missing → worst case."""
        raw = rubric.get(key)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            return 1.0
        return max(0.0, min(1.0, float(raw)))

    @classmethod
    def _rubric_loss(cls, rubric: Dict[str, Any]) -> float:
        """Weighted mean of the rubric dimensions."""
        total = sum(
            weight * cls._score(rubric, key) for key, weight in _RUBRIC_WEIGHTS.items()
        )
        return total / sum(_RUBRIC_WEIGHTS.values())

    def _measure_loss(self, test_result: Dict[str, Any], spec: SkillSpec) -> float:
        """Measure loss from the scored rubric.

        Fail-high, not fail-low: an unparseable reply yields loss 1.0 so an
        unverified skill can never masquerade as a converged one.
        """
        rubric = test_result.get("rubric")
        if not rubric:
            logger.warning("No scored rubric in evaluation — treating as max loss")
            return 1.0
        return self._rubric_loss(rubric)

    async def _diagnose_loss(self, loss: float, test_result: Dict[str, Any],
                             spec: SkillSpec) -> str:
        """Diagnose the loss (root cause)."""
        rubric = test_result.get("rubric") or {}
        notes = rubric.get("notes") or test_result.get("evaluation", "")
        prompt = f"""Based on this test evaluation:
{notes}

And this skill:
{spec.method}

What is the ROOT CAUSE of the loss? Be specific (e.g., "instructions assume familiarity with X").
Diagnosis:"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=200,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text.strip()

    async def _apply_fix(self, spec: SkillSpec, diagnosis: str) -> SkillSpec:
        """Fix the skill based on diagnosis."""
        prompt = f"""You are refining a skill. Based on this diagnosis:
{diagnosis}

Current skill method:
{spec.method}

Refine the method section to address the diagnosis. Output ONLY the refined method
(Markdown, 100-500 words). Make it more clear, complete, or in-scope."""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=800,
            messages=[{"role": "user", "content": prompt}]
        )

        refined_method = response.content[0].text.strip()

        # Return new spec with refined method
        return SkillSpec(
            spec_id=spec.spec_id,
            name=spec.name,
            scope=spec.scope,
            purpose=spec.purpose,
            method=refined_method,
            dependencies=spec.dependencies,
            keywords=spec.keywords,
            created_at=spec.created_at,
            iteration_count=spec.iteration_count + 1,
        )


# ============================================================================
# PHASE 4: ADVERSARIAL REVIEW (3 reviewers, 0-finding target)
# ============================================================================

class AdversarialReviewer:
    """Phase 4: 3 independent reviewers on distinct dimensions.

    Dimensions:
      1. Correctness: Find wrong, missing, or impossible instructions
      2. Simplification: Find overcomplicated or redundant instructions
      3. Scope Creep: Find instructions that drift beyond stated purpose
    """

    def __init__(self, claude_client=None):
        """Initialize reviewer (Claude Code CLI by default — no API key needed)."""
        self.client = resolve_llm_client(claude_client)
        self.reviewers = [
            ("correctness", self._review_correctness),
            ("simplification", self._review_simplification),
            ("scope_creep", self._review_scope_creep),
        ]

    async def review(self, spec: SkillSpec) -> List[ReviewFinding]:
        """Run 3 parallel adversarial reviews.

        Args:
            spec: SkillSpec to review

        Returns:
            List of ReviewFindings (may be empty if all REFUTED)

        Raises:
            ReviewError: If review process fails
        """
        if self.client is None:
            logger.warning("Adversarial review skipped: no LLM engine available")
            return []

        try:
            # Run 3 reviewers in parallel
            tasks = [
                self._run_review_dimension(dimension, review_fn, spec)
                for dimension, review_fn in self.reviewers
            ]
            findings_by_dimension = await asyncio.gather(*tasks, return_exceptions=True)

            # Flatten and filter
            all_findings = []
            for findings in findings_by_dimension:
                if isinstance(findings, Exception):
                    logger.error(f"Review failed: {findings}")
                    continue
                all_findings.extend(findings)

            logger.info(f"Review complete: {len(all_findings)} findings")
            return all_findings

        except Exception as e:
            raise ReviewError(f"Review process failed: {e}") from e

    async def _run_review_dimension(self, dimension: str,
                                    review_fn, spec: SkillSpec) -> List[ReviewFinding]:
        """Run one review dimension and parse findings."""
        findings_text = await review_fn(spec)
        return self._parse_findings(findings_text, dimension)

    async def _review_correctness(self, spec: SkillSpec) -> str:
        """Dimension 1: Correctness."""
        prompt = f"""You are a skill reviewer focused on CORRECTNESS.

Skill:
{spec.method}

Purpose: {spec.purpose}

Try to find wrong, missing, or impossible instructions that would mislead
a future user. For each finding, output:
FINDING: <one sentence>
VERDICT: CONFIRMED / PLAUSIBLE / REFUTED

If no findings, output: VERDICT: REFUTED"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    async def _review_simplification(self, spec: SkillSpec) -> str:
        """Dimension 2: Simplification."""
        prompt = f"""You are a skill reviewer focused on SIMPLIFICATION.

Skill:
{spec.method}

Try to find unnecessarily complex, overcomplicated, or redundant instructions
that could be shortened without losing meaning. For each finding, output:
FINDING: <one sentence>
VERDICT: CONFIRMED / PLAUSIBLE / REFUTED

If no findings, output: VERDICT: REFUTED"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    async def _review_scope_creep(self, spec: SkillSpec) -> str:
        """Dimension 3: Scope Creep."""
        prompt = f"""You are a skill reviewer focused on SCOPE CREEP.

Skill name: {spec.name}
Purpose: {spec.purpose}

Method:
{spec.method}

Try to find instructions that do more, less, or different than the stated
purpose — scope drift or unwanted coupling. For each finding, output:
FINDING: <one sentence>
VERDICT: CONFIRMED / PLAUSIBLE / REFUTED

If no findings, output: VERDICT: REFUTED"""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=500,
            messages=[{"role": "user", "content": prompt}]
        )
        return response.content[0].text

    def _parse_findings(self, review_text: str, dimension: str) -> List[ReviewFinding]:
        """Parse findings from review text."""
        findings = []

        # Simple parsing: look for FINDING: ... VERDICT: ...
        finding_pattern = r"FINDING:\s*(.+?)\s+VERDICT:\s*(CONFIRMED|PLAUSIBLE|REFUTED)"
        matches = re.finditer(finding_pattern, review_text, re.IGNORECASE | re.DOTALL)

        for match in matches:
            summary = match.group(1).strip()[:100]  # Truncate to 100 chars
            verdict_str = match.group(2).upper()

            try:
                verdict = ReviewVerdict(verdict_str.lower())
            except ValueError:
                verdict = ReviewVerdict.PLAUSIBLE

            findings.append(ReviewFinding(
                finding_id=str(uuid4()),
                dimension=dimension,
                summary=summary,
                verdict=verdict,
                reasoning=summary,
            ))

        # If no findings parsed, assume REFUTED (null finding)
        if not findings:
            logger.debug(f"No explicit findings in {dimension} review; assuming REFUTED")

        return findings


def score_quality(findings: List[ReviewFinding], *, converged: bool = True,
                  dimensions: int = 3) -> float:
    """Quality in [0, 1] — a PER-DIMENSION score, not a per-finding penalty.

    The original formula was `1.0 - (confirmed*0.3 + plausible*0.1)`, which
    saturates at four confirmed findings. Adversarial reviewers are instructed
    to *find* problems and routinely return five to ten, so in practice every
    run reported 0% and the number carried no information at all (measured
    across four live runs: 5, 6, 10 and 5 confirmed findings).

    Scoring per review DIMENSION bounds the penalty by how many independent
    angles found something, which is what the reviewer panel actually
    measures:

        clean dimension              → 1.0
        only PLAUSIBLE findings      → 0.5
        at least one CONFIRMED       → 0.0

    Non-convergence of the LDD loop costs a further 0.2 — a real signal, not
    a free pass.

    NOTE: a CONFIRMED verdict here is the reviewer's own claim; there is no
    independent verification pass, so treat the score as a review summary,
    not a proof. Findings travel with the artifact so an operator can judge
    them.
    """
    dimensions = max(1, dimensions)
    per_dimension: Dict[str, float] = {}
    for finding in findings:
        current = per_dimension.get(finding.dimension, 1.0)
        if finding.verdict == ReviewVerdict.CONFIRMED:
            per_dimension[finding.dimension] = 0.0
        elif finding.verdict == ReviewVerdict.PLAUSIBLE:
            per_dimension[finding.dimension] = min(current, 0.5)
        else:
            per_dimension.setdefault(finding.dimension, 1.0)

    # Dimensions that reported nothing are clean.
    total = sum(per_dimension.values()) + (dimensions - len(per_dimension)) * 1.0
    score = total / dimensions

    if not converged:
        score -= 0.2
    return round(max(0.0, min(1.0, score)), 3)


# ============================================================================
# PHASE 5: PROMOTION (SkillForge Registration)
# ============================================================================

class SkillPromoter:
    """Phase 5: Register the skill in SkillForge so it can actually be USED.

    Writing a markdown file into a directory is not promotion. Skill
    availability in CorvinOS runs through the SkillForge REGISTRY — a manifest
    (`SkillRegistry.list()` reads the manifest, never the directory), a
    plugin-slot mirror for `user`/`project` scope that makes the skill visible
    to the next `claude` subprocess, and `skill_inject`'s eligibility gate,
    which drops anything with `n_grades < 1 or mean_score <= 0`.

    Until this change the promoter wrote a flat `~/.claude/skills/<name>.md`.
    Nothing in this system reads that path (Claude Code itself expects
    `<name>/SKILL.md`), so every generated skill existed and was unreachable —
    the exact "looks wired, isn't" failure class `e2e-wiring-proof` exists to
    catch.

    `registry_root` is `<tenant-global>/skill-forge`, resolved by the caller
    from the authenticated session's tenant — never from an env var
    (CLAUDE.md, console tenant routing).
    """

    def __init__(self, registry_root: str = None, *, tenant_id: str = None,
                 scope: str = "user"):
        """Initialize promoter.

        Args:
            registry_root: `<tenant-global>/skill-forge`; derived from
                `tenant_id` when omitted.
            tenant_id: tenant whose registry receives the skill.
            scope: SkillForge scope. Only `user` and `project` reach the
                engine plugin slot (Layer-16 scope gate), so promoting below
                them would leave the skill un-injectable by construction.
        """
        from pathlib import Path
        self.scope = scope
        self.tenant_id = tenant_id
        self.registry_root = (
            Path(registry_root) if registry_root else _default_registry_root(tenant_id)
        )
        self.registry_root.mkdir(parents=True, exist_ok=True)

    def promote(self, spec: SkillSpec, quality_score: float) -> SkillArtifact:
        """Promote skill to disk + registry.

        Args:
            spec: Final SkillSpec
            quality_score: Quality score (0-1)

        Returns:
            SkillArtifact (ready for use)

        Raises:
            PromotionError: If promotion fails at any step
        """
        try:
            registration = promote_to_registry(
                self.registry_root,
                name=spec.name,
                body_md=self._render_body(spec),
                description=spec.purpose,
                scope=self.scope,
                run_id=spec.spec_id,
            )

            artifact = SkillArtifact(
                artifact_id=str(uuid4()),
                spec=spec,
                quality_score=quality_score,
                review_findings=[],
                ldd_iterations=spec.iteration_count,
                registration=registration,
            )

            logger.info("Skill promoted: %s (quality=%.2f, injectable=%s)",
                        spec.name, quality_score, registration.get("injectable"))
            return artifact

        except Exception as e:
            raise PromotionError(f"Promotion failed: {e}") from e

    def _render_body(self, spec: SkillSpec) -> str:
        """Body handed to the registry.

        The registry renders its OWN YAML front-matter around this, so the
        body must not carry a second one — two front-matter blocks make the
        skill unparseable for the engine that loads it.
        """
        body = spec.method.strip()
        trailer = []
        if spec.keywords:
            trailer.append(f"**Keywords:** {', '.join(spec.keywords)}")
        if spec.dependencies:
            trailer.append(f"**Dependencies:** {', '.join(spec.dependencies)}")
        if trailer:
            body = body + "\n\n---\n\n" + "\n\n".join(trailer) + "\n"
        return body


# ============================================================================
# ORCHESTRATOR: PHASE 1-5 COORDINATION
# ============================================================================

class SkillCreatorOrchestrator:
    """Main orchestrator for 5-phase skill generation."""

    def __init__(self, claude_client=None, registry_root: str = None,
                 progress_cb: Optional[Callable[[str, int, str], None]] = None,
                 escalate_on_k_max: bool = False):
        """Initialize orchestrator.

        Args:
            claude_client: injected LLM client; None resolves the configured
                engine (Claude Code CLI / Max subscription by default).
            registry_root: `<tenant-global>/skill-forge` receiving the
                promoted skill. Console callers derive it from the
                authenticated session's tenant.
            progress_cb: ``(phase, progress_pct, message)`` called as each of
                the five phases starts and finishes. The console polls the
                run status, which would otherwise sit at "planning 20%" for
                the whole run and jump straight to done.
            escalate_on_k_max: see ``SkillTester``. Defaults to False here —
                an operator-facing run returns its best iterate rather than
                discarding the whole generation.
        """
        # Resolve ONCE and inject, so all four phases provably run on the
        # same engine and a run cannot half-succeed on two backends.
        self.client = resolve_llm_client(claude_client)
        self.engine_id = engine_id_of(self.client)
        self.registry_root = registry_root
        self.progress_cb = progress_cb
        self.planner = SkillPlanner(self.client)
        self.validator = SkillValidator()
        self.tester = SkillTester(self.client, escalate_on_k_max=escalate_on_k_max)
        self.reviewer = AdversarialReviewer(self.client)
        self.promoter = SkillPromoter(registry_root)

    async def _validate_with_repair(self, spec: SkillSpec) -> SkillSpec:
        """Normalise, then validate, then repair once, then validate again.

        The gate stays fail-closed: this only adds two chances to reach a
        valid spec before it rejects. Without them a single character over
        the purpose cap (measured: 201/200) destroyed an entire run that had
        already spent minutes of engine time.
        """
        spec = normalize_spec(spec)
        problems = self.validator.collect_violations(spec)
        if not problems:
            self.validator.validate(spec)
            return spec

        logger.warning("Spec violates %d rule(s); attempting one repair: %s",
                       len(problems), problems)
        self._progress("validation", 40,
                       f"Repairing spec ({len(problems)} validation issue(s))…")
        spec = normalize_spec(await self.planner.repair(spec, problems))

        # Fail-closed: a still-invalid spec raises exactly as before.
        self.validator.validate(spec)
        return spec

    def _progress(self, phase: str, pct: int, message: str) -> None:
        """Report phase progress; a broken callback must never fail a run."""
        if self.progress_cb is None:
            return
        try:
            self.progress_cb(phase, pct, message)
        except Exception as exc:  # noqa: BLE001
            logger.warning("progress callback failed: %s", exc)

    async def create_skill(self, user_request: str,
                           base: Optional[Dict[str, str]] = None) -> SkillArtifact:
        """Orchestrate full skill creation: Phases 1-5.

        Args:
            user_request: User's natural language request

        Returns:
            SkillArtifact (completed, promoted skill)

        Raises:
            SkillCreatorError: If any phase fails
        """
        logger.info(f"=== SKILL CREATION START (engine={self.engine_id}) ===\n"
                    f"Request: {user_request}\n")

        # Phase 1: Planning
        logger.info("PHASE 1: PLANNING...")
        self._progress(
            "planning", 10,
            f"{'Refining' if base else 'Planning'} skill via {self.engine_id}…",
        )
        spec = await self.planner.plan(user_request, base=base)
        logger.info(f"  Spec generated: {spec.name}")

        # Phase 2: Validation
        logger.info("PHASE 2: VALIDATION...")
        self._progress("validation", 35, f"Validating spec '{spec.name}'…")
        spec = await self._validate_with_repair(spec)
        logger.info("  Validation passed")

        # Phase 3: LDD-Iteration
        logger.info("PHASE 3: LDD-ITERATION...")
        self._progress("ldd_iteration", 50, "Running LDD test loop…")
        spec = await self.tester.ldd_iterate(spec)
        logger.info(f"  LDD finished at k={spec.iteration_count}")

        # Phase 4: Adversarial Review
        logger.info("PHASE 4: ADVERSARIAL REVIEW...")
        self._progress("review", 75, "Adversarial review (3 dimensions)…")
        findings = await self.reviewer.review(spec)
        confirmed_count = sum(1 for f in findings if f.verdict == ReviewVerdict.CONFIRMED)
        plausible_count = sum(1 for f in findings if f.verdict == ReviewVerdict.PLAUSIBLE)

        converged = bool(getattr(self.tester, "converged", True))
        quality_score = score_quality(findings, converged=converged,
                                      dimensions=len(self.reviewer.reviewers))
        logger.info("  Review complete: quality=%.2f (%d confirmed, %d plausible, "
                    "converged=%s)", quality_score, confirmed_count,
                    plausible_count, converged)

        # Phase 5: Promotion
        logger.info("PHASE 5: PROMOTION...")
        self._progress("promotion", 90, f"Promoting '{spec.name}'…")
        artifact = self.promoter.promote(spec, quality_score)
        # Carry the findings out with the artifact. They used to be counted
        # into a number and then dropped, so an operator saw "Quality: 0%"
        # with no way to learn what the reviewers actually objected to.
        artifact.review_findings = findings
        logger.info(f"  Skill promoted: {artifact.spec.name}")

        logger.info(f"=== SKILL CREATION COMPLETE ===\n")
        return artifact
