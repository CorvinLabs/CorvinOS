"""Skeleton Generator — Template-based skill generation (Phase 1)."""

from pathlib import Path
from datetime import datetime
from .manifest import SkillManifest, SkillType, SkillScope


SKELETON_TEMPLATES = {
    SkillType.LEARNED_EXPERIENCE: """# {title}

**Type:** Learned Experience
**Status:** DRAFT (generated {timestamp})

## Context

{description}

## The Pattern

[How-to section — describe the working method]

## When to Use / When NOT to Use

[Boundary section — when this pattern applies and when it doesn't]

## Examples

[Concrete examples and case studies]

## Related

[Cross-references to other skills, ADRs, or concepts]
""",

    SkillType.REASONING: """# {title}

**Type:** Reasoning Skill
**Status:** DRAFT (generated {timestamp})

## Context

{description}

## The Thesis

[Initial proposal or framing]

## The Antithesis

[Counter-arguments and challenges]

## The Synthesis

[Reconciled position]

## How to Apply

[Step-by-step application]

## Examples

[Real-world scenarios]
""",

    SkillType.REFERENCE: """# {title}

**Type:** Reference
**Status:** DRAFT (generated {timestamp})

## Overview

{description}

## API / Interface

| Field | Type | Required | Description |
|---|---|---|---|
| | | | |

## Examples

```
[Example usage or code]
```

## See Also

[Related resources]
""",

    SkillType.AUTOMATION: """# {title}

**Type:** Automation Skill
**Status:** DRAFT (generated {timestamp})

## Context

{description}

## Algorithm

[High-level algorithm or decision tree]

## Input Contract

[What this skill expects as input]

## Output Contract

[What this skill produces]

## Error Handling

[Failure modes and recovery]

## Examples

[Concrete examples]
""",
}


class SkeletonGenerator:
    """Generate skill skeleton from manifest spec."""

    def __init__(self, skill_type: SkillType = SkillType.LEARNED_EXPERIENCE):
        self.skill_type = skill_type
        self.timestamp = datetime.utcnow().isoformat() + "Z"

    def generate(self, name: str, title: str, description: str,
                 scope: SkillScope = SkillScope.TASK) -> SkillManifest:
        """Generate skeleton manifest."""

        template = SKELETON_TEMPLATES.get(self.skill_type, SKELETON_TEMPLATES[SkillType.LEARNED_EXPERIENCE])

        body_md = template.format(
            title=title,
            description=description,
            timestamp=self.timestamp,
        )

        return SkillManifest(
            name=name,
            skill_type=self.skill_type,
            title=title,
            description=description,
            scope=scope,
            body_md=body_md,
            version="1.0.0",
            tags=[self.skill_type.value],
            generated_at=self.timestamp,
            generator_phase="skeleton",
        )


def generate_folder_structure(root: Path, skill_name: str) -> Path:
    """Generate standard folder structure for a skill."""

    skill_dir = root / skill_name
    skill_dir.mkdir(parents=True, exist_ok=True)

    # Create standard subdirectories
    (skill_dir / ".forge").mkdir(exist_ok=True)  # Metadata
    (skill_dir / "hooks").mkdir(exist_ok=True)   # Event hooks
    (skill_dir / "scripts").mkdir(exist_ok=True)  # Utility scripts
    (skill_dir / "tests").mkdir(exist_ok=True)   # Test suite

    # Write standard files
    (skill_dir / ".forge" / "manifest.json").write_text("{}")
    (skill_dir / ".forge" / "hooks.json").write_text('{"on_skill_loaded": []}')
    (skill_dir / "tests" / "__init__.py").write_text("")
    (skill_dir / "README.md").write_text(f"# {skill_name}\n\nSkill documentation.\n")

    return skill_dir
