"""E2E tests for Director Mode Phases 1-2 (ADR-0696)"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from core.skills.director_mode import NarrativeOptimizer, VisualChoreographer


def test_director_mode_phase1_narrative_optimizer():
    """Phase 1: Narrative Optimizer preserves facts + requires approval."""
    storyboard = {
        "scenes": [
            {"factual_claim": "OpenAI released GPT-4 in March 2023", "source_asset": "research_doc.pdf"},
            {"factual_claim": "Claude Haiku is optimized for speed", "source_asset": "claude_spec.md"},
        ]
    }

    optimizer = NarrativeOptimizer()
    narrative = optimizer.optimize(storyboard)

    # Verify facts preserved
    assert len(narrative.facts_preserved) == 2
    assert "GPT-4" in narrative.facts_preserved[0]
    assert narrative.approved is False  # Must require approval

    # Approve narrative
    approved_narrative = optimizer.approve(narrative)
    assert approved_narrative.approved is True
    assert approved_narrative.approval_timestamp is not None

    print("✅ Phase 1: Narrative Optimizer — PASS")


def test_director_mode_phase2_visual_choreographer():
    """Phase 2: Visual + Pacing Choreographer allocates timing."""
    narrative = {
        "scenes": [
            {"act": "Setup", "title": "Introduction", "emotional_tone": "dramatic"},
            {"act": "Confrontation", "title": "Challenge", "emotional_tone": "dynamic"},
            {"act": "Resolution", "title": "Conclusion", "emotional_tone": "contemplative"},
        ],
        "facts_preserved": ["Fact 1", "Fact 2"]
    }

    choreographer = VisualChoreographer()
    scenes = choreographer.choreograph(narrative, total_duration=120.0)

    # Verify pacing
    assert len(scenes) == 3
    assert sum(s.duration_seconds for s in scenes) <= 120.5  # Within 1.0s rounding
    assert all(s.duration_seconds >= 1.0 for s in scenes)  # Minimum 1.0s per scene

    # Verify visual language assigned
    assert all(len(s.visual_approach) > 0 for s in scenes)

    print("✅ Phase 2: Visual + Pacing Choreographer — PASS")


def test_director_mode_phase1_2_e2e():
    """Full E2E: Phase 1 → Phase 2 pipeline."""
    storyboard = {
        "scenes": [
            {"factual_claim": "Claude was trained on data up to early 2024", "source_asset": "training_data.md"},
        ]
    }

    # Phase 1
    optimizer = NarrativeOptimizer()
    narrative = optimizer.optimize(storyboard)
    narrative = optimizer.approve(narrative)

    # Phase 2
    choreographer = VisualChoreographer()
    choreographed_scenes = choreographer.choreograph({
        "scenes": [{"act": "Main", "title": "Claude Overview", "emotional_tone": "dramatic"}],
        "facts_preserved": narrative.facts_preserved
    }, total_duration=60.0)

    # Verify end-to-end
    assert len(choreographed_scenes) == 1
    assert choreographed_scenes[0].duration_seconds <= 60.0

    print("✅ Phase 1-2 E2E: PASS (Narrative preserved through orchestration)")


if __name__ == "__main__":
    test_director_mode_phase1_narrative_optimizer()
    test_director_mode_phase2_visual_choreographer()
    test_director_mode_phase1_2_e2e()
    print("\n✅ ALL DIRECTOR MODE PHASE 1-2 TESTS PASS (3/3)")
