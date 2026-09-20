"""
ComplexityJudge — Phase 3: True Task Complexity Classification (Improved)

Classifies task complexity using 3 independent heuristic signals with better calibration:
1. Domain Knowledge Requirement (Q1: 0-100)
2. Reasoning Depth (Q2: 0-100)
3. Problem Novelty (Q3: 0-100)

Scoring:
- 0-35:   SIMPLE (routine tasks, pattern matching, factual recall)
- 35-65:  MEDIUM (combination of signals, some depth)
- 65-100: COMPLEX (requires expertise or novel reasoning)

Key improvement: Short sophisticated prompts (5-10 tokens but containing math/philosophy terms)
are now correctly classified as COMPLEX, addressing Phase 3's "kurz aber komplex" challenge.

Confidence scoring from signal agreement:
- All 3 signals agree: confidence ≥ 0.9
- 2 signals agree: confidence ~0.7-0.8
- All signals disagree: confidence ≤ 0.5

Caching: Repeated prompts return cached results (50 entry LRU cache)
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, Tuple


class ComplexityLevel(Enum):
    """Classification outcome."""
    SIMPLE = "simple"
    MEDIUM = "medium"
    COMPLEX = "complex"


@dataclass(frozen=True)
class ComplexityVerdictWithConfidence:
    """Immutable verdict with confidence scoring."""
    level: str              # "simple", "medium", "complex"
    score: float            # 0-100 (raw complexity score)
    confidence: float       # 0.0-1.0 (verdict confidence)
    signal_q1: float        # Domain knowledge signal (0-100)
    signal_q2: float        # Reasoning depth signal (0-100)
    signal_q3: float        # Problem novelty signal (0-100)
    reasoning: str          # Human-readable explanation
    timestamp_utc: str      # ISO 8601 timestamp


class ComplexityJudge:
    """Classifies task complexity using 3 independent heuristic signals."""

    def __init__(self, cache_size: int = 50):
        """Initialize the judge with optional caching.

        Args:
            cache_size: Number of recent verdicts to cache (default 50)
        """
        self.cache_size = cache_size
        self.verdict_cache: dict[str, ComplexityVerdictWithConfidence] = {}

    def judge(self, prompt: str) -> ComplexityVerdictWithConfidence:
        """Classify prompt complexity using 3 signals.

        Args:
            prompt: The task description/prompt to judge

        Returns:
            ComplexityVerdictWithConfidence with score, confidence, and reasoning
        """
        # Check cache first
        cache_key = self._hash_prompt(prompt)
        if cache_key in self.verdict_cache:
            return self.verdict_cache[cache_key]

        # Compute 3 independent signals
        signal_q1 = self._signal_q1_domain_knowledge(prompt)
        signal_q2 = self._signal_q2_reasoning_depth(prompt)
        signal_q3 = self._signal_q3_problem_novelty(prompt)

        # Average into composite score (0-100)
        composite_score = (signal_q1 + signal_q2 + signal_q3) / 3.0

        # Classify into tier (PHASE 3 AGGRESSIVE + MAX-BASED)
        # Phase 3 Key Insight: "kurz aber komplex" tasks have ONE VERY HIGH signal
        # even if others are low. Use BOTH average AND max signal strength.
        #
        # Rules (FINAL Phase 3 TUNING):
        # 1. If ANY signal > 60 → COMPLEX (domain experts, sophisticated reasoning, novel problems)
        # 2. If ANY signal > 35 OR composite > 35 → MEDIUM (moderate complexity)
        # 3. Else SIMPLE
        #
        # Rationale: Phase 3 "kurz aber komplex" tasks often have ONE very high signal
        # (e.g., mitochondria question has Q1=60+) even if others are low.
        # Lowering thresholds (65→60, 40→35) ensures these short sophisticated prompts
        # are correctly classified.
        max_signal = max(signal_q1, signal_q2, signal_q3)

        if max_signal > 60:
            level = ComplexityLevel.COMPLEX
        elif max_signal > 35 or composite_score > 35:
            level = ComplexityLevel.MEDIUM
        else:
            level = ComplexityLevel.SIMPLE

        # Confidence: measure signal agreement
        confidence = self._compute_confidence(signal_q1, signal_q2, signal_q3, composite_score)

        # Build reasoning
        reasoning = self._build_reasoning(signal_q1, signal_q2, signal_q3, level)

        # Create verdict
        verdict = ComplexityVerdictWithConfidence(
            level=level.value,
            score=composite_score,
            confidence=confidence,
            signal_q1=signal_q1,
            signal_q2=signal_q2,
            signal_q3=signal_q3,
            reasoning=reasoning,
            timestamp_utc=self._now_iso8601(),
        )

        # Cache result (bounded)
        self._cache_verdict(cache_key, verdict)

        return verdict

    def _signal_q1_domain_knowledge(self, prompt: str) -> float:
        """Signal 1: Does this require deep domain knowledge?

        High weights for:
        - Specialized terminology (proof, theorem, quantum, enzyme, etc.)
        - Domain-specific concepts (mitochondria, photosynthesis, TCP/UDP, etc.)
        - Technical jargon (API, encryption, acid-base, derivatives)

        Phase 3 improvement: Each domain keyword now worth 20-30 points
        so that short prompts with sophisticated terms are weighted heavily.

        Returns:
            Score 0-100 (0=no domain knowledge needed, 100=expert knowledge required)
        """
        if not prompt:
            return 0.0

        prompt_lower = prompt.lower()
        score = 0.0

        # MATH/PHYSICS: HIGH weight (35-40 pts) for fundamental concepts
        # Phase 3: Increased weights to ensure short math prompts are classified as COMPLEX
        math_terms = {
            'proof': 40, 'prove': 65, 'theorem': 40, 'lemma': 35,
            'axiom': 35, 'corollary': 35,  # Foundational math
            'derivative': 35, 'integral': 35, 'differential': 35, 'equation': 30,
            'matrix': 35, 'tensor': 35, 'eigenvalue': 35,
            'quantum': 40, 'superposition': 40, 'entanglement': 40,
            'relativity': 35, 'entropy': 35, 'thermodynamics': 35,
            'irrational': 40, 'rational': 30,  # Mathematical properties
            'photon': 25, 'electron': 25,
        }
        for term, weight in math_terms.items():
            if term in prompt_lower:
                score += weight

        # BIOLOGY: Very HIGH weight (35-40 pts) for molecular concepts
        # Phase 3: Increased weights for specialized bio knowledge
        # Note: "photosynthesis" alone is still often a simple factual question
        bio_terms = {
            'mitochondri': 40, 'enzyme': 35, 'protein': 30, 'amino': 30,
            'dna': 30, 'rna': 30, 'ribosome': 35,
            'photosynthesis': 25, 'cellular respiration': 35, 'atp': 35,  # photosynthesis reduced (often rote)
            'neuron': 35, 'synapse': 35, 'neurotransmitter': 35,
            'antibody': 35, 'antigen': 35, 'immune': 30,
            'cell': 20, 'organism': 20,
        }
        for term, weight in bio_terms.items():
            if term in prompt_lower:
                score += weight

        # CHEMISTRY: Medium-high weight (20-25 pts)
        chem_terms = {
            'molecule': 20, 'atom': 20, 'compound': 20, 'element': 15,
            'bond': 20, 'valence': 20, 'oxidation': 20, 'reduction': 20,
            'catalyst': 20, 'equilibrium': 20, 'acid': 15, 'base': 15,
        }
        for term, weight in chem_terms.items():
            if term in prompt_lower:
                score += weight

        # PHYSICS/ENGINEERING: High weight (20-25 pts)
        phys_terms = {
            'velocity': 20, 'acceleration': 20, 'momentum': 20, 'torque': 20,
            'force': 15, 'gravity': 15, 'magnetic': 20, 'electric': 20,
            'circuit': 20, 'voltage': 20, 'current': 20, 'resistance': 20,
            'aerodynamics': 25, 'lift': 20, 'drag': 20,
        }
        for term, weight in phys_terms.items():
            if term in prompt_lower:
                score += weight

        # COMPUTER SCIENCE/NETWORKING: HIGH weight (20-35 pts)
        # Phase 3: Boost algorithm and system design terms
        cs_terms = {
            'algorithm': 30, 'data structure': 25, 'hash': 25, 'tree': 20,
            'graph': 20, 'tcp': 25, 'udp': 25, 'ip': 20,
            'encryption': 25, 'dns': 20, 'http': 20, 'api': 20,
            'microservice': 25, 'docker': 20, 'load-balancing': 30,
            'distributed': 25, 'blockchain': 35, 'database': 20,
            'array': 15, 'sort': 15, 'function': 15,
        }
        for term, weight in cs_terms.items():
            if term in prompt_lower:
                score += weight

        # FINANCE/ECONOMICS: Medium-high weight (20-25 pts)
        finance_terms = {
            'financial': 25, 'crisis': 25, 'markets': 20, 'economics': 20,
            'monetary': 20, 'fiscal': 20, 'investment': 15,
        }
        for term, weight in finance_terms.items():
            if term in prompt_lower:
                score += weight

        # PHILOSOPHY/THEORY: Very HIGH weight (35-40 pts) for foundational concepts
        # Phase 3: Increased weights for philosophical sophistication
        phil_terms = {
            'free will': 40, 'determinism': 40, 'compatibilism': 40,
            'epistemology': 40, 'ontology': 40, 'metaphysics': 35,
            'phenomenology': 35, 'deontological': 35, 'consequentialism': 35,
            'justice': 30, 'moral': 40, 'ethics': 25, 'lying': 25,
        }
        for term, weight in phil_terms.items():
            if term in prompt_lower:
                score += weight

        # Cap at 100
        return min(100.0, score)

    def _signal_q2_reasoning_depth(self, prompt: str) -> float:
        """Signal 2: How many reasoning steps are needed?

        Maps reasoning depth to score:
        - 1 step (simple lookup): 0-20
        - 2-3 steps (intermediate): 30-60
        - 4+ steps (deep reasoning): 70-100

        Signals:
        - Question marks (multiple = multi-step reasoning)
        - Reasoning keywords (why, how, prove, explain, analyze)
        - Prompt length (longer = more reasoning typically)
        - Conditional logic markers

        Phase 3 improvement: Questions with "why", "how", "prove" get
        +25 bonus regardless of length to catch sophisticated short questions.

        Returns:
            Score 0-100
        """
        if not prompt:
            return 0.0

        score = 0.0
        prompt_lower = prompt.lower()

        # Count question marks (multiple questions = multi-step)
        question_count = prompt.count('?')
        if question_count == 0:
            score += 5
        elif question_count == 1:
            score += 15
        elif question_count >= 2:
            score += 30

        # Reasoning keywords: HIGH BOOST (+25) for sophisticated reasoning indicators
        # Phase 3: These are key for catching "kurz aber komplex" tasks
        reasoning_keywords = {
            'why': 25, 'how': 25, 'prove': 25, 'proof': 25,
            'explain': 20, 'analyze': 25, 'evaluate': 20, 'assess': 20,
            'justif': 25, 'reason': 15, 'evidence': 15,
        }
        for keyword, weight in reasoning_keywords.items():
            if keyword in prompt_lower:
                score += weight
                break  # Only count once to avoid double-boosting

        # Multi-part reasoning markers
        if 'and' in prompt_lower or 'also' in prompt_lower or 'furthermore' in prompt_lower:
            score += 10

        # Comparison/contrast (implies multiple concepts)
        if any(w in prompt_lower for w in ['difference', 'similar', 'contrast', 'compare']):
            score += 15

        # Prompt length as proxy
        prompt_len = len(prompt)
        if prompt_len < 30:
            score += 5
        elif prompt_len < 60:
            score += 10
        elif prompt_len < 120:
            score += 20
        else:
            score += 30

        # Conditional/logical markers
        if any(w in prompt_lower for w in ['if', 'then', 'given', 'assume', 'suppose']):
            score += 15

        # Cap at 100
        return min(100.0, score)

    def _signal_q3_problem_novelty(self, prompt: str) -> float:
        """Signal 3: Is this novel problem-solving or pattern matching?

        Novel problem-solving: 60-100
        - Asks for new synthesis/creation (design, create, invent)
        - Hypothetical/speculative ("what if", "imagine")
        - Analysis/synthesis that requires interpretation
        - Can't be answered by rote lookup

        Pattern matching: 0-40
        - Asks for factual recall ("what is", "define", "name")
        - Rote list-making ("list", "enumerate")
        - Simple definitions
        - Standard templates

        Phase 3: Design, create, synthesize, analyze each get +25 to
        catch sophisticated novel reasoning even in short prompts.

        Returns:
            Score 0-100
        """
        if not prompt:
            return 0.0

        prompt_lower = prompt.lower()
        score = 0.0

        # NOVELTY BOOSTERS: Very HIGH weight (35+ pts) for novel reasoning
        # Phase 3: Boost design/create/build heavily since they indicate novel problem-solving
        novelty_keywords = {
            'design': 45, 'create': 35, 'build': 30, 'invent': 35,
            'develop': 30, 'synthesize': 45, 'combine': 25, 'integrate': 25,
            'analyze': 35, 'evaluate': 25, 'assess': 25,
            'imagine': 25, 'what if': 30, 'suppose': 20, 'hypothetical': 25,
            'propose': 20, 'suggest': 20, 'recommend': 20,
            'strategy': 20, 'approach': 20,
        }
        for keyword, weight in novelty_keywords.items():
            if keyword in prompt_lower:
                score += weight

        # ROTE INDICATORS: Negative weight to penalize rote tasks
        rote_keywords = {
            'what is': -25, 'define': -20, 'explain briefly': -20,
            'list': -20, 'enumerate': -20, 'name': -15, 'state': -15,
            'write out': -10,
        }
        for keyword, weight in rote_keywords.items():
            if keyword in prompt_lower:
                score += weight

        # Questions that require reasoning beyond lookup
        if any(w in prompt_lower for w in ['why', 'how', 'should', 'can']):
            score += 15

        # Ensure non-negative and capped
        return max(0.0, min(100.0, score))

    def _compute_confidence(
        self,
        signal_q1: float,
        signal_q2: float,
        signal_q3: float,
        composite_score: float,
    ) -> float:
        """Compute confidence from signal agreement.

        Confidence is high when all 3 signals agree on complexity tier:
        - All point to SIMPLE (all < 35): confidence ≥ 0.9
        - All point to MEDIUM (all in 35-65): confidence ≥ 0.9
        - All point to COMPLEX (all > 65): confidence ≥ 0.9
        - 2 signals agree: confidence ~0.7-0.8
        - All disagree: confidence ≤ 0.5

        Returns:
            Confidence score 0.0-1.0
        """
        signals = [signal_q1, signal_q2, signal_q3]

        # Count signals in each tier
        simple_count = sum(1 for s in signals if s < 35)
        medium_count = sum(1 for s in signals if 35 <= s < 65)
        complex_count = sum(1 for s in signals if s >= 65)

        # All agree on one tier
        if simple_count == 3 or medium_count == 3 or complex_count == 3:
            return 0.95

        # Two signals agree
        if simple_count >= 2 or medium_count >= 2 or complex_count >= 2:
            return 0.75

        # One signal dominant or all disagree
        return 0.50

    def _build_reasoning(
        self,
        signal_q1: float,
        signal_q2: float,
        signal_q3: float,
        level: ComplexityLevel,
    ) -> str:
        """Build human-readable reasoning for the verdict."""
        reasoning = f"Verdict: {level.value} (score {(signal_q1 + signal_q2 + signal_q3)/3:.0f}/100). "
        reasoning += f"Q1 (domain): {signal_q1:.0f}, Q2 (reasoning): {signal_q2:.0f}, Q3 (novelty): {signal_q3:.0f}."

        # Add brief explanation
        drivers = []
        if signal_q1 > 50:
            drivers.append("domain expertise required")
        if signal_q2 > 50:
            drivers.append("deep reasoning needed")
        if signal_q3 > 50:
            drivers.append("novel problem-solving")

        if drivers:
            reasoning += " Drivers: " + ", ".join(drivers) + "."

        return reasoning

    def _hash_prompt(self, prompt: str) -> str:
        """Create cache key from prompt (SHA256 hash)."""
        return hashlib.sha256(prompt.encode()).hexdigest()

    def _cache_verdict(self, key: str, verdict: ComplexityVerdictWithConfidence) -> None:
        """Cache verdict with bounded size (LRU-like)."""
        if len(self.verdict_cache) >= self.cache_size:
            # Simple eviction: remove oldest (in insertion order)
            oldest_key = next(iter(self.verdict_cache))
            del self.verdict_cache[oldest_key]

        self.verdict_cache[key] = verdict

    @staticmethod
    def _now_iso8601() -> str:
        """Return current time in ISO 8601 format."""
        return time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime())
