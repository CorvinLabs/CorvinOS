"""CLI tools for CorvinOS Brain management."""

import asyncio
import json
import logging
from pathlib import Path

from .config import BrainConfigLoader

logger = logging.getLogger(__name__)


class BrainCLI:
    """CLI interface for Brain management."""

    def __init__(self):
        self.brain = None

    async def cmd_status(self, args=None):
        """Show Brain status."""
        try:
            self.brain = BrainConfigLoader.load_brain()
        except Exception as e:
            print(f"❌ Cannot load Brain: {e}")
            return

        print("🧠 CorvinOS Brain Status\n")
        print(f"Subsystems registered: {len(self.brain.hub.subsystems)}")
        print(f"Poll interval: {self.brain.poll_interval_s}s\n")

        print("Subsystems:")
        for name, subsys in self.brain.hub.subsystems.items():
            print(f"  ✓ {name:20} v{subsys.version}")

    async def cmd_config_validate(self, args=None):
        """Validate Brain config."""
        config_path = "~/.corvin/brain-config.yaml"
        path = Path(config_path).expanduser()

        if not path.exists():
            print(f"❌ Config not found: {path}")
            return

        try:
            import yaml

            with open(path) as f:
                config = yaml.safe_load(f)

            # Validate structure
            assert "brain" in config, "Missing 'brain' key"
            assert "subsystems" in config["brain"], "Missing 'subsystems' key"

            # Validate subsystems
            subsystems = config["brain"]["subsystems"]
            for i, subsys in enumerate(subsystems):
                assert "name" in subsys, f"Subsystem {i} missing 'name'"

            print(f"✓ Config valid ({len(subsystems)} subsystems)")

        except Exception as e:
            print(f"❌ Config invalid: {e}")

    async def cmd_config_show(self, args=None):
        """Show current Brain config."""
        config_path = "~/.corvin/brain-config.yaml"
        path = Path(config_path).expanduser()

        if not path.exists():
            print(f"Config not found: {path}")
            return

        with open(path) as f:
            print(f.read())

    async def cmd_plugin_list(self, args=None):
        """List loaded plugins."""
        try:
            self.brain = BrainConfigLoader.load_brain()
        except Exception as e:
            print(f"Cannot load Brain: {e}")
            return

        print("🔌 Loaded Subsystems\n")
        for name, subsys in self.brain.hub.subsystems.items():
            print(f"  {name:20} v{subsys.version}")

    async def cmd_budget_status(self, args=None):
        """Show budget status."""
        try:
            self.brain = BrainConfigLoader.load_brain()
        except Exception as e:
            print(f"Cannot load Brain: {e}")
            return

        try:
            status = await self.brain.hub.request_from_subsystem(
                "cost_controller", "budget_status"
            )
            print("💰 Budget Status\n")
            print(f"  Daily budget: ${status['daily_budget']:.2f}")
            print(f"  Spent today: ${status['spent']:.2f}")
            print(f"  Remaining: ${status['remaining']:.2f}")
            print(f"  Used: {status['percent_used']:.1f}%")
        except Exception as e:
            print(f"Cost Controller not available: {e}")

    async def cmd_init_config(self, args=None):
        """Create default config."""
        config_path = Path("~/.corvin/brain-config.yaml").expanduser()
        config_path.parent.mkdir(parents=True, exist_ok=True)

        default_config = """# CorvinOS Brain Configuration
brain:
  poll_interval_s: 5
  max_event_queue_size: 10000

  subsystems:
    - name: health_monitor
      enabled: true
      params:
        stall_timeout_min: 10
        error_rate_threshold: 0.3

    - name: context_bridge
      enabled: true
      params:
        checkpoint_interval_turns: 25

    - name: loop_engineer
      enabled: true
      params:
        max_retries: 5

    - name: orchestrator
      enabled: true
      params:
        max_parallel_sessions: 3
"""

        with open(config_path, "w") as f:
            f.write(default_config)

        print(f"✓ Created default config at {config_path}")

    async def run(self, command, args=None):
        """Run a CLI command."""
        cmd_map = {
            "status": self.cmd_status,
            "config": {
                "validate": self.cmd_config_validate,
                "show": self.cmd_config_show,
                "init": self.cmd_init_config,
            },
            "plugin": {
                "list": self.cmd_plugin_list,
            },
            "budget": {
                "status": self.cmd_budget_status,
            },
        }

        if command in cmd_map:
            handler = cmd_map[command]
            if isinstance(handler, dict):
                print(f"Use: corvin-brain {command} <subcommand>")
                print(f"Subcommands: {', '.join(handler.keys())}")
            else:
                await handler(args)
        else:
            print(f"Unknown command: {command}")
            print("Commands: status, config, plugin, budget")


async def main():
    """CLI entry point."""
    import sys

    cli = BrainCLI()

    if len(sys.argv) < 2:
        print("Usage: corvin-brain <command> [args]")
        print("Commands:")
        print("  status               Show Brain status")
        print("  config validate      Validate config")
        print("  config show          Show current config")
        print("  config init          Create default config")
        print("  plugin list          List loaded plugins")
        print("  budget status        Show budget status")
        return

    command = sys.argv[1]
    args = sys.argv[2:] if len(sys.argv) > 2 else None

    if "." in command:
        # Handle nested commands like "config validate"
        parts = command.split(".")
        await cli.run(parts[0], {"subcommand": parts[1]})
    else:
        await cli.run(command, args)


if __name__ == "__main__":
    asyncio.run(main())
