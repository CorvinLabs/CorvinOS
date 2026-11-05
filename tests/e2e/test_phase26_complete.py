# ADR-0885 step 0: model_selection_skill_full.py (a 13-line stub with no
# production caller) was deleted; the real skill is the ADR-0642 integration.
#
# The VideoProducerSkill tests that lived here were removed with the stub they
# exercised (fbf783f40, "delete two VideoProducerSkill stubs with no caller");
# the video producer is the marketplace plugin ``media/video_producer``. The 17
# exec-generated ``test_skill_variant_*`` copies of the same ``is not None``
# call were removed with them: one call asserted 17 times is one test.
from core.skills.os_skills.model_selector_skill_integration import ModelSelectorSkill


def test_model_selection_routing():
    skill = ModelSelectorSkill(tenant_id="_default")
    decision = skill.execute(task_input="task1: coding, complex", task_type="coding")
    assert decision.recommended_model.startswith("claude-")
