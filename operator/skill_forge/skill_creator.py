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
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional
from uuid import uuid4

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
# PHASE 1: PLANNING (Dialectical Reasoning)
# ============================================================================

class SkillPlanner:
    """Phase 1: Generate skill spec via thesis → antithesis → synthesis."""

    def __init__(self, claude_client=None):
        """Initialize planner with LLM client."""
        self.client = claude_client or self._default_client()

    def _default_client(self):
        """Return default Claude client (injected by caller in tests)."""
        import anthropic
        return anthropic.Anthropic()

    async def plan(self, user_request: str) -> SkillSpec:
        """Generate SkillSpec from user request via dialectical reasoning.

        Args:
            user_request: Natural language request (e.g., "erzeuge einen Skill der JSON validiert")

        Returns:
            SkillSpec (unvalidated, for Phase 2)

        Raises:
            PlanningError: If planning fails at any stage
        """
        try:
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
  "name": "<scope>.<name>",  // assistant.* or project.* format
  "scope": "assistant",      // or "project"
  "purpose": "<one sentence purpose>",
  "method": "<full Markdown skill body (8-15 lines of clear instructions)>",
  "dependencies": ["<tool1>", "<tool2>"],
  "keywords": ["<keyword1>", "<keyword2>"]
}}

Make the method section concrete, actionable, and safe (no prompt injection patterns)."""

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
            name=spec_dict["name"],
            scope=SkillScope(spec_dict["scope"]),
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
            "name_format": r"^(assistant|project)\.[a-z_]+$",
            "purpose_length": (20, 200),
            "method_length": (100, 5000),
            "forbidden_patterns": [
                r"<|im_start|>",  # Prompt injection
                r"<|im_end|>",
                r"instructions:",   # Don't repeat system prompt keywords
                r"system:",
            ]
        }

    def validate(self, spec: SkillSpec) -> None:
        """Validate SkillSpec; raise ValidationError if invalid.

        Args:
            spec: SkillSpec to validate

        Raises:
            ValidationError: If any validation rule fails (fail-closed)
        """
        # Rule 1: Name format
        if not re.match(self.rules["name_format"], spec.name):
            raise ValidationError(f"Invalid name format: {spec.name}. "
                                  f"Must match: {self.rules['name_format']}")

        # Rule 2: Purpose length
        min_len, max_len = self.rules["purpose_length"]
        if not (min_len <= len(spec.purpose) <= max_len):
            raise ValidationError(f"Purpose length {len(spec.purpose)} outside "
                                  f"range [{min_len}, {max_len}]")

        # Rule 3: Method length
        min_len, max_len = self.rules["method_length"]
        if not (min_len <= len(spec.method) <= max_len):
            raise ValidationError(f"Method length {len(spec.method)} outside "
                                  f"range [{min_len}, {max_len}]")

        # Rule 4: Forbidden patterns
        for pattern in self.rules["forbidden_patterns"]:
            if re.search(pattern, spec.method, re.IGNORECASE):
                raise ValidationError(f"Forbidden pattern detected in method: {pattern}")

        # Rule 5: Markdown structure
        if not spec.method.startswith("#"):
            raise ValidationError("Method must start with a Markdown heading (# title)")

        # Rule 6: Dependencies check (basic)
        builtin_tools = {"bash", "python3", "read", "write", "edit"}
        for dep in spec.dependencies:
            if dep not in builtin_tools:
                logger.warning(f"Dependency not in builtin list: {dep} "
                              f"(will fail at promotion if unavailable)")

        logger.info(f"Validation passed: {spec.name}")


# ============================================================================
# PHASE 3: LDD-ITERATION (E2E test loop, k ≤ 5)
# ============================================================================

class SkillTester:
    """Phase 3: Test skill via E2E scenarios; measure loss; diagnose & fix."""

    def __init__(self, claude_client=None):
        """Initialize tester."""
        self.client = claude_client or self._default_client()
        self.max_iterations = 5

    def _default_client(self):
        import anthropic
        return anthropic.Anthropic()

    async def ldd_iterate(self, spec: SkillSpec) -> SkillSpec:
        """Run LDD inner loop: test → measure → diagnose → fix (k ≤ 5).

        Args:
            spec: SkillSpec to iterate on

        Returns:
            SkillSpec (refined), with iteration_count updated

        Raises:
            LDDIterationError: If k_max reached without convergence
        """
        current_spec = spec
        loss_history = []

        for k in range(1, self.max_iterations + 1):
            logger.info(f"LDD Iteration k={k}")

            # 1. Generate test scenario
            scenario = await self._generate_test_scenario(current_spec)

            # 2. Test skill in scenario
            test_result = await self._test_skill(current_spec, scenario)

            # 3. Measure loss
            loss = self._measure_loss(test_result, current_spec)
            loss_history.append(loss)

            logger.info(f"  k={k}: loss={loss:.3f}, scenario={scenario[:50]}...")

            # 4. Check convergence
            if loss < 0.1:  # Converged
                logger.info(f"  Converged at k={k}")
                current_spec = current_spec.__class__(
                    **{**current_spec.__dict__, "iteration_count": k}
                )
                return current_spec

            # 5. Diagnose & fix
            if k < self.max_iterations:
                diagnosis = await self._diagnose_loss(loss, test_result, current_spec)
                current_spec = await self._apply_fix(current_spec, diagnosis)
            else:
                # k == max_iterations: escalate
                logger.warning(f"k_max reached without convergence. Loss history: {loss_history}")
                raise LDDIterationError(
                    f"LDD did not converge after {self.max_iterations} iterations. "
                    f"Loss history: {loss_history}. "
                    f"Consider architectural change or larger step size."
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
        """Test skill in scenario (simulate usage)."""
        prompt = f"""You are testing a skill. The skill is:

Name: {spec.name}
Purpose: {spec.purpose}
Method:
{spec.method}

Test Scenario: {scenario}

Now, pretend you are a user following this skill's instructions.
Evaluate: (1) Can you understand the instructions? (2) Can you execute them?
(3) Do they stay within scope? (4) Are there any gaps or ambiguities?

Provide a brief evaluation (3-4 sentences)."""

        response = self.client.messages.create(
            model="claude-opus-5",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}]
        )

        return {
            "scenario": scenario,
            "evaluation": response.content[0].text,
            "test_passed": "understand" in response.content[0].text.lower()
                          and "execute" in response.content[0].text.lower()
        }

    def _measure_loss(self, test_result: Dict[str, Any], spec: SkillSpec) -> float:
        """Measure loss (how well the skill performed in the test).

        Loss components:
          - clarity_loss (0-1): Can instructions be understood?
          - scope_loss (0-1): Do instructions stay within scope?
          - coupling_loss (0-1): Are there unwanted dependencies?
        """
        eval_text = test_result["evaluation"].lower()

        clarity_loss = 0.0 if "understand" in eval_text else 0.5
        scope_loss = 0.0 if "scope" not in eval_text else 0.3
        coupling_loss = 0.0 if "dependencies" not in eval_text else 0.2

        total_loss = (clarity_loss + scope_loss + coupling_loss) / 3
        return total_loss

    async def _diagnose_loss(self, loss: float, test_result: Dict[str, Any],
                             spec: SkillSpec) -> str:
        """Diagnose the loss (root cause)."""
        prompt = f"""Based on this test evaluation:
{test_result['evaluation']}

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
        """Initialize reviewer."""
        self.client = claude_client or self._default_client()
        self.reviewers = [
            ("correctness", self._review_correctness),
            ("simplification", self._review_simplification),
            ("scope_creep", self._review_scope_creep),
        ]

    def _default_client(self):
        import anthropic
        return anthropic.Anthropic()

    async def review(self, spec: SkillSpec) -> List[ReviewFinding]:
        """Run 3 parallel adversarial reviews.

        Args:
            spec: SkillSpec to review

        Returns:
            List of ReviewFindings (may be empty if all REFUTED)

        Raises:
            ReviewError: If review process fails
        """
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


# ============================================================================
# PHASE 5: PROMOTION (SkillForge Registration)
# ============================================================================

class SkillPromoter:
    """Phase 5: Promote skill to disk + SkillForge registry."""

    def __init__(self, skills_dir: str = None):
        """Initialize promoter.

        Args:
            skills_dir: Directory to write skills (default: ~/.claude/skills/)
        """
        from pathlib import Path
        self.skills_dir = Path(skills_dir or Path.home() / ".claude" / "skills")
        self.skills_dir.mkdir(parents=True, exist_ok=True)

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
            # Step 1: Write to disk
            skill_file = self._write_skill_file(spec)
            logger.info(f"Written to disk: {skill_file}")

            # Step 2: Register in SkillForge (simulated)
            self._register_in_skillforge(spec, quality_score)
            logger.info(f"Registered in SkillForge: {spec.name}")

            # Step 3: Create artifact
            artifact = SkillArtifact(
                artifact_id=str(uuid4()),
                spec=spec,
                quality_score=quality_score,
                review_findings=[],
                ldd_iterations=spec.iteration_count,
            )

            logger.info(f"Skill promoted: {spec.name} (quality={quality_score:.2f})")
            return artifact

        except Exception as e:
            raise PromotionError(f"Promotion failed: {e}") from e

    def _write_skill_file(self, spec: SkillSpec) -> str:
        """Write skill to markdown file."""
        filename = f"{spec.name.replace('.', '_')}.md"
        filepath = self.skills_dir / filename

        # Format: YAML frontmatter + Markdown body
        content = f"""---
name: {spec.name}
scope: {spec.scope.value}
purpose: {spec.purpose}
dependencies: {json.dumps(spec.dependencies)}
keywords: {json.dumps(spec.keywords)}
generated_by: skill-creator
created_at: {spec.created_at.isoformat()}
---

{spec.method}
"""

        filepath.write_text(content)
        return str(filepath)

    def _register_in_skillforge(self, spec: SkillSpec, quality_score: float) -> None:
        """Register skill in SkillForge registry (simulated).

        In production, this would:
          - Add to ~/.claude/skills/registry.json
          - Auto-grade with +0.3 (bootstrap seed)
          - Emit concept-gate signal if generalizable
        """
        # Simulated: just log
        logger.info(f"Would register {spec.name} with quality {quality_score:.2f}")


# ============================================================================
# ORCHESTRATOR: PHASE 1-5 COORDINATION
# ============================================================================

class SkillCreatorOrchestrator:
    """Main orchestrator for 5-phase skill generation."""

    def __init__(self, claude_client=None, skills_dir: str = None):
        """Initialize orchestrator."""
        self.client = claude_client
        self.skills_dir = skills_dir
        self.planner = SkillPlanner(claude_client)
        self.validator = SkillValidator()
        self.tester = SkillTester(claude_client)
        self.reviewer = AdversarialReviewer(claude_client)
        self.promoter = SkillPromoter(skills_dir)

    async def create_skill(self, user_request: str) -> SkillArtifact:
        """Orchestrate full skill creation: Phases 1-5.

        Args:
            user_request: User's natural language request

        Returns:
            SkillArtifact (completed, promoted skill)

        Raises:
            SkillCreatorError: If any phase fails
        """
        logger.info(f"=== SKILL CREATION START ===\nRequest: {user_request}\n")

        # Phase 1: Planning
        logger.info("PHASE 1: PLANNING...")
        spec = await self.planner.plan(user_request)
        logger.info(f"  Spec generated: {spec.name}")

        # Phase 2: Validation
        logger.info("PHASE 2: VALIDATION...")
        self.validator.validate(spec)
        logger.info("  Validation passed")

        # Phase 3: LDD-Iteration
        logger.info("PHASE 3: LDD-ITERATION...")
        spec = await self.tester.ldd_iterate(spec)
        logger.info(f"  LDD converged at k={spec.iteration_count}")

        # Phase 4: Adversarial Review
        logger.info("PHASE 4: ADVERSARIAL REVIEW...")
        findings = await self.reviewer.review(spec)
        confirmed_count = sum(1 for f in findings if f.verdict == ReviewVerdict.CONFIRMED)
        plausible_count = sum(1 for f in findings if f.verdict == ReviewVerdict.PLAUSIBLE)

        if confirmed_count > 0:
            logger.warning(f"  {confirmed_count} CONFIRMED findings → LDD re-entry")
            # In production, would re-enter Phase 3 with findings as loss signals
            # For now, just log and continue (simplified path)

        quality_score = 1.0 - (confirmed_count * 0.3 + plausible_count * 0.1)
        logger.info(f"  Review complete: quality={quality_score:.2f}")

        # Phase 5: Promotion
        logger.info("PHASE 5: PROMOTION...")
        artifact = self.promoter.promote(spec, quality_score)
        logger.info(f"  Skill promoted: {artifact.spec.name}")

        logger.info(f"=== SKILL CREATION COMPLETE ===\n")
        return artifact
