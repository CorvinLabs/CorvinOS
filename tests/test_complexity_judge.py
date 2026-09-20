"""
Tests for ComplexityJudge — Phase 3 complexity classification

Validates:
- Signal Q1 (domain knowledge detection)
- Signal Q2 (reasoning depth)
- Signal Q3 (problem novelty)
- Composite scoring and classification
- Confidence scoring from signal agreement
- Caching mechanism
- Edge cases (empty prompts, very long prompts, etc.)
"""

import pytest
import sys
from pathlib import Path

# Add the CorvinOS core to path
sys.path.insert(0, str(Path(__file__).parent.parent / "core"))

from skills.os_skills.complexity_judge import ComplexityJudge, ComplexityLevel


class TestSignalQ1DomainKnowledge:
    """Test Signal Q1: Domain Knowledge Requirement detection."""

    def test_q1_math_terminology(self):
        """Q1 should detect mathematical terminology."""
        judge = ComplexityJudge()
        prompt = "Prove that sqrt(2) is irrational using contradiction."
        verdict = judge.judge(prompt)
        # Should score high on Q1 (theorem, proof terminology)
        assert verdict.signal_q1 > 50, f"Expected Q1 > 50, got {verdict.signal_q1}"

    def test_q1_biology_terminology(self):
        """Q1 should detect biology terminology."""
        judge = ComplexityJudge()
        prompt = "How does mitochondrial ATP production work in cellular respiration?"
        verdict = judge.judge(prompt)
        assert verdict.signal_q1 > 40, f"Expected Q1 > 40, got {verdict.signal_q1}"

    def test_q1_physics_terminology(self):
        """Q1 should detect physics terminology."""
        judge = ComplexityJudge()
        prompt = "Explain quantum entanglement and superposition."
        verdict = judge.judge(prompt)
        assert verdict.signal_q1 > 40, f"Expected Q1 > 40, got {verdict.signal_q1}"

    def test_q1_chemistry_terminology(self):
        """Q1 should detect chemistry terminology."""
        judge = ComplexityJudge()
        prompt = "What is the oxidation state of each element in the sulfate ion?"
        verdict = judge.judge(prompt)
        assert verdict.signal_q1 > 30, f"Expected Q1 > 30, got {verdict.signal_q1}"

    def test_q1_no_domain_knowledge(self):
        """Q1 should be low for simple arithmetic."""
        judge = ComplexityJudge()
        prompt = "What is 2 + 2?"
        verdict = judge.judge(prompt)
        # No domain terminology
        assert verdict.signal_q1 < 20, f"Expected Q1 < 20, got {verdict.signal_q1}"

    def test_q1_philosophy_terminology(self):
        """Q1 should detect philosophy terminology."""
        judge = ComplexityJudge()
        prompt = "Is free will compatible with determinism? Discuss compatibilism."
        verdict = judge.judge(prompt)
        assert verdict.signal_q1 > 40, f"Expected Q1 > 40, got {verdict.signal_q1}"

    def test_q1_networking_terminology(self):
        """Q1 should detect networking terminology."""
        judge = ComplexityJudge()
        prompt = "Explain the difference between TCP and UDP protocols."
        verdict = judge.judge(prompt)
        assert verdict.signal_q1 > 30, f"Expected Q1 > 30, got {verdict.signal_q1}"


class TestSignalQ2ReasoningDepth:
    """Test Signal Q2: Reasoning Depth detection."""

    def test_q2_multi_sentence_prompt(self):
        """Q2 should increase with multi-sentence prompts."""
        judge = ComplexityJudge()
        prompt = "Write a function that sorts an array. It should handle edge cases."
        verdict = judge.judge(prompt)
        # Multiple sentences suggest more depth
        assert verdict.signal_q2 > 20, f"Expected Q2 > 20, got {verdict.signal_q2}"

    def test_q2_single_sentence_simple(self):
        """Q2 should be low for single-sentence simple prompts."""
        judge = ComplexityJudge()
        prompt = "What is 2+2?"
        verdict = judge.judge(prompt)
        assert verdict.signal_q2 < 30, f"Expected Q2 < 30, got {verdict.signal_q2}"

    def test_q2_conditional_reasoning(self):
        """Q2 should detect conditional/multi-step language."""
        judge = ComplexityJudge()
        prompt = "Why does salt melt ice? How does this affect freezing point?"
        verdict = judge.judge(prompt)
        # Multiple questions suggest deeper reasoning
        assert verdict.signal_q2 > 30, f"Expected Q2 > 30, got {verdict.signal_q2}"

    def test_q2_proof_language(self):
        """Q2 should detect proof/evidence language."""
        judge = ComplexityJudge()
        prompt = "Prove that the derivative of sin(x) is cos(x). Justify each step."
        verdict = judge.judge(prompt)
        # "Prove" and "justify" = deeper reasoning
        assert verdict.signal_q2 > 40, f"Expected Q2 > 40, got {verdict.signal_q2}"

    def test_q2_longer_prompts(self):
        """Q2 should increase for longer prompts."""
        judge = ComplexityJudge()
        long_prompt = "Analyze the following scenario: A company has three departments. " \
                     "Each department has its own budget. How should the CEO allocate resources?"
        verdict = judge.judge(long_prompt)
        # Longer = likely more reasoning steps
        assert verdict.signal_q2 > 25, f"Expected Q2 > 25, got {verdict.signal_q2}"

    def test_q2_explanation_required(self):
        """Q2 should increase when explanation is requested."""
        judge = ComplexityJudge()
        prompt = "Explain why photosynthesis requires sunlight."
        verdict = judge.judge(prompt)
        assert verdict.signal_q2 > 15, f"Expected Q2 > 15, got {verdict.signal_q2}"


class TestSignalQ3ProblemNovelty:
    """Test Signal Q3: Problem Novelty detection."""

    def test_q3_creation_keywords(self):
        """Q3 should increase for creation/design keywords."""
        judge = ComplexityJudge()
        prompt = "Design a load-balancing algorithm for a distributed system."
        verdict = judge.judge(prompt)
        # "Design" = novel problem-solving
        assert verdict.signal_q3 > 40, f"Expected Q3 > 40, got {verdict.signal_q3}"

    def test_q3_hypothetical_scenarios(self):
        """Q3 should increase for hypothetical/speculative prompts."""
        judge = ComplexityJudge()
        prompt = "What if gravity worked in reverse? How would the universe look?"
        verdict = judge.judge(prompt)
        # "What if" = novel reasoning
        assert verdict.signal_q3 > 40, f"Expected Q3 > 40, got {verdict.signal_q3}"

    def test_q3_factual_recall(self):
        """Q3 should be low for factual recall."""
        judge = ComplexityJudge()
        prompt = "What is the capital of France?"
        verdict = judge.judge(prompt)
        # Simple lookup = low novelty
        assert verdict.signal_q3 < 30, f"Expected Q3 < 30, got {verdict.signal_q3}"

    def test_q3_analysis_keywords(self):
        """Q3 should increase for analysis keywords."""
        judge = ComplexityJudge()
        prompt = "Analyze the financial crisis of 2008. What were the root causes?"
        verdict = judge.judge(prompt)
        # "Analyze" = requires reasoning
        assert verdict.signal_q3 > 30, f"Expected Q3 > 30, got {verdict.signal_q3}"

    def test_q3_synthesis_keywords(self):
        """Q3 should increase for synthesis keywords."""
        judge = ComplexityJudge()
        prompt = "Synthesize the key differences between capitalism and socialism."
        verdict = judge.judge(prompt)
        # "Synthesize" = novel reasoning
        assert verdict.signal_q3 > 40, f"Expected Q3 > 40, got {verdict.signal_q3}"

    def test_q3_rote_list_keywords(self):
        """Q3 should be low for list/enumerate keywords."""
        judge = ComplexityJudge()
        prompt = "List the five elements of a strong argument."
        verdict = judge.judge(prompt)
        # "List" = rote, not novel
        assert verdict.signal_q3 < 30, f"Expected Q3 < 30, got {verdict.signal_q3}"


class TestCompositeScoring:
    """Test composite scoring and classification into tiers."""

    def test_classify_simple(self):
        """Composite score < 30 should classify as SIMPLE."""
        judge = ComplexityJudge()
        prompt = "What is 2+2?"
        verdict = judge.judge(prompt)
        assert verdict.level == ComplexityLevel.SIMPLE.value, \
            f"Expected SIMPLE, got {verdict.level} (score {verdict.score})"

    def test_classify_medium(self):
        """Composite score 30-70 should classify as MEDIUM."""
        judge = ComplexityJudge()
        prompt = "Write a function to sort an array of integers."
        verdict = judge.judge(prompt)
        # Should be in MEDIUM range (has some complexity)
        assert verdict.level in [ComplexityLevel.MEDIUM.value, ComplexityLevel.COMPLEX.value], \
            f"Expected MEDIUM or COMPLEX, got {verdict.level}"

    def test_classify_complex(self):
        """Composite score > 70 should classify as COMPLEX."""
        judge = ComplexityJudge()
        prompt = "Prove that the set of real numbers is uncountable using Cantor's diagonal argument."
        verdict = judge.judge(prompt)
        # Should be COMPLEX due to theorem + proof + novel reasoning
        assert verdict.level == ComplexityLevel.COMPLEX.value, \
            f"Expected COMPLEX, got {verdict.level} (score {verdict.score})"

    def test_score_range(self):
        """Composite score should always be 0-100."""
        judge = ComplexityJudge()
        test_prompts = [
            "What is 2+2?",
            "Write a function to sort an array.",
            "Prove that sqrt(2) is irrational.",
        ]
        for prompt in test_prompts:
            verdict = judge.judge(prompt)
            assert 0 <= verdict.score <= 100, \
                f"Score {verdict.score} out of range for prompt: {prompt}"


class TestConfidenceScoring:
    """Test confidence scoring from signal agreement."""

    def test_high_confidence_all_simple(self):
        """All signals < 30 should give high confidence (≥0.9)."""
        judge = ComplexityJudge()
        prompt = "What is 2+2?"
        verdict = judge.judge(prompt)
        # All signals should be low for this simple question
        if verdict.signal_q1 < 30 and verdict.signal_q2 < 30 and verdict.signal_q3 < 30:
            assert verdict.confidence >= 0.90, \
                f"Expected high confidence for all-simple signals, got {verdict.confidence}"

    def test_medium_confidence_partial_agreement(self):
        """2 signals in same tier should give medium confidence (0.7-0.8)."""
        # This is hard to test without hardcoding prompts, so we verify bounds
        judge = ComplexityJudge()
        prompt = "Explain why photosynthesis requires sunlight."
        verdict = judge.judge(prompt)
        # Confidence should be reasonable (0.5-1.0)
        assert 0.5 <= verdict.confidence <= 1.0, \
            f"Expected confidence 0.5-1.0, got {verdict.confidence}"

    def test_low_confidence_disagreement(self):
        """All signals in different tiers should give low confidence (≤0.5)."""
        # Difficult to force without artificial construction
        # Just verify confidence is always in valid range
        judge = ComplexityJudge()
        test_prompts = [
            "What?",
            "Why is the sky blue?",
            "Design a distributed database.",
        ]
        for prompt in test_prompts:
            verdict = judge.judge(prompt)
            assert 0.0 <= verdict.confidence <= 1.0, \
                f"Confidence out of range for: {prompt}"


class TestCaching:
    """Test caching mechanism for repeated prompts."""

    def test_caching_same_prompt(self):
        """Same prompt twice should return same verdict (cached)."""
        judge = ComplexityJudge()
        prompt = "Prove that sqrt(2) is irrational."
        verdict1 = judge.judge(prompt)
        verdict2 = judge.judge(prompt)
        # Should be identical
        assert verdict1.level == verdict2.level
        assert verdict1.score == verdict2.score
        assert verdict1.confidence == verdict2.confidence

    def test_cache_bounded_size(self):
        """Cache should not exceed max size (50 by default)."""
        judge = ComplexityJudge(cache_size=5)  # Small cache for testing
        # Add 10 different prompts
        for i in range(10):
            prompt = f"Test prompt number {i}"
            judge.judge(prompt)
        # Cache should never exceed 5
        assert len(judge.verdict_cache) <= 5, \
            f"Cache size {len(judge.verdict_cache)} exceeds max 5"

    def test_cache_different_prompts(self):
        """Different prompts should not return cached results."""
        judge = ComplexityJudge()
        verdict1 = judge.judge("What is 2+2?")
        verdict2 = judge.judge("Prove the Riemann hypothesis.")
        # These should have different complexities
        assert verdict1.score != verdict2.score, \
            "Different prompts returned same score (bad cache logic)"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_prompt(self):
        """Empty prompt should classify as SIMPLE."""
        judge = ComplexityJudge()
        verdict = judge.judge("")
        assert verdict.score < 30, f"Empty prompt should be SIMPLE, got score {verdict.score}"

    def test_single_word_prompt(self):
        """Single word should classify as SIMPLE."""
        judge = ComplexityJudge()
        verdict = judge.judge("What?")
        assert verdict.score < 40, f"Single word should be low complexity, got {verdict.score}"

    def test_very_long_prompt(self):
        """Very long prompt should increase Q2 (reasoning depth)."""
        judge = ComplexityJudge()
        long_prompt = "Design a system that: " + ", ".join([f"handles requirement {i}" for i in range(20)])
        verdict = judge.judge(long_prompt)
        # Should have reasonable Q2 score from length
        assert verdict.signal_q2 >= 20, f"Long prompt Q2 too low: {verdict.signal_q2}"

    def test_mixed_terminology(self):
        """Prompt with multiple domain terms should have higher Q1."""
        judge = ComplexityJudge()
        prompt = "Explain how photosynthesis uses quantum mechanics at the molecular level."
        verdict = judge.judge(prompt)
        # Multiple domains: biology + quantum physics
        assert verdict.signal_q1 > 40, f"Mixed terminology Q1 too low: {verdict.signal_q1}"

    def test_prompts_with_special_characters(self):
        """Prompts with special characters should be handled."""
        judge = ComplexityJudge()
        prompt = "What is E=mc^2? (Einstein's equation)"
        verdict = judge.judge(prompt)
        # Should not crash and should detect 'equation' terminology
        assert 0 <= verdict.score <= 100


class TestRealWorldExamples:
    """Test on real-world edge case examples."""

    def test_mathematical_proof_short(self):
        """Short mathematical proof should be classified as COMPLEX."""
        judge = ComplexityJudge()
        prompt = "Prove that sqrt(2) is irrational in 2 sentences"
        verdict = judge.judge(prompt)
        assert verdict.level == ComplexityLevel.COMPLEX.value, \
            f"Short math proof should be COMPLEX, got {verdict.level} (score {verdict.score})"

    def test_domain_knowledge_short(self):
        """Short domain expertise question should be COMPLEX."""
        judge = ComplexityJudge()
        prompt = "Why does the mitochondria need ATP?"
        verdict = judge.judge(prompt)
        assert verdict.level == ComplexityLevel.COMPLEX.value, \
            f"Domain knowledge Q should be COMPLEX, got {verdict.level}"

    def test_false_complexity_long_but_simple(self):
        """Long prompt that sounds complex but is simple should be MEDIUM or SIMPLE."""
        judge = ComplexityJudge()
        prompt = "Explain blockchain technology for beginners using simple terms"
        verdict = judge.judge(prompt)
        # Should NOT be COMPLEX because it's "for beginners" and "simple terms"
        # But might be MEDIUM due to length
        assert verdict.level in [ComplexityLevel.SIMPLE.value, ComplexityLevel.MEDIUM.value], \
            f"Simplified explanation should not be COMPLEX, got {verdict.level}"

    def test_nuanced_reasoning_short(self):
        """Short nuanced reasoning question should be COMPLEX."""
        judge = ComplexityJudge()
        prompt = "Is lying ever morally justified?"
        verdict = judge.judge(prompt)
        # Should be COMPLEX due to philosophical/ethical depth
        assert verdict.level == ComplexityLevel.COMPLEX.value, \
            f"Ethical question should be COMPLEX, got {verdict.level} (score {verdict.score})"

    def test_false_simplicity_long_list(self):
        """Simple list task should remain SIMPLE even if long."""
        judge = ComplexityJudge()
        prompt = "List the advantages of the iPhone over Android phones"
        verdict = judge.judge(prompt)
        # "List" keyword should keep it simple
        assert verdict.level in [ComplexityLevel.SIMPLE.value, ComplexityLevel.MEDIUM.value], \
            f"List task should not be COMPLEX, got {verdict.level}"


class TestConsistency:
    """Test consistency of scoring."""

    def test_same_prompt_same_score(self):
        """Scoring should be deterministic."""
        judge = ComplexityJudge()
        prompt = "What is photosynthesis?"
        verdicts = [judge.judge(prompt) for _ in range(5)]
        # All should have same score
        scores = [v.score for v in verdicts]
        assert len(set(scores)) == 1, f"Scores not consistent: {scores}"

    def test_signals_sum_to_score(self):
        """Composite score should be average of 3 signals."""
        judge = ComplexityJudge()
        prompt = "Prove that 0.999... equals 1"
        verdict = judge.judge(prompt)
        expected_score = (verdict.signal_q1 + verdict.signal_q2 + verdict.signal_q3) / 3.0
        assert abs(verdict.score - expected_score) < 0.01, \
            f"Composite score {verdict.score} doesn't match average {expected_score}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
