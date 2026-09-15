"""
Quality Skills Defaults Loader
Ensures all Quality LDDs + Skills enabled for new users on console load
(ADR-0262, ADR-0445)
"""

import yaml
from pathlib import Path
from typing import Dict, Any

def load_installation_defaults() -> Dict[str, Any]:
    """Load install-defaults.yaml — applied to all new users"""
    defaults_file = Path.home() / ".corvin" / "install-defaults.yaml"

    if not defaults_file.exists():
        # Fallback: all Quality skills enabled
        return {
            "quality": {"enabled": True, "modules": ["dod_verifier", "hallucination_detector"]},
            "console": {"quality_panel": {"enabled": True, "default_visible": True}},
            "ldd": {"enabled": True, "quality_ldd_enabled": True}
        }

    with open(defaults_file) as f:
        return yaml.safe_load(f)

def get_quality_skills_for_task(task_type: str, task_size: str) -> list:
    """
    Route Quality Skills to ALL task types + sizes
    Returns list of enabled skills for this task
    """
    defaults = load_installation_defaults()
    skills_config = defaults.get("skills", {})

    # All task types enabled by default
    task_skills = skills_config.get("task_types", {}).get(task_type, {}).get("skills", [])

    # All sizes enabled by default
    size_config = skills_config.get("task_sizes", {}).get(task_size, {})

    if not task_skills:
        # Fallback: default quality skills for all types
        return ["dod_verifier", "spec_convergence_optimizer"]

    return task_skills

def console_quality_enabled() -> bool:
    """Check if Quality panel should be visible in console"""
    defaults = load_installation_defaults()
    console_config = defaults.get("console", {})
    quality_panel = console_config.get("quality_panel", {})
    return quality_panel.get("enabled", True) and quality_panel.get("default_visible", True)

def quality_ldd_enabled() -> bool:
    """Check if Quality-LDD is enabled"""
    defaults = load_installation_defaults()
    ldd_config = defaults.get("ldd", {})
    return ldd_config.get("enabled", True) and ldd_config.get("quality_ldd_enabled", True)

# FastAPI integration
from fastapi import APIRouter
router = APIRouter(prefix="/v1/console/quality", tags=["quality"])

@router.get("/defaults")
async def get_quality_defaults():
    """GET /v1/console/quality/defaults — returns all Quality defaults for new users"""
    return load_installation_defaults()

@router.get("/check-setup")
async def check_quality_setup():
    """Health check: verify Quality skills configured for all task types"""
    defaults = load_installation_defaults()

    return {
        "status": "ok" if all([
            defaults.get("skills", {}).get("enabled_by_default"),
            defaults.get("console", {}).get("quality_panel", {}).get("enabled"),
            defaults.get("ldd", {}).get("quality_ldd_enabled")
        ]) else "degraded",

        "checks": {
            "skills_enabled": defaults.get("skills", {}).get("enabled_by_default", False),
            "console_quality_visible": console_quality_enabled(),
            "quality_ldd_enabled": quality_ldd_enabled(),
            "task_type_coverage": list(defaults.get("skills", {}).get("task_types", {}).keys()),
            "task_size_coverage": list(defaults.get("skills", {}).get("task_sizes", {}).keys()),
        }
    }
