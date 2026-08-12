"""Skill Learning System (ADR-0306+0307+0313+0314).

Three layers:
1. Skill Object (skill.py) — immutable metadata + mutable grades
2. Store Backend (store.py) — persistence (in-memory or file-based)
3. Learning Loop (learning_loop.py) — @skill_learnable decorator + async grading
"""

from .learning_loop import SkillLearningManager, skill_learnable
from .skill import Grade, Skill
from .store import FileSkillStore, InMemorySkillStore, SkillStore

__all__ = [
    "Skill",
    "Grade",
    "SkillStore",
    "InMemorySkillStore",
    "FileSkillStore",
    "skill_learnable",
    "SkillLearningManager",
]
