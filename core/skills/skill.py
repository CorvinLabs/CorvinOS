"""Skill Object — immutable metadata + mutable grades (ADR-0306)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class Grade:
    """A single skill grade: score + feedback + timestamp."""

    value: float  # 0.0–1.0
    feedback: str = ""
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def __post_init__(self):
        if not 0.0 <= self.value <= 1.0:
            raise ValueError(f"Grade value must be 0.0–1.0, got {self.value}")


@dataclass
class Skill:
    """Skill object: immutable identity + mutable learning history.

    A skill is a callable that can be graded over time. The object tracks:
    - name, version, body (immutable)
    - tags, tier (immutable metadata)
    - grades (mutable learning history)

    Computed properties (mean_score, last_updated) derive from grades.
    """

    name: str  # e.g. "code-review", "skill-forge-prompt"
    version: str  # e.g. "1.0", "2.1-rc1"
    body: str  # source code or serialized callable
    tags: list[str] = field(default_factory=list)  # e.g. ["code-review", "production"]
    tier: str = "bundled"  # bundled | installed | community (ADR-0156)
    grades: list[Grade] = field(default_factory=list)  # learning history

    def __post_init__(self):
        if not self.name or "/" in self.name:
            raise ValueError(f"Skill name invalid: {self.name!r}")
        if not self.version:
            raise ValueError("Skill version cannot be empty")
        if not self.body:
            raise ValueError("Skill body cannot be empty")

    @property
    def mean_score(self) -> float:
        """Average score across all grades, 0.0 if no grades yet."""
        if not self.grades:
            return 0.0
        return sum(g.value for g in self.grades) / len(self.grades)

    @property
    def n_trials(self) -> int:
        """Number of grades (trials/invocations)."""
        return len(self.grades)

    @property
    def last_updated(self) -> datetime | None:
        """Timestamp of most recent grade, None if no grades yet."""
        if not self.grades:
            return None
        return max(g.timestamp for g in self.grades)

    def add_grade(self, grade: Grade) -> None:
        """Add a grade to the learning history (appends, doesn't deduplicate)."""
        self.grades.append(grade)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to JSON-compatible dict."""
        return {
            "name": self.name,
            "version": self.version,
            "body": self.body,
            "tags": self.tags,
            "tier": self.tier,
            "grades": [
                {
                    "value": g.value,
                    "feedback": g.feedback,
                    "timestamp": g.timestamp.isoformat(),
                }
                for g in self.grades
            ],
            "mean_score": self.mean_score,
            "n_trials": self.n_trials,
            "last_updated": self.last_updated.isoformat() if self.last_updated else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Skill":
        """Deserialize from JSON dict (reconstructs grades, recomputes stats)."""
        grades = [
            Grade(
                value=g["value"],
                feedback=g.get("feedback", ""),
                timestamp=datetime.fromisoformat(g["timestamp"]),
            )
            for g in data.get("grades", [])
        ]
        skill = cls(
            name=data["name"],
            version=data["version"],
            body=data["body"],
            tags=data.get("tags", []),
            tier=data.get("tier", "bundled"),
            grades=grades,
        )
        return skill

    def __eq__(self, other: object) -> bool:
        """Two skills equal if name+version+body match (grades ignored)."""
        if not isinstance(other, Skill):
            return NotImplemented
        return (
            self.name == other.name
            and self.version == other.version
            and self.body == other.body
        )

    def __hash__(self) -> int:
        return hash((self.name, self.version, self.body))
