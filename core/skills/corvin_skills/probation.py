"""Probation class: bootstrap grades for new skills (ADR-0421).

New skills created by Skill-Creator, CEL, or package-manager start with n_grades=0,
blocking auto-promotion. ProbationSkill breaks this deadlock: new skills enter
probation (24h window), receive a bootstrap grade (capped at 0.3), then exit
when normal grades accrue or window expires.

Public API:
  - is_in_probation(skill_name: str, manifest_entry: dict) -> bool
  - apply_bootstrap_grade(score: float) -> float  (returns min(score, 0.3))
  - exit_probation(manifest_entry: dict) -> dict  (clears probation marker)
"""

from datetime import datetime, timedelta, timezone
from dataclasses import dataclass, field
from typing import Optional


@dataclass(frozen=True)
class ProbationSkill:
    """Immutable record of a skill in probation state."""
    skill_name: str
    created_at: datetime
    bootstrap_grade_at: Optional[datetime] = None
    bootstrap_score: Optional[float] = None  # Always <= 0.3


def is_in_probation(
    skill_name: str,
    manifest_entry: dict,
    now: datetime = None,
) -> bool:
    """Check if skill is in 24h probation window.

    Args:
        skill_name: Name of the skill (e.g., "assistant.validate_json")
        manifest_entry: Manifest entry dict with 'created_at' field
        now: Current time (defaults to now UTC); for testing

    Returns:
        True if skill is durable AND created within last 24h
    """
    if now is None:
        now = datetime.now(timezone.utc)

    lifecycle = manifest_entry.get("lifecycle", "durable")
    if lifecycle != "durable":
        return False  # Only durable skills enter probation

    created_at_str = manifest_entry.get("created_at")
    if not created_at_str:
        return False

    try:
        created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return False

    probation_expiry = created_at + timedelta(hours=24)
    return now < probation_expiry


def apply_bootstrap_grade(score: float) -> float:
    """Cap score to 0.3 for bootstrap seeding.

    Returns the minimum of score and 0.3. Ensures bootstrap grades
    don't accidentally trigger auto-promotion on their own.

    Args:
        score: Desired score (0.0–1.0)

    Returns:
        min(score, 0.3)
    """
    return min(max(score, 0.0), 0.3)


def should_apply_probation_cap(
    skill_name: str,
    manifest_entry: dict,
    grading_context: dict = None,
) -> bool:
    """Determine if a grade should be capped by probation.

    Probation cap applies if:
    1. Skill is in probation (created < 24h ago)
    2. Bootstrap grade hasn't been seeded yet (no bootstrap_score in manifest)

    Args:
        skill_name: Name of skill
        manifest_entry: Manifest entry dict
        grading_context: Optional context (for future extension)

    Returns:
        True if first grade should be capped at 0.3
    """
    if not is_in_probation(skill_name, manifest_entry):
        return False

    # If bootstrap score already exists, probation phase is active
    # but we don't cap subsequent grades
    bootstrap_score = manifest_entry.get("metadata", {}).get("bootstrap_score")
    if bootstrap_score is not None:
        return False

    return True


def exit_probation(manifest_entry: dict) -> dict:
    """Clear probation markers from manifest entry.

    Called when:
    - 24h window expires, OR
    - Skill receives a real grade (n_grades >= 2, past bootstrap)

    Args:
        manifest_entry: Manifest entry dict (modified in-place)

    Returns:
        Updated manifest_entry with probation markers cleared
    """
    metadata = manifest_entry.get("metadata", {})
    if "bootstrap_score" in metadata:
        del metadata["bootstrap_score"]
    if "bootstrap_grade_at" in metadata:
        del metadata["bootstrap_grade_at"]

    return manifest_entry
