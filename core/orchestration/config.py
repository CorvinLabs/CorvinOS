"""Configuration loader for Brain subsystems.

ADR-0350: Configuration-Driven Plugin Loading
"""

import importlib
import importlib.util
import logging
from pathlib import Path
from typing import Dict, Any

import yaml

from .brain import TaskBrain

logger = logging.getLogger(__name__)


class BrainConfigLoader:
    """Load Brain configuration from YAML and instantiate subsystems."""

    # Builtin subsystems registry (Phase 1 + Phase 2)
    BUILTIN_SUBSYSTEMS = {
        # Phase 1: Core subsystems
        "health_monitor": "core.orchestration.subsystems.health_monitor:HealthMonitor",
        "context_bridge": "core.orchestration.subsystems.context_bridge:ContextBridge",
        "loop_engineer": "core.orchestration.subsystems.loop_engineer:LoopEngineer",
        "orchestrator": "core.orchestration.subsystems.orchestrator:Orchestrator",
        # Phase 2: Advanced subsystems
        "learning_engine": "core.orchestration.subsystems.learning_engine:LearningEngine",
        "cost_controller": "core.orchestration.subsystems.cost_controller:CostController",
        "safety_validator": "core.orchestration.subsystems.safety_validator:SafetyValidator",
        "strategy_advisor": "core.orchestration.subsystems.strategy_advisor:StrategyAdvisor",
    }

    @classmethod
    def load_brain(
        cls, config_path: str = "~/.corvin/brain-config.yaml"
    ) -> TaskBrain:
        """Load brain configuration and instantiate all subsystems."""
        config_path = Path(config_path).expanduser()

        if not config_path.exists():
            raise FileNotFoundError(f"Brain config not found: {config_path}")

        with open(config_path) as f:
            config = yaml.safe_load(f)

        if not config:
            raise ValueError("Empty brain config")

        brain_cfg = config.get("brain", {})
        brain = TaskBrain(
            poll_interval_s=brain_cfg.get("poll_interval_s", 5),
            max_event_queue_size=brain_cfg.get("max_event_queue_size", 10000),
        )

        # Load each subsystem
        for subsystem_config in brain_cfg.get("subsystems", []):
            name = subsystem_config.get("name")
            if not name:
                logger.warning("Subsystem missing 'name' key, skipping")
                continue

            enabled = subsystem_config.get("enabled", True)
            if not enabled:
                logger.info(f"Subsystem {name} disabled in config, skipping")
                continue

            try:
                subsystem = cls._instantiate_subsystem(name, subsystem_config)
                brain.register_subsystem(subsystem)
                logger.info(f"Registered subsystem: {name} v{subsystem.version}")
            except Exception as e:
                logger.error(f"Failed to register {name}: {e}")
                raise

        return brain

    @classmethod
    def _instantiate_subsystem(cls, name: str, config: Dict[str, Any]) -> Any:
        """Instantiate a subsystem from config."""
        # Builtin subsystem?
        if name in cls.BUILTIN_SUBSYSTEMS:
            module_path, class_name = cls.BUILTIN_SUBSYSTEMS[name].split(":")
            module = cls._import_module(module_path)
            subsystem_class = getattr(module, class_name)
            return subsystem_class(**config.get("params", {}))

        # Custom subsystem (user plugin)?
        if "path" in config and "class" in config:
            module = cls._import_module(config["path"])
            subsystem_class = getattr(module, config["class"])
            return subsystem_class(**config.get("params", {}))

        raise ValueError(f"Unknown subsystem: {name}")

    @staticmethod
    def _import_module(module_path: str):
        """Import module from path or module name."""
        if "/" in module_path or module_path.endswith(".py"):
            # File path: ~/.corvin/plugins/my_plugin.py
            spec = importlib.util.spec_from_file_location(
                "custom_module", Path(module_path).expanduser()
            )
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load module from {module_path}")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        else:
            # Module path: core.orchestration.subsystems.health_monitor
            return importlib.import_module(module_path)
