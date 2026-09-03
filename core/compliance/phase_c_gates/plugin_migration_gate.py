"""Gate 4: Plugin Migration — ADR-0538 Phase C

Measures: >=95% of plugins migrated to ACP Skills
Pass Criteria: migration_rate >= 0.95
"""

from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class PluginMigrationResult:
    passed: bool
    total_plugins: int
    migrated_plugins: int
    migration_rate: float  # pct
    laggards: list  # [{plugin_id, owner, deadline}]
    evidence: dict


class PluginMigrationGate:
    """Gate 4: Verify >=95% plugins migrated to Skills."""

    def execute(self) -> PluginMigrationResult:
        """
        Run Gate 4: Plugin Migration

        Returns:
            PluginMigrationResult with migration rate + laggards
        """
        try:
            total, migrated, laggards = self._scan_plugin_registry()

            migration_rate = (migrated / total * 100) if total > 0 else 0.0
            passed = migration_rate >= 95.0

            return PluginMigrationResult(
                passed=passed,
                total_plugins=total,
                migrated_plugins=migrated,
                migration_rate=round(migration_rate, 1),
                laggards=laggards,
                evidence={
                    "pass_criteria": "migration_rate >= 95%",
                    "pass_threshold": 95,
                    "current_rate": round(migration_rate, 1),
                    "laggard_count": len(laggards),
                    "laggard_deadline": "week 7 EOD"
                }
            )

        except Exception as e:
            logger.error(f"Gate 4 failed: {e}")
            return PluginMigrationResult(
                passed=False,
                total_plugins=0,
                migrated_plugins=0,
                migration_rate=0.0,
                laggards=[],
                evidence={"error": str(e)}
            )

    def _scan_plugin_registry(self) -> tuple:
        """Scan plugin registry for migration status (REAL implementation)."""
        import subprocess
        import yaml
        import os

        laggards = []
        total_plugins = 0
        migrated_plugins = 0

        try:
            # Load plugin registry
            registry_path = "/home/shumway/projects/CorvinOS/core/plugins/registry.yaml"
            if not os.path.exists(registry_path):
                return 0, 0, []

            with open(registry_path, 'r') as f:
                registry = yaml.safe_load(f) or {}

            plugins = registry.get('plugins', [])
            total_plugins = len(plugins)

            # Check each plugin for old-subsystem imports in its manifest
            old_imports_pattern = "core.brain|core.vibe_engineering|core.context_engineering"

            for plugin in plugins:
                plugin_id = plugin.get('id', 'unknown')
                plugin_path = plugin.get('path', '')

                # Check plugin's source files for old imports
                cmd = f"""grep -r "{old_imports_pattern}" "{plugin_path}" --include="*.py" 2>/dev/null | wc -l"""
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=5)
                old_import_count = int(result.stdout.strip() or 0)

                if old_import_count == 0:
                    migrated_plugins += 1
                else:
                    laggards.append({
                        "plugin_id": plugin_id,
                        "owner": plugin.get('owner', 'unknown'),
                        "deadline": "2026-09-10",
                        "old_import_count": old_import_count
                    })

            return total_plugins, migrated_plugins, laggards

        except Exception as e:
            logger.error(f"Failed to scan plugin registry: {e}")
            # Fallback: assume no plugins scanned
            return 0, 0, []
