"""Generate meta.json for migrated skills."""

import json
from pathlib import Path
from datetime import datetime

def generate_skill_metadata(skill_dir: Path, skill_id: str) -> dict:
    """Generate meta.json for a skill (legacy or new)."""

    metadata = {
        "id": skill_id,
        "name": skill_id.replace("_", " ").title(),
        "version": "1.0.0",
        "scope": "_shared",
        "created": datetime.now().isoformat(),
        "last_modified": datetime.now().isoformat(),
        "tags": [],
        "dependencies": [],
        "compatibility": {
            "corvinOS_min": "0.3-rc1",
            "python_min": "3.11"
        },
        "metrics": {
            "auto_grade_score": 0,
            "usage_count": 0,
            "success_rate": 0.0,
            "last_used": None
        },
        "exported_to": [],
        "audit_trail": {
            "created_by": "migration",
            "created_session": "system:migration",
            "last_modified_by": "migration",
            "last_modified_session": "system:migration"
        }
    }

    # If skill_dir has tests, note it
    if (skill_dir / "tests").exists():
        metadata["has_tests"] = True

    return metadata

def write_metadata(skill_dir: Path, metadata: dict) -> bool:
    """Write meta.json to skill directory."""
    try:
        meta_path = skill_dir / "meta.json"
        with open(meta_path, "w") as f:
            json.dump(metadata, f, indent=2)
        return True
    except Exception as e:
        print(f"Failed to write meta.json: {e}")
        return False
