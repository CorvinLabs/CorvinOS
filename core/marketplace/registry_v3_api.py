"""Marketplace Registry API v3"""

import json
from typing import List, Dict
from datetime import datetime


class RegistryV3:
    """Marketplace registry with Skills metadata"""

    def __init__(self):
        self.skills: List[Dict] = []

    def add_skill(self, skill_id: str, name: str, version: str, source_url: str):
        """Add skill to registry"""
        skill = {
            "id": skill_id,
            "name": name,
            "version": version,
            "source_url": source_url,
            "added_at": datetime.utcnow().isoformat(),
        }
        self.skills.append(skill)

    def list_skills(self) -> List[Dict]:
        """List all skills"""
        return self.skills

    def get_skill(self, skill_id: str) -> Dict | None:
        """Get skill by ID"""
        return next((s for s in self.skills if s["id"] == skill_id), None)

    def export_json(self) -> str:
        """Export registry as JSON"""
        return json.dumps({"skills": self.skills, "total": len(self.skills)})
