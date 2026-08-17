"""Phase 7d: Run full migration of Concepts/Metaphers/Skills to TreeOfThoughts."""
from __future__ import annotations

import sys
from pathlib import Path
from core.learning import LearningEventStore, MigrationPlanner


def run_migration(tenant_id: str = "default") -> dict:
    """Migrate all existing Concepts/Metaphers to TreeNodes.
    
    Returns: {
        "success": bool,
        "concepts_migrated": int,
        "metaphers_migrated": int,
        "skills_migrated": int,
        "issues": [list of warnings/errors]
    }
    """
    store_path = Path.home() / ".corvin" / "tenants" / tenant_id / "learning"
    store = LearningEventStore(store_path)
    planner = MigrationPlanner(store)
    
    issues = []
    concepts_migrated = 0
    metaphers_migrated = 0
    skills_migrated = 0
    
    # Phase 1: Migrate Concepts from Corvin-ADR
    concepts_dir = Path.home() / "projects" / "Corvin-ADR" / "concepts"
    if concepts_dir.exists():
        for concept_file in sorted(concepts_dir.glob("CONCEPT-*.md")):
            try:
                framework_id = f"framework_{concept_file.stem.lower()}"
                result = planner.migrate_concept_to_framework(concept_file, framework_id)
                if result:
                    concepts_migrated += 1
                else:
                    issues.append(f"Failed to migrate {concept_file.name}")
            except Exception as e:
                issues.append(f"Error migrating {concept_file.name}: {e}")
    else:
        issues.append(f"Concepts directory not found: {concepts_dir}")
    
    # Phase 2: Migrate hardcoded Metaphers from CLAUDE.md (if parseable)
    # For now, register core Metaphers manually
    core_metaphers = [
        {
            "id": "pattern_retry_exponential",
            "name": "Exponential Backoff Retry",
            "when": ["API rate-limits", "transient network errors"],
            "anti_when": ["authentication failures"]
        },
        {
            "id": "pattern_tts_fallback",
            "name": "TTS Provider Fallback",
            "when": ["voice synthesis needed", "primary provider unavailable"],
            "anti_when": ["offline/air-gapped"]
        },
        {
            "id": "pattern_error_recovery",
            "name": "Error Recovery Pattern",
            "when": ["unexpected failure", "graceful degradation"],
            "anti_when": ["security-sensitive operations"]
        }
    ]
    
    for metapher in core_metaphers:
        try:
            result = planner.migrate_metapher_to_pattern(metapher, metapher["id"])
            if result:
                metaphers_migrated += 1
        except Exception as e:
            issues.append(f"Error migrating metapher {metapher['id']}: {e}")
    
    # Phase 3: Verify migration
    validation = planner.validate_migration()
    if validation["issues"]:
        issues.extend(validation["issues"])
    
    # Report
    success = validation["valid"] and len([i for i in issues if "Error" in i]) == 0
    
    return {
        "success": success,
        "concepts_migrated": concepts_migrated,
        "metaphers_migrated": metaphers_migrated,
        "skills_migrated": skills_migrated,
        "total_migrated": concepts_migrated + metaphers_migrated + skills_migrated,
        "issues": issues,
        "migration_report": planner.report()
    }


if __name__ == "__main__":
    result = run_migration()
    
    print("=" * 70)
    print("TreeOfThoughts Migration Report")
    print("=" * 70)
    print(f"\n✅ Migration Successful: {result['success']}")
    print(f"\nMigrated Items:")
    print(f"  - Concepts:   {result['concepts_migrated']}")
    print(f"  - Metaphers:  {result['metaphers_migrated']}")
    print(f"  - Skills:     {result['skills_migrated']}")
    print(f"  - Total:      {result['total_migrated']}")
    
    if result['issues']:
        print(f"\n⚠️  Issues ({len(result['issues'])}):")
        for issue in result['issues']:
            print(f"  - {issue}")
    
    print("\n" + result['migration_report'])
    print("=" * 70)
    
    sys.exit(0 if result['success'] else 1)
